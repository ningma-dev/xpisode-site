#!/usr/bin/env python3
"""A policy on a recorded Episode, before it is on the robot: a replay check.

    .venv/bin/python scripts/replay_check.py --root data --episode <id> --who <person> \\
        --client "<command, with --station {station}>" --client-name <name> [--speed 1.0]
        [--seconds N] [--hold 5.0] [--keep]

It writes a station of its own, ``replay_<id>``, in ``<root>/local/replay/<run id>/``: the
Episode's station as its ``run.json`` says, every device a playback device that plays that
Episode back under the same name (``xpisode_device.playback:Playback``), the one client the
given command (holding every control group: its commands go to devices that ignore them), no
inputs. That folder is a station project of its own: its data root is ``data/`` there, never
the store. It runs that station, records while the Episode plays (its length, or
``--seconds``, and the hold), stops it, and compares what the client proposed with what the
original executed and measured, at the same moments of the original
(``replay_compare.compare_policy``).

The check is a run in ``<root>`` (``xpisode_format.runs``): it read the Episode and what the
client declared it read; its folder holds ``report.json`` and ``summary.txt``. The replay's
view, with the robot as the policy would move it, is ``replay.rrd`` in the replay folder
(``rerun <path>``). Without ``--keep`` the replay's Episode is deleted once viewed; the
station file and the logs stay. Nothing here moves an arm or opens a camera.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

# replay_compare.py was not in the 2026-11-13 handover package: restore it from the old workspace's
# git bundle on OSS (xpisode_data/archive/station-20261113/git-bundles-20261113/) before using this.
from replay_compare import PolicyComparison, compare_policy
from xpisode.config import StationConfig
from xpisode.station.operations import (
    OperationError,
    RecordStart,
    RecordStop,
    ShutdownRequest,
    StatusSubscription,
    read_status,
    request,
)
from xpisode.station.prompts import set_prompts
from xpisode_format import EpisodeReader
from xpisode_format.documents import Termination
from xpisode_format.ids import new_id
from xpisode_format.names import check_who
from xpisode_format.runs import Run, check_reference, episode_reference
from xpisode_format.store import find_episode, local, models_dir
from xpisode_format.tasks import TaskReader

FACTORY = "xpisode_device.playback:Playback"
REPLAY = "replay"
"""``<root>/local/replay/<run id>/``: each check's station project."""
DROPPED = ("station", "episodes", "models", "data_root", "devices", "clients", "groups", "inputs")
"""Keys of ``run.json`` the replay station file does not take from it: it names its own."""
POLL_S = 0.2


class CheckError(RuntimeError):
    """The check could not go on; the message says why."""


# The replay station.


def station_name(run_id: str) -> str:
    """``replay_<8 hex>``: a station of its own, its iceoryx channels apart from any other's."""
    return f"{REPLAY}_{run_id.rpartition('-')[2][:8]}"


def replay_station(
    run_config: Mapping[str, Any],
    *,
    station: str,
    episode: Path,
    client: Sequence[str],
    client_name: str,
    speed: float = 1.0,
    hold_s: float = 5.0,
    python: str = sys.executable,
    rateless: Sequence[str] = (),
) -> dict[str, Any]:
    """The station file, as a document, that plays ``episode`` back to ``client``: the
    layout of ``run_config`` (the Episode's ``run.json``), each device a playback device of
    the same name, ``client_name`` the only client and every group's holder. A required
    stream the recording declares no rate for (``rateless``: older D405 framesets) is left
    out of ``required_streams``, since a station cannot judge such a stream silent."""
    known = StationConfig.model_fields  # a key an older run.json has and it lacks: left out
    doc: dict[str, Any] = {"station": station}
    doc.update(
        {k: v for k, v in run_config.items() if k in known and k not in DROPPED and v is not None}
    )
    doc["devices"] = [
        {
            "name": device["name"],
            "command": [
                python,
                *("-m", "xpisode_device.cli", "run", "--station", "{station}", "--name", "{name}"),
                *("--factory", FACTORY, "--config"),
                json.dumps(
                    {
                        "episode": str(episode.resolve()),
                        "device": device["name"],
                        "speed": speed,
                        "hold_s": hold_s,
                    }
                ),
            ],
        }
        for device in run_config.get("devices", [])
    ]
    if "required_streams" in doc:
        doc["required_streams"] = [t for t in doc["required_streams"] if t not in set(rateless)]
    doc["clients"] = [{"name": client_name, "command": list(client)}]
    doc["groups"] = {
        name: {
            "endpoints": [f"{e['device']}/{e['endpoint']}" for e in group["endpoints"]],
            "holder": client_name,
        }
        for name, group in run_config.get("groups", {}).items()
    }
    return doc


