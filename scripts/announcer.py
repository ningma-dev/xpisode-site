#!/usr/bin/env python3
"""Say out loud what an operator must not miss: what keeps the station from recording, a
recording cut short, and what quickcheck found in the last Episode.

    .venv/bin/python scripts/announcer.py                 this machine's station (XPISODE_STATION_FILE) or the project's default
    .venv/bin/python scripts/announcer.py --station yam_3 --every 8

The station starts it (a ``[[clients]]`` entry of the station file): it joins the station as a
client with no control groups, so the station sees it running and does not start a second
one. It reads the station's status (what the panel reads) and says, in Chinese:

- each change of ``status.blocking``, compared by ``(kind, subject)``: "注意，<设备>掉线" when
  one appears, again every --every seconds while it stays, "<设备>已恢复" when it clears;
  ``starting`` and ``sealing`` pass by themselves and are not said, nor a device that is not
  ``fault`` or ``lost`` (starting, closing) or a disk not measured yet. A station without
  ``blocking`` in its status is read by its device states instead;
- "录制已中止" when a recording ends while something blocks;
- "站点已断开" when the station stops answering while it records;
- from quickcheck's alerts (``<data root>/local/quickcheck/alerts.jsonl``) every ``fail``, and
  once per Episode a missed return to ready or too few gripper cycles:
  "上一条：没有回到准备位".

xpisode only records these facts; telling people is this program's business (xpisode design/design.md §9). Speech
goes through spd-say (speech-dispatcher) in Chinese, else espeak-ng, else the terminal. Names
read better through --names, a TOML table of device name to spoken name.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DOWN = ("fault", "lost")
NAMES = {
    "follower_l": "左从臂",
    "follower_r": "右从臂",
    "leader_l": "左主臂",
    "leader_r": "右主臂",
    "cam_head": "头部相机",
    "cam_left_wrist": "左腕相机",
    "cam_right_wrist": "右腕相机",
}
QUIET = ("starting", "sealing")
"""Blocking kinds that pass by themselves."""
ONCE = ("operator",)
"""Blocking kinds said once, not repeated."""
POLL_S = 0.1
"""The station drops a client it has not heard from for 1 s (env-protocol.md 4)."""

Key = tuple[str, str]


def spoken_name(names: Mapping[str, str], subject: str) -> str:
    device = subject.strip("/").split("/")[0] if subject else ""
    return names.get(device, device.replace("_", " "))


# What changed


def blocking_key(item: Mapping[str, Any]) -> Key:
    return str(item.get("kind", "")), str(item.get("subject", ""))


def blocking_changes(
    before: Mapping[Key, Mapping[str, Any]], after: Iterable[Mapping[str, Any]]
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[Key, Mapping[str, Any]]]:
    """What appeared and what cleared between two ``status.blocking`` lists, compared by
    ``(kind, subject)``; and the new list by key."""
    now = {blocking_key(item): item for item in after}
    appeared = [item for key, item in now.items() if key not in before]
    cleared = [item for key, item in before.items() if key not in now]
    return appeared, cleared, now


def device_changes(
    before: Mapping[str, str], after: Mapping[str, str]
) -> tuple[list[str], list[str]]:
    """Devices that went down and that came back, from their states (stations whose status
    has no ``blocking``)."""
    down = [n for n, s in after.items() if s in DOWN and before.get(n) not in DOWN]
    back = [n for n, s in after.items() if s == "ready" and before.get(n) in DOWN]
    return down, back


def blocking_speech(
    item: Mapping[str, Any], appeared: bool, names: Mapping[str, str]
) -> str | None:
    """What to say when a blocking item appears or clears; None for what is not said: a device
    whose state is not one of DOWN (starting, closing, unknown), a disk not measured."""
    kind = str(item.get("kind", ""))
    name = spoken_name(names, str(item.get("subject", "")))
    if kind == "device":
        if item.get("state") not in DOWN:
            return None
        return f"注意，{name}掉线" if appeared else f"{name}已恢复"
    if kind == "stream":
        return f"注意，{name}没有数据" if appeared else f"{name}数据已恢复"
    if kind == "recorder":
        return "注意，录制出错" if appeared else "录制已恢复"
    if kind == "disk":
        if item.get("free_bytes") is None:
            return None
        return "注意，磁盘空间不足" if appeared else "磁盘空间已恢复"
    if kind == "operator":
        return "没有操作员" if appeared else None
    return None  # starting, sealing, and kinds this program does not know


# Quickcheck's alerts

FAILS = {
    "R1": "录制没有正常结束",
    "R2": "缺少数据流",
    "R5": "太短",
}
ONCE_WARNINGS = {
    "R9": "没有回到准备位",
    "R6": "夹爪次数太少",
}


@dataclass
class AlertTail:
    """New lines of quickcheck's alerts file, from where it was at the first ``read``."""

    path: Path
    offset: int = -1
    said: set[tuple[str, str]] = field(default_factory=set)

    def read(self) -> list[dict[str, Any]]:
        try:
            size = self.path.stat().st_size
        except OSError:
            self.offset = 0
            return []
        if self.offset < 0:
            self.offset = size
            return []
        if size < self.offset:
            self.offset = 0  # a new file
        if size == self.offset:
            return []
        with self.path.open("rb") as file:
            file.seek(self.offset)
            data = file.read(size - self.offset)
        complete = data.rfind(b"\n") + 1
        self.offset += complete
        lines = []
        for line in data[:complete].splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                continue
        return lines


def alert_speech(lines: Sequence[Mapping[str, Any]], said: set[tuple[str, str]]) -> list[str]:
    """One sentence per Episode: its failures, a missed return to ready and too few gripper
    cycles, each said once."""
    phrases: dict[str, list[str]] = {}
    for line in lines:
        episode, rule, level = str(line.get("episode_id")), str(line.get("rule")), line.get("level")
        if level == "fail":
            phrase = FAILS.get(rule, "快检不通过")
        elif level == "warn" and rule in ONCE_WARNINGS:
            phrase = ONCE_WARNINGS[rule]
        else:
            continue
        if (episode, phrase) in said:
            continue
        said.add((episode, phrase))
        phrases.setdefault(episode, []).append(phrase)
    return ["上一条：" + "，".join(p) for p in phrases.values()]


# The station


@dataclass
class Announcer:
    """What the station's status said last, and what to say about the next one."""

    names: Mapping[str, str]
    every: float
    blocking: dict[Key, Mapping[str, Any]] = field(default_factory=dict)
    spoken: dict[Key, Mapping[str, Any]] = field(default_factory=dict)
    """Items said as they appeared, kept until their key clears: their recovery is said then."""
    states: dict[str, str] = field(default_factory=dict)
    last_said: dict[Key, float] = field(default_factory=dict)
    was_recording: bool = False
    seen: bool = False

    def lost(self) -> list[str]:
        """The station stopped answering."""
        said = ["站点已断开"] if self.seen and self.was_recording else []
        self.seen = self.was_recording = False
        self.blocking.clear()
        self.spoken.clear()
        self.states.clear()
        self.last_said.clear()
        return said

    def status(self, status: Mapping[str, Any], now: float) -> list[str]:
        """What to say about this status, ``now`` on a monotonic clock in seconds."""
        self.seen = True
        blocking = status.get("blocking")
        if isinstance(blocking, list):
            said, down = self._blocking(blocking, now)
        else:
            said, down = self._devices(status.get("devices") or {}, now)
        recording = bool(status.get("recording"))
        if self.was_recording and not recording and down:
            said.append("录制已中止")
        self.was_recording = recording
        return said

    def _blocking(self, blocking: list[Mapping[str, Any]], now: float) -> tuple[list[str], bool]:
        """A key's item can change while it stays (a device starting, then fault): what is said
        follows the item, its recovery once the key clears."""
        said: list[str] = []
        _, cleared, self.blocking = blocking_changes(self.blocking, blocking)
        for item in cleared:
            key = blocking_key(item)
            self.last_said.pop(key, None)
            spoken = self.spoken.pop(key, None)
            if spoken is not None and (text := blocking_speech(spoken, False, self.names)):
                said.append(text)
        for key, item in self.blocking.items():
            text = blocking_speech(item, True, self.names)
            if text is None:
                self.last_said.pop(key, None)
                continue
            spoken = self.spoken.get(key)
            fresh = key not in self.last_said or spoken is None
            if fresh or blocking_speech(spoken, True, self.names) != text:
                said.append(text)
                self.spoken[key], self.last_said[key] = item, now
            elif key[0] not in ONCE and now - self.last_said[key] >= self.every:
                said.append(text)
                self.last_said[key] = now
        down = any(kind not in (*QUIET, *ONCE) for kind, _ in self.blocking)
        return said, down

    def _devices(self, devices: Mapping[str, Any], now: float) -> tuple[list[str], bool]:
        said: list[str] = []
        states = {
            name: str(device.get("state", "") if isinstance(device, Mapping) else device)
            for name, device in devices.items()
        }
        went, back = device_changes(self.states, states)
        self.states = states
        for name in back:
            self.last_said.pop(("device", name), None)
            said.append(f"{spoken_name(self.names, name)}已恢复")
        for name, state in states.items():
            key = ("device", name)
            if state not in DOWN:
                continue
            if name in went or now - self.last_said.get(key, now) >= self.every:
                said.append(f"注意，{spoken_name(self.names, name)}掉线")
                self.last_said[key] = now
        return said, any(s in DOWN for s in states.values())