def client_command(command: str, cwd: Path) -> list[str]:
    """The client's command as words; a relative path that names a file in ``cwd`` made
    absolute, as the station runs it in the replay folder."""
    words = shlex.split(command)
    out = []
    for word in words:
        if not word.startswith("-") and "/" in word and not word.startswith("/"):
            found = cwd / word
            if found.exists():
                word = str(found.absolute())
        out.append(word)
    return out


_BARE = re.compile(r"[A-Za-z0-9_-]+\Z")


def toml(doc: Mapping[str, Any]) -> str:
    """``doc`` as TOML: plain keys first, then its tables, then its arrays of tables."""

    def key(k: str) -> str:
        return k if _BARE.match(k) else json.dumps(k)

    def value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int | float):
            return repr(v)
        if isinstance(v, str):
            return json.dumps(v)  # JSON's escapes are TOML's basic string's
        if isinstance(v, Mapping):
            inner = ", ".join(f"{key(k)} = {value(x)}" for k, x in v.items() if x is not None)
            return "{" + inner + "}"
        if isinstance(v, list | tuple):
            return "[" + ", ".join(value(x) for x in v) + "]"
        raise TypeError(f"no TOML for {v!r}")

    def tables(v: Any) -> bool:
        return isinstance(v, list | tuple) and bool(v) and all(isinstance(x, Mapping) for x in v)

    def body(table: Mapping[str, Any]) -> list[str]:
        return [f"{key(k)} = {value(v)}" for k, v in table.items() if v is not None]

    plain = {k: v for k, v in doc.items() if not isinstance(v, Mapping) and not tables(v)}
    lines = body(plain)
    for name, table in doc.items():
        if isinstance(table, Mapping):
            inner = {k: v for k, v in table.items() if not isinstance(v, Mapping)}
            if inner or not table:
                lines += ["", f"[{key(name)}]", *body(inner)]
            for sub, subtable in table.items():
                if isinstance(subtable, Mapping):
                    lines += ["", f"[{key(name)}.{key(sub)}]", *body(subtable)]
    for name, items in doc.items():
        if tables(items):
            for item in items:
                lines += ["", f"[[{key(name)}]]", *body(item)]
    return "\n".join(lines) + "\n"


PROJECT = """\
# A replay check's station project: its data root is data/ here, never the store.
[tool.xpisode]
data = "data"
"""


def write_project(folder: Path, doc: Mapping[str, Any], original: str) -> Path:
    """The replay folder as a station project of its own; its station file's path."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pyproject.toml").write_text(PROJECT)
    station_file = folder / "station.toml"
    head = f"# Plays Episode {original} back to one client (scripts/replay_check.py).\n\n"
    station_file.write_text(head + toml(doc))
    return station_file


def prompt_values(reader: EpisodeReader, root: Path) -> dict[str, Any]:
    """The original's prompts as it started; an instruction it set later or its task's if it
    started with none."""
    timeline = reader.prompts()
    values = {k: v for k, v in timeline[0][1].values.items() if v is not None}
    for _, prompts in timeline[1:]:
        for name, v in prompts.values.items():
            if v is not None:
                values.setdefault(name, v)
    context = reader.header.context
    if "instruction" not in values and context.task:
        tasks = TaskReader(root)
        task = tasks.version(context.task_version) if context.task_version else None
        if task is None and (current := tasks.current().get(context.task)) is not None:
            task = current[1]
        if task is not None and task.instruction:
            values["instruction"] = task.instruction
    return values


# Running it.


class Station:
    """The replay station's process, and what is asked of it. ``stop`` always leaves it
    shut down."""

    def __init__(self, station_file: Path, name: str, who: str, log: Path) -> None:
        self.file, self.name, self.who, self.log = station_file, name, who, log
        self.process: subprocess.Popen[bytes] | None = None
        self.recording = False
        self._status: Any = None

    def start(self) -> None:
        with self.log.open("ab") as out:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "xpisode.cli", "run", str(self.file), "--who", self.who],
                cwd=self.file.parent,
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # Ctrl+C reaches this script, which stops it in order
            )

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def status(self) -> Any:
        """Its newest status; None while there is none."""
        if self._status is None:
            try:
                read_status(self.name, timeout_s=0.5)
            except OperationError:
                return None
            from xpisode_device.iox import IceoryxTransport

            transport = IceoryxTransport(self.name, f"replay_check_{os.getpid()}")
            self._status = StatusSubscription(transport)
        return self._status.newest()

    def request(self, operation: Any, timeout_s: float = 10.0) -> Any:
        return request(self.name, operation, timeout_s)

    def wait(self, what: str, until: Any, timeout_s: float) -> Any:
        """The first status for which ``until`` holds."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not self.alive():
                raise CheckError(f"the replay station ended while waiting for {what}; {self.log}")
            status = self.status()
            if status is not None and until(status):
                return status
            time.sleep(POLL_S)
        raise CheckError(f"no {what} after {timeout_s:.0f} s; see {self.log}")

    def stop(self) -> None:
        """End the recording, let it seal, shut the station down; signals if it will not."""
        if not self.alive():
            return
        if self.recording:
            with contextlib.suppress(OperationError):
                self.request(RecordStop(operator=self.who))
            with contextlib.suppress(CheckError):
                self.wait("the Episode sealed", lambda s: not s.busy, 120)
            self.recording = False
        with contextlib.suppress(OperationError):
            self.request(ShutdownRequest())
        assert self.process is not None
        for sig, wait_s in ((None, 60), (signal.SIGINT, 30), (signal.SIGTERM, 10)):
            if sig is not None:
                print(f"the replay station is slow to stop; sending {sig.name}", file=sys.stderr)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, sig)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(wait_s)
                return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait()


@contextlib.contextmanager
def running(station: Station) -> Iterator[Station]:
    """The station started; stopped when this ends, however it ends. A second Ctrl+C while
    it stops is ignored: stopping it is what the first asked for."""
    station.start()
    try:
        yield station
    finally:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            station.stop()
        finally:
            signal.signal(signal.SIGINT, previous)


def record(
    station: Station,
    *,
    client_name: str,
    prompts: Mapping[str, Any],
    task: str | None,
    record_s: float,
    wait_s: float,
) -> str:
    """Wait until the station can record and the client holds its groups, set the prompts,
    record for ``record_s``; the recorded Episode's id."""

    def holding(status: Any) -> bool:
        client = status.clients.get(client_name)
        if client is None or not status.ready_for(station.who):
            return False
        groups = client.groups or tuple(status.groups)
        return all(g not in status.groups or status.groups[g].holder == client_name for g in groups)

    print(f"waiting for {station.name} to be ready and {client_name} to hold its groups")
    status = station.wait(f"{client_name} holding its groups", holding, wait_s)
    declared = status.prompts.declared
    values = {k: v for k, v in prompts.items() if k in declared and declared[k].spec.type != "clip"}
    if values:
        station.request(set_prompts(status, values, operator=station.who))
    if "instruction" in declared and "instruction" not in values:
        print("warning: the original has no instruction; the client gets none", file=sys.stderr)
    result = station.request(RecordStart(task=task, operator=station.who, via="cli"))
    station.recording = True
    episode_id = str(result["episode_id"])
    print(f"recording {episode_id} for {record_s:.1f} s")
    deadline = time.monotonic() + record_s
    seen = False  # a status from before the start says it is not recording yet
    while time.monotonic() < deadline:
        if not station.alive():
            raise CheckError(f"the replay station ended while recording; {station.log}")
        status = station.status()
        if status is not None and status.recording:
            seen = True
        elif status is not None and seen:
            print("the station ended the recording early", file=sys.stderr)
            break
        time.sleep(POLL_S)
    return episode_id


# The check.