# Speaking


def say(text: str) -> None:
    print(time.strftime("%H:%M:%S"), text, flush=True)
    if shutil.which("spd-say"):
        subprocess.run(["spd-say", "-l", "zh", "-r", "-10", "-w", text], check=False)
    elif shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", "-v", "zh", text], check=False)


class Voice:
    """Speaks in a thread of its own, so the client's heartbeat never waits for speech."""

    def __init__(self, speak: Callable[[str], None] = say) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._speak = speak
        threading.Thread(target=self._run, daemon=True).start()

    def __call__(self, text: str) -> None:
        self._queue.put(text)

    def _run(self) -> None:
        while True:
            self._speak(self._queue.get())


def as_dict(status: Any) -> dict[str, Any]:
    """The status as JSON gives it, extra keys (such as ``blocking``) included."""
    return status.model_dump(mode="json") if hasattr(status, "model_dump") else dict(status)


def default_station() -> str:
    from xpisode.config import load, project_of

    named = os.environ.get("XPISODE_STATION_FILE", "").strip()  # scripts/station_env.sh
    if named:
        return load(Path(named)).station
    project = project_of(Path.cwd())
    if project is None or project.settings.default_station is None:
        raise SystemExit("no default station here; pass --station")
    return load(project.directory / project.settings.default_station).station


def project_data_root() -> Path | None:
    for directory in (Path.cwd(), *Path.cwd().parents):
        project = directory / "pyproject.toml"
        if project.is_file():
            settings = tomllib.loads(project.read_text()).get("tool", {}).get("xpisode")
            if settings is not None:
                return (directory / Path(settings.get("data", "data")).expanduser()).resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--station")
    parser.add_argument("--name", default="announcer", help="the client name it joins as")
    parser.add_argument("--every", type=float, default=8.0, help="seconds between repeats")
    parser.add_argument("--names", type=Path, help="TOML: device name = spoken name")
    parser.add_argument("--root", type=Path, help="data root (default: the station's)")
    args = parser.parse_args()
    from xpisode.station.operations import StatusSubscription
    from xpisode_device.iox import IceoryxTransport
    from xpisode_env.env import Env, EnvClosedError, EnvError

    station = args.station or default_station()
    names = dict(NAMES)
    if args.names:
        names.update(tomllib.loads(args.names.read_text()))
    transport = IceoryxTransport(station, f"env_{args.name}")
    status_of = StatusSubscription(transport)
    # A client with no groups: the station counts it as running and starts no second one.
    env = Env(args.name, transport, description="says faults and quickcheck alerts out loud")
    voice = Voice()
    announcer = Announcer(names, args.every)
    tail: AlertTail | None = None
    print(f"announcing for station {station!r}", flush=True)
    while True:
        try:
            env.poll()
        except EnvClosedError:
            print("the station shut down", flush=True)
            return 0
        except EnvError as error:
            print(error, file=sys.stderr, flush=True)
            return 1
        status = status_of.newest()
        if status is None:
            for text in announcer.lost():
                voice(text)
        else:
            current = as_dict(status)
            for text in announcer.status(current, time.monotonic()):
                voice(text)
            if tail is None:
                where = current.get("data_root")
                root = args.root or (Path(where) if where else project_data_root())
                if root is not None:
                    tail = AlertTail(root / "local" / "quickcheck" / "alerts.jsonl")
                    tail.read()  # what it holds already was said before, or not at all
        if tail is not None:
            for text in alert_speech(tail.read(), tail.said):
                voice(text)
        time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