def report(comparison: PolicyComparison, extra: Mapping[str, Any]) -> dict[str, Any]:
    def plain(value: Any) -> Any:
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {k: plain(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [plain(v) for v in value]
        return value

    return {**plain(dataclasses.asdict(comparison)), **extra}


def view(comparison: PolicyComparison, replay: Path, rrd: Path, models: Path) -> Path:
    """The replay's view, the original's targets and measurements laid over its curves at
    speed 1."""
    from xpisode_format import rerun

    overlay = None
    if comparison.speed == 1.0:
        with EpisodeReader(comparison.original) as source:
            start, end = source.header.start_ns, source.seal.end_ns
            topics = [t for t in source.topics if t.startswith("/") and not t.endswith("/events")]
        overlay = rerun.Overlay(
            comparison.original,
            start,
            end,
            comparison.offset_ns,
            tuple(t for g in comparison.groups for t in topics if t == f"/control/{g.group}")
            + tuple(t for t in topics if t.endswith("/state")),
        )
    return rerun.save(replay, rrd, models=models, overlay=overlay)


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, required=True, help="the data root")
    parser.add_argument("--episode", required=True, help="the Episode's id")
    parser.add_argument("--client", required=True, help="the client's command, one string")
    parser.add_argument("--client-name", required=True, help="the name it connects as")
    parser.add_argument("--who", required=True, help="who checks (a person)")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--seconds", type=float, help="record this long (default: the Episode's)")
    parser.add_argument("--hold", type=float, default=5.0, help="the playback's hold, seconds")
    parser.add_argument(
        "--wait", type=float, default=600.0, help="how long the client may take to connect"
    )
    parser.add_argument("--keep", action="store_true", help="keep the replay's Episode")
    args = parser.parse_args(argv)
    try:
        args.who = check_who(args.who)
    except ValueError as error:
        parser.error(f"--who: {error}")
    if args.speed <= 0:
        parser.error("--speed must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    root = args.root.resolve()
    original = find_episode(root, args.episode)
    if original is None:
        print(f"error: no Episode {args.episode} in {root}", file=sys.stderr)
        return 1
    with EpisodeReader(original) as reader:
        run_config = reader.run_config
        task = reader.header.context.task
        duration_s = (reader.seal.end_ns - reader.header.start_ns) / 1e9
        prompts = prompt_values(reader, root)
        rateless = [c.topic for c in reader.channels if getattr(c.spec, "nominal_hz", 1.0) is None]
    run_id = new_id()
    folder = local(root, REPLAY, run_id)
    name = station_name(run_id)
    command = client_command(args.client, Path.cwd())
    doc = replay_station(
        run_config,
        station=name,
        episode=original,
        client=command,
        client_name=args.client_name,
        speed=args.speed,
        hold_s=args.hold,
        rateless=rateless,
    )
    station_file = write_project(folder, doc, args.episode)
    record_s = (args.seconds if args.seconds is not None else duration_s / args.speed) + args.hold
    params = {
        "client_command": command,
        "client_name": args.client_name,
        "speed": args.speed,
        "hold_s": args.hold,
        "record_s": record_s,
        "replay": {"station": name, "folder": str(folder)},
    }
    print(f"replay check {run_id}: {station_file}")
    run = Run(
        root, args.who, [episode_reference(args.episode)], params=params, run_id=run_id, via="cli"
    )
    try:
        station = Station(station_file, name, args.who, folder / "station.log")
        with running(station):
            episode_id = record(
                station,
                client_name=args.client_name,
                prompts=prompts,
                task=task,
                record_s=record_s,
                wait_s=args.wait,
            )
        replay_root = folder / "data"
        replay = find_episode(replay_root, episode_id)
        if replay is None:
            raise CheckError(f"the replay Episode {episode_id} is not in {replay_root}")
        with EpisodeReader(replay) as reader:
            declared = reader.header.clients.get(args.client_name)
        if declared is None:
            raise CheckError(f"{args.client_name} was not connected when the recording began")
        reads = []
        for reference in declared.reads:
            try:
                reads.append(check_reference(reference))
            except ValueError:
                print(f"warning: the client read {reference!r}, not a reference", file=sys.stderr)
        run.read(*reads)
        comparison = compare_policy(original, replay, args.client_name)
        summary = comparison.summary()
        rrd = folder / "replay.rrd"
        extra = {"run": run_id, "replay_episode": episode_id, "rrd": str(rrd), **params}
        report_path = run.output("report.json")
        report_path.write_text(json.dumps(report(comparison, extra), indent=1) + "\n")
        summary_path = run.output("summary.txt")
        summary_path.write_text(summary + "\n")
        run.wrote(report_path, summary_path)
        print(summary)
        models = models_dir(replay_root)
        try:
            view(comparison, replay, rrd, models if models.is_dir() else models_dir(root))
        except Exception as error:  # noqa: BLE001 - the view is not the check
            print(f"warning: no view: {error}", file=sys.stderr)
        else:
            print(f"rerun {rrd}")
        if not args.keep:
            shutil.rmtree(replay_root / "store", ignore_errors=True)
        run.finish()
    except BaseException as error:
        run.note = f"{type(error).__name__}: {error}"[:500]
        run.finish(Termination.ABORTED)
        if isinstance(error, CheckError | OperationError | KeyboardInterrupt):
            print(f"error: {error or 'interrupted'}", file=sys.stderr)
            print(f"kept for a look: {folder}", file=sys.stderr)
            return 130 if isinstance(error, KeyboardInterrupt) else 1
        raise
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
