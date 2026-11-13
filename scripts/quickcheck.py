#!/usr/bin/env python3
"""Quickcheck: rules over each newly sealed Episode, written as derived data.

    .venv/bin/python scripts/quickcheck.py watch               check each Episode as it is sealed
    .venv/bin/python scripts/quickcheck.py watch --backfill    first those not checked yet
    .venv/bin/python scripts/quickcheck.py watch --writes http://localhost:8043
                                                             follow the panel's write feed instead
    .venv/bin/python scripts/quickcheck.py check <id|path>...  check these now
    .venv/bin/python scripts/quickcheck.py validate            compare with what people judged

Each check reads the Episode itself: header, seal, holders and prompts (about 25 ms), then the
frame index and the executed targets ``/control/<group>`` (about 1.5 s). It writes one run of
the program ``quickcheck_rules`` per Episode (xpisode_format.derivations), which the panel
shows, and appends what needs attention to ``<root>/local/quickcheck/alerts.jsonl``, which the
announcer reads.

- R1 sealed as finished, not recovered: fail.
- R2 no required stream missing: fail.
- R3 each camera's rate and gaps: warn below 29.7 fps, over 2 frames missing or a gap over
  100 ms; info for a single missing frame.
- R4 state streams (observations) at their nominal rate (199 of 200 Hz); ``/control/<group>`` at
  90 Hz or more while held: warn.
- R5 duration: fail under 10 s; warn over 1.5 x the p99 of kept Episodes, or over the plan's
  time limit.
- R6 gripper cycles of each active arm while teleop or a policy holds it: warn below the kept
  minimum.
- R7 longest stretch with every active arm still (targets under 0.05 rad/s): warn over 8 s.
- R8 takeovers, control taken from a client that declared a method (not handed to ``move``,
  not released at the end): warn.
- R9 return to ready, the last holder ``move`` with target ``ready`` and 3 s recorded after:
  warn.
- R10 a policy holds while the instruction is empty: warn.

Thresholds marked "kept" are learned per task from the Episodes whose current judgment keeps
them, and stored in the run's parameters. An "active arm" is one whose gripper moves in at
least 80 % of them. xpisode only shows these results; nothing here judges an Episode.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

PROGRAM = "quickcheck_rules"
VERSION = "1"
WHO = "quickcheck"
OPEN_LOOP = ("move",)
"""Clients that replay or return, not teleoperation or a policy."""
LEVELS = ("ok", "info", "warn", "fail")
GRID_NS = 50_000_000
"""The grid the stillness of the arms is judged on."""

FIXED: dict[str, float | int] = {
    "duration_fail_s": 10.0,
    "duration_factor": 1.5,
    "fps_warn": 29.7,
    "gap_warn_ms": 100.0,
    "gap_warn_frames": 2,
    "rate_share": 0.995,
    "control_hz_min": 90.0,
    "idle_speed_rad_s": 0.05,
    "idle_warn_s": 8.0,
    "idle_span_s": 3.0,
    "ready_after_s": 3.0,
    "active_share": 0.8,
    "min_kept": 5,
    "gripper_low": 0.4,
    "gripper_high": 0.6,
}


def data_root(start: Path) -> Path:
    """The ``[tool.xpisode] data`` of the nearest pyproject.toml that has it."""
    for directory in (start, *start.parents):
        project = directory / "pyproject.toml"
        if project.is_file():
            settings = tomllib.loads(project.read_text()).get("tool", {}).get("xpisode")
            if settings is not None:
                return (directory / Path(settings.get("data", "data")).expanduser()).resolve()
    raise SystemExit("no pyproject.toml with [tool.xpisode] above here; pass --root")


# What an Episode holds


@dataclass
class Stream:
    times: npt.NDArray[np.int64]
    """Of each message, ns since the Episode's start."""
    role: str
    nominal_hz: float | None
    camera: bool


@dataclass
class Targets:
    """A group's executed targets, ``/control/<group>``."""

    times: npt.NDArray[np.int64]
    joints: npt.NDArray[np.float64]
    """``[N, J]``: every ``*joint_pos`` field of the group, side by side."""
    gripper: npt.NDArray[np.float64] | None
    """``[N]``: its first ``*gripper_pos`` field, if it has one."""


@dataclass
class Facts:
    episode_id: str
    task: str | None
    duration_ns: int
    termination: str
    reason: str = ""
    issues: list[dict[str, str]] = field(default_factory=list)
    required: tuple[str, ...] = ()
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Name to its declaration: ``groups``, ``method``."""
    holders: list[tuple[int, str, str | None]] = field(default_factory=list)
    """``(ns since start, group, holder)``, the header's first."""
    pauses: list[tuple[int, str]] = field(default_factory=list)
    """``control.state`` paused: ``(ns since start, group)``."""
    prompts: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    streams: dict[str, Stream] = field(default_factory=dict)
    """By topic, ``/device/stream``."""
    targets: dict[str, Targets] = field(default_factory=dict)
    """By group."""
    collection: dict[str, Any] | None = None
    sha256: str = ""


def read_facts(path: Path, *, sha256: bool = True) -> Facts:
    """Read what the rules need from a sealed Episode."""
    from xpisode_format import spec
    from xpisode_format.frames import _index  # the MCAP message indexes, without the messages
    from xpisode_format.reader import EpisodeReader, EventMessage, RecordMessage

    with EpisodeReader(path) as reader:
        header, seal = reader.header, reader.seal
        start = header.start_ns
        facts = Facts(
            episode_id=header.episode_id,
            task=header.context.task,
            duration_ns=max(seal.end_ns - start, 0),
            termination=seal.termination.value,
            reason=seal.reason,
            issues=[
                {"stream": i.stream, "kind": i.kind.value, "detail": i.detail} for i in seal.issues
            ],
            required=tuple(header.required_streams),
            clients={
                name: {"groups": list(c.groups), "method": dict(c.method)}
                for name, c in header.clients.items()
            },
            holders=[(t - start, g, who) for t, g, who, _ in reader.holders()],
            prompts=[(t - start, dict(p.values)) for t, p in reader.prompts()],
            collection=dict(header.context.collection) if header.context.collection else None,
        )
        channels = reader.channels
        index = _index(path, reader.chunk_indexes(), channels, 2.0)
        for channel in channels:
            stream = channel.spec
            if not isinstance(stream, spec.RecordStream) or channel.topic in facts.streams:
                continue
            found = index.get(channel.topic.removeprefix("/"))
            times = found.times - start if found is not None else np.zeros(0, np.int64)
            camera = any(f.dtype == "uint8" and len(f.shape) == 3 for f in stream.fields)
            facts.streams[channel.topic] = Stream(
                times=times.astype(np.int64),
                role=stream.role.value,
                nominal_hz=stream.nominal_hz,
                camera=camera,
            )
        control = [
            c.topic
            for c in channels
            if c.topic.startswith("/control/")
            and isinstance(c.spec, spec.RecordStream)
            and not c.topic.endswith("_proposed")
        ]
        wanted = [*dict.fromkeys(control)]
        if "/control/events" in reader.topics:
            wanted.append("/control/events")
        rows: dict[str, list[tuple[int, npt.NDArray[np.float64], float | None]]] = {}
        for message in reader.messages(wanted):
            if isinstance(message, EventMessage):
                data = message.data
                if message.kind == "control.state" and data.get("state") == "paused":
                    facts.pauses.append((message.log_time - start, str(data.get("group"))))
                continue
            assert isinstance(message, RecordMessage)
            fields = message.stream.fields
            joints = [f.name for f in fields if f.name.endswith("joint_pos")]
            grips = [f.name for f in fields if f.name.endswith("gripper_pos")]
            q = np.concatenate([np.asarray(message.value(n), np.float64).ravel() for n in joints])
            g = float(np.asarray(message.value(grips[0])).ravel()[0]) if grips else None
            rows.setdefault(message.channel.topic, []).append((message.log_time - start, q, g))
        for topic, entries in rows.items():
            gripper = [e[2] for e in entries]
            facts.targets[topic.removeprefix("/control/")] = Targets(
                times=np.array([e[0] for e in entries], np.int64),
                joints=np.stack([e[1] for e in entries]),
                gripper=None if gripper[0] is None else np.array(gripper, np.float64),
            )
    if sha256:
        facts.sha256 = file_hash(path)
    return facts


def file_hash(path: Path) -> str:
    """The Episode's sha256: its sidecar's when the sizes agree, else read from the file."""
    from xpisode_format.sidecar import read
    from xpisode_format.store import file_sha256

    sidecar = read(path)
    if sidecar and sidecar.get("bytes") == path.stat().st_size and sidecar.get("sha256"):
        return str(sidecar["sha256"])
    return file_sha256(path)


# Pieces


Interval = tuple[int, int, str | None]


def holds(facts: Facts, group: str) -> list[Interval]:
    """Who held ``group`` from when to when: ``(start, end, holder)``."""
    changes = [(t, who) for t, g, who in facts.holders if g == group]
    found: list[Interval] = []
    for i, (t, who) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else facts.duration_ns
        if end > t:
            found.append((max(t, 0), min(end, facts.duration_ns), who))
    return found


def driving(facts: Facts, group: str) -> list[tuple[int, int]]:
    """When teleoperation or a policy held ``group``: anyone but an open-loop client."""
    return [(a, b) for a, b, who in holds(facts, group) if who is not None and who not in OPEN_LOOP]


def crossings(values: npt.NDArray[np.float64], low: float, high: float) -> int:
    """How often ``values`` go from below ``low`` to above ``high`` or back."""
    side = np.where(values > high, 1, np.where(values < low, 0, -1))
    side = side[side >= 0]
    return int(np.count_nonzero(np.diff(side))) if len(side) > 1 else 0


def gripper_cycles(facts: Facts, group: str, low: float, high: float) -> int | None:
    """Closings and openings, a pair counting as one, while teleop or a policy held it."""
    targets = facts.targets.get(group)
    if targets is None or targets.gripper is None:
        return None
    total = 0
    for a, b in driving(facts, group):
        inside = (targets.times >= a) & (targets.times < b)
        total += crossings(targets.gripper[inside], low, high)
    return (total + 1) // 2


def arms(facts: Facts) -> list[str]:
    """Groups that drive a gripper."""
    return [g for g, t in facts.targets.items() if t.gripper is not None]


def side_of(group: str) -> str | None:
    lowered = group.lower()
    for side in ("left", "right"):
        if lowered == side or lowered.endswith(("_" + side, side)):
            return side
    return None


def prompt_at(facts: Facts, at_ns: int, name: str) -> Any:
    """The prompt ``name`` in effect at ``at_ns`` since the start."""
    value = None
    for t, values in facts.prompts:
        if t > at_ns:
            break
        value = values.get(name)
    return value


def runs_of(mask: npt.NDArray[np.bool_]) -> list[tuple[int, int]]:
    """``[start, end)`` index ranges where ``mask`` holds."""
    if not len(mask):
        return []
    padded = np.concatenate([[False], mask, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(int(a), int(b)) for a, b in zip(edges[::2], edges[1::2], strict=True)]


# Learning


@dataclass
class Measures:
    """What the thresholds are learned from, per Episode."""

    episode_id: str
    task: str | None
    duration_s: float
    cycles: dict[str, int]

    def encode(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "task": self.task,
            "duration_s": self.duration_s,
            "cycles": self.cycles,
            "version": VERSION,
        }


def measure(facts: Facts, fixed: Mapping[str, float | int] = FIXED) -> Measures:
    cycles = {
        g: c
        for g in arms(facts)
        if (
            c := gripper_cycles(facts, g, float(fixed["gripper_low"]), float(fixed["gripper_high"]))
        )
        is not None
    }
    return Measures(facts.episode_id, facts.task, facts.duration_ns / 1e9, cycles)


@dataclass
class Thresholds:
    task: str | None
    kept: int
    fixed: dict[str, float | int]
    duration_p99_s: float | None = None
    duration_warn_s: float | None = None
    time_limit_s: float | None = None
    active: tuple[str, ...] | None = None
    """None: not learned (too few kept Episodes)."""
    gripper_min: dict[str, int] = field(default_factory=dict)

    def parameters(self) -> dict[str, bool | int | float | str]:
        found: dict[str, bool | int | float | str] = {"task": self.task or "", "kept": self.kept}
        found.update({k: v for k, v in self.fixed.items()})
        if self.duration_p99_s is not None:
            found["duration_p99_s"] = round(self.duration_p99_s, 3)
        if self.duration_warn_s is not None:
            found["duration_warn_s"] = round(self.duration_warn_s, 3)
        if self.time_limit_s is not None:
            found["time_limit_s"] = self.time_limit_s
        if self.active is not None:
            found["active_groups"] = ",".join(self.active)
        for group, low in self.gripper_min.items():
            found[f"gripper_min_{group}"] = low
        return found


def learn(
    task: str | None,
    kept: Sequence[Measures],
    fixed: Mapping[str, float | int] = FIXED,
    time_limit_s: float | None = None,
) -> Thresholds:
    """Per-task thresholds from the kept Episodes of ``task``."""
    learned = Thresholds(task, len(kept), dict(fixed), time_limit_s=time_limit_s)
    if len(kept) < int(fixed["min_kept"]):
        return learned
    p99 = float(np.percentile([m.duration_s for m in kept], 99))
    learned.duration_p99_s = p99
    learned.duration_warn_s = p99 * float(fixed["duration_factor"])
    groups = sorted({g for m in kept for g in m.cycles})
    active = []
    for group in groups:
        moved = sum(1 for m in kept if m.cycles.get(group, 0) > 0)
        if moved >= float(fixed["active_share"]) * len(kept):
            active.append(group)
            learned.gripper_min[group] = min(m.cycles.get(group, 0) for m in kept)
    learned.active = tuple(active)
    return learned


# Rules


@dataclass
class Rule:
    id: str
    level: str
    text: str
    measured: Any = None
    threshold: Any = None

    def encode(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "measured": self.measured,
            "threshold": self.threshold,
            "text": self.text,
        }


@dataclass
class Span:
    start_ns: int
    end_ns: int
    field: str
    value: bool | int | float | str
    text: str = ""


@dataclass
class Result:
    episode_id: str
    status: str
    values: dict[str, bool | int | float | str | None]
    spans: list[Span]
    rules: list[Rule]

    def flagged(self, *ids: str) -> list[Rule]:
        """The rules among ``ids`` (all, without any) that warn or fail."""
        return [r for r in self.rules if r.level in ("warn", "fail") and (not ids or r.id in ids)]


def worst(levels: Iterable[str]) -> str:
    rank = max((LEVELS.index(x) for x in levels), default=0)
    return "ok" if rank < LEVELS.index("warn") else LEVELS[rank]


def evaluate(facts: Facts, th: Thresholds) -> Result:
    """Every rule, from the facts of one Episode and the thresholds of its task."""
    fx = th.fixed
    rules: list[Rule] = []
    spans: list[Span] = []
    values: dict[str, bool | int | float | str | None] = {}

    # R1, R2: the seal
    if facts.termination == "finished":
        rules.append(Rule("R1", "ok", "finished", facts.termination))
    else:
        text = f"{facts.termination}: {facts.reason}" if facts.reason else facts.termination
        rules.append(Rule("R1", "fail", text, facts.termination, "finished"))
    missing = [i["stream"] for i in facts.issues if i["kind"] == "missing_required"]
    if missing:
        rules.append(Rule("R2", "fail", "missing " + ", ".join(missing), missing, []))
    else:
        rules.append(Rule("R2", "ok", "every required stream", [], []))

    # R3: cameras
    reported = _reported_gaps(facts)
    rates: list[float] = []
    for topic, stream in facts.streams.items():
        if not stream.camera:
            continue
        camera = topic.strip("/").split("/")[0]
        t = stream.times
        if len(t) < 2:
            rates.append(0.0)
            rules.append(Rule("R3", "warn", f"{camera}: no frames", {"fps": 0.0}, fx["fps_warn"]))
            continue
        fps = (len(t) - 1) / ((int(t[-1]) - int(t[0])) / 1e9) if t[-1] > t[0] else 0.0
        rates.append(fps)
        dt = np.diff(t)
        period = float(np.median(dt))
        lost = 0
        for i in np.flatnonzero(dt > 1.5 * period):
            frames = max(round(float(dt[i]) / period) - 1, 1)
            lost += frames
            spans.append(
                Span(int(t[i]), int(t[i + 1]), "camera_gap", frames, f"{camera}: {frames} missing")
            )
        lost = max(lost, reported.get(topic, 0))
        gap_ms = float(dt.max()) / 1e6
        measured = {"fps": round(fps, 2), "missing": lost, "gap_ms": round(gap_ms, 1)}
        bad = []
        if fps < fx["fps_warn"]:
            bad.append(f"{fps:.2f} fps")
        if lost > fx["gap_warn_frames"]:
            bad.append(f"{lost} frames missing")
        if gap_ms > fx["gap_warn_ms"]:
            bad.append(f"a {gap_ms:.0f} ms gap")
        threshold = {k: fx[k] for k in ("fps_warn", "gap_warn_frames", "gap_warn_ms")}
        if bad:
            rules.append(Rule("R3", "warn", f"{camera}: " + ", ".join(bad), measured, threshold))
        elif lost:
            rules.append(Rule("R3", "info", f"{camera}: {lost} missing", measured, threshold))
        else:
            rules.append(Rule("R3", "ok", f"{camera}: {fps:.2f} fps", measured, threshold))
    values["fps_min"] = round(min(rates), 3) if rates else None

    # R4: device and control rates
    for topic, stream in facts.streams.items():
        if stream.camera or not stream.nominal_hz or stream.role != "observation":
            continue
        floor = stream.nominal_hz * float(fx["rate_share"])
        t = stream.times
        hz = (len(t) - 1) / ((int(t[-1]) - int(t[0])) / 1e9) if len(t) > 1 and t[-1] > t[0] else 0.0
        level = "warn" if hz < floor and facts.duration_ns > 1e9 else "ok"
        rules.append(Rule("R4", level, f"{topic}: {hz:.1f} Hz", round(hz, 2), round(floor, 2)))
    for group, targets in facts.targets.items():
        hz = _control_rate(facts, group, targets)
        if hz is None:
            continue
        level = "warn" if hz < fx["control_hz_min"] else "ok"
        rules.append(
            Rule("R4", level, f"/control/{group}: {hz:.1f} Hz", round(hz, 2), fx["control_hz_min"])
        )

    # R5: duration
    seconds = facts.duration_ns / 1e9
    values["duration_s"] = round(seconds, 3)
    if seconds < fx["duration_fail_s"]:
        rules.append(
            Rule("R5", "fail", f"{seconds:.1f} s: too short", seconds, fx["duration_fail_s"])
        )
    elif th.duration_warn_s is not None and seconds > th.duration_warn_s:
        text = f"{seconds:.0f} s: longer than 1.5 x kept p99"
        rules.append(Rule("R5", "warn", text, seconds, round(th.duration_warn_s, 1)))
    elif th.time_limit_s is not None and seconds > th.time_limit_s:
        text = f"{seconds:.0f} s: over the time limit"
        rules.append(Rule("R5", "warn", text, seconds, th.time_limit_s))
    else:
        rules.append(Rule("R5", "ok", f"{seconds:.1f} s", seconds, th.duration_warn_s))

    # R6: gripper cycles
    low, high = float(fx["gripper_low"]), float(fx["gripper_high"])
    cycles = {g: c for g in arms(facts) if (c := gripper_cycles(facts, g, low, high)) is not None}
    for side in ("left", "right"):
        values[f"gripper_cycles_{side}"] = next(
            (c for g, c in cycles.items() if side_of(g) == side), None
        )
    if th.active is None:
        rules.append(Rule("R6", "ok", f"not learned ({th.kept} kept)", cycles))
    for group in th.active or ():
        count, least = cycles.get(group, 0), th.gripper_min.get(group, 0)
        if count < least:
            rules.append(
                Rule("R6", "warn", f"{group}: gripper {count} times, kept {least}+", count, least)
            )
        else:
            rules.append(Rule("R6", "ok", f"{group}: gripper {count} times", count, least))

    # R7: stillness
    active = list(th.active) if th.active else arms(facts)
    idle = _still(facts, active, float(fx["idle_speed_rad_s"]))
    longest = max((b - a for a, b in idle), default=0) / 1e9
    values["idle_max_s"] = round(longest, 3)
    for a, b in idle:
        if (b - a) / 1e9 >= fx["idle_span_s"]:
            spans.append(Span(a, b, "idle", round((b - a) / 1e9, 3), "arms still"))
    if longest > fx["idle_warn_s"]:
        rules.append(
            Rule("R7", "warn", f"still for {longest:.1f} s", round(longest, 2), fx["idle_warn_s"])
        )
    else:
        rules.append(
            Rule("R7", "ok", f"longest still {longest:.1f} s", round(longest, 2), fx["idle_warn_s"])
        )

    # R8: takeovers
    taken = _takeovers(facts)
    values["takeovers"] = len(taken)
    for a, b, by, text in taken:
        spans.append(Span(a, b, "takeover", by, text))
    if taken:
        rules.append(Rule("R8", "warn", f"{len(taken)} takeover(s): {taken[0][3]}", len(taken), 0))
    else:
        rules.append(Rule("R8", "ok", "no takeover", 0, 0))

    # R9: return to ready
    returned, why = _returned(facts, float(fx["ready_after_s"]))
    values["returned_ready"] = returned
    if returned is False:
        rules.append(Rule("R9", "warn", why, False, True))
    else:
        rules.append(Rule("R9", "ok", why, returned, True))

    # R10: instruction while a policy holds
    empty = _empty_instruction(facts)
    if empty:
        rules.append(
            Rule(
                "R10",
                "warn",
                f"a policy held with no instruction for {empty:.1f} s",
                round(empty, 2),
                0,
            )
        )
    else:
        rules.append(
            Rule("R10", "ok", "instruction given" if _methods(facts) else "no policy", 0, 0)
        )

    status = worst(r.level for r in rules)
    values = {"status": status, **values}
    spans.sort(key=lambda s: (s.start_ns, s.field))
    return Result(facts.episode_id, status, values, spans, rules)


_GAP_TEXT = re.compile(r"(\d+) messages missing")


def _reported_gaps(facts: Facts) -> dict[str, int]:
    found: dict[str, int] = {}
    for issue in facts.issues:
        if issue["kind"] == "gap" and (match := _GAP_TEXT.search(issue["detail"])):
            found[issue["stream"]] = found.get(issue["stream"], 0) + int(match.group(1))
    return found


def _control_rate(facts: Facts, group: str, targets: Targets) -> float | None:
    """Executed targets a second while the group was held, leaving out pauses."""
    pauses = np.array([t for t, g in facts.pauses if g == group], np.int64)
    ticks = 0
    span = 0
    for a, b, who in holds(facts, group):
        if who is None:
            continue
        t = targets.times[(targets.times >= a) & (targets.times < b)]
        if len(t) < 2:
            continue
        starts, ends = t[:-1], t[1:]
        paused = (
            np.zeros(len(starts), bool)
            if not len(pauses)
            else (np.searchsorted(pauses, starts, "right") < np.searchsorted(pauses, ends, "right"))
        )
        ticks += int(np.count_nonzero(~paused))
        span += int((ends - starts)[~paused].sum())
    return ticks / (span / 1e9) if span >= 1e9 else None


def _still(facts: Facts, groups: Sequence[str], speed: float) -> list[tuple[int, int]]:
    """Stretches (ns since start) in which every group in ``groups`` was driven by teleop or a
    policy and none of its joint targets moved faster than ``speed``."""
    if not groups or facts.duration_ns <= 0:
        return []
    grid = np.arange(0, facts.duration_ns, GRID_NS, dtype=np.int64)
    still = np.ones(len(grid), bool)
    for group in groups:
        targets = facts.targets.get(group)
        held = np.zeros(len(grid), bool)
        for a, b in driving(facts, group):
            held |= (grid >= a) & (grid < b)
        still &= held
        if targets is None or len(targets.times) < 2:
            continue
        k = 5
        dq = np.abs(targets.joints[k:] - targets.joints[:-k]).max(axis=1)
        dt = (targets.times[k:] - targets.times[:-k]) / 1e9
        fast = np.concatenate([np.zeros(k, bool), dq / np.maximum(dt, 1e-6) >= speed])
        i = np.searchsorted(targets.times, grid, "right") - 1
        fresh = (i >= 0) & (grid - targets.times[np.maximum(i, 0)] <= 2 * GRID_NS)
        still &= ~(fresh & fast[np.maximum(i, 0)])
    return [
        (int(grid[a]), min(int(grid[b - 1]) + GRID_NS, facts.duration_ns))
        for a, b in runs_of(still)
    ]


def _methods(facts: Facts) -> set[str]:
    return {name for name, c in facts.clients.items() if c.get("method")}


def _takeovers(facts: Facts) -> list[tuple[int, int, str, str]]:
    """Each moment control was taken from a client that declared a method: ``(start, end,
    new holder, text)``, until the method holds again or the Episode ends. Handing over to an
    open-loop client (the return to ready R9 asks for) is not taking it, nor a release that is
    the group's last change."""
    methods = _methods(facts)
    if not methods:
        return []
    final = {group: i for i, (_, group, _) in enumerate(facts.holders)}
    changes: list[tuple[int, str, str]] = []
    last: dict[str, str | None] = {}
    for i, (t, group, who) in enumerate(facts.holders):
        before = last.get(group)
        last[group] = who
        if before not in methods or who == before or who in OPEN_LOOP:
            continue
        if who is None and final[group] == i:
            continue
        changes.append((t, before, who or "none"))
    found: list[tuple[int, int, str, str]] = []
    for t, before, by in changes:
        if found and t - found[-1][0] < 100_000_000:
            continue  # one switch moves several groups at once
        back = next(
            (u for u, _, who in facts.holders if u > t and who == before), facts.duration_ns
        )
        found.append((t, back, by, f"{before} -> {by}"))
    return found


def _returned(facts: Facts, after_s: float) -> tuple[bool | None, str]:
    """Whether every group of the open-loop client was last held by it, sent to ``ready``,
    with ``after_s`` recorded after."""
    mover = next((name for name in OPEN_LOOP if name in facts.clients), None)
    if mover is None:
        return None, "no move client"
    groups = facts.clients[mover].get("groups") or []
    for group in groups:
        held = holds(facts, group)
        if not held or held[-1][2] != mover:
            return False, "did not return to ready"
        start = held[-1][0]
        target = prompt_at(facts, start + 500_000_000, "target")
        if target != "ready":
            return False, f"last move was to {target!r}, not ready"
        if (facts.duration_ns - start) / 1e9 < after_s:
            return (
                False,
                f"stopped {(facts.duration_ns - start) / 1e9:.1f} s after the move to ready",
            )
    return True, "returned to ready"


def _empty_instruction(facts: Facts) -> float:
    """Seconds in which a policy held a group while the instruction was empty."""
    methods = _methods(facts)
    if not methods:
        return 0.0
    grid = np.arange(0, facts.duration_ns, GRID_NS, dtype=np.int64)
    policy = np.zeros(len(grid), bool)
    for group in {g for _, g, _ in facts.holders}:
        for a, b, who in holds(facts, group):
            if who in methods:
                policy |= (grid >= a) & (grid < b)
    empty = np.zeros(len(grid), bool)
    changes = facts.prompts or [(0, {})]
    for i, (t, values) in enumerate(changes):
        end = changes[i + 1][0] if i + 1 < len(changes) else facts.duration_ns
        if not str(values.get("instruction") or "").strip():
            empty |= (grid >= t) & (grid < end)
    return float(np.count_nonzero(policy & empty)) * GRID_NS / 1e9


# Writing


def outputs() -> Any:
    from xpisode_format.derivations import Outputs
    from xpisode_format.value_types import ValueSpec

    def number(label: str) -> ValueSpec:
        return ValueSpec(type="number", label=label, min=0)

    def integer(label: str) -> ValueSpec:
        return ValueSpec(type="integer", label=label, min=0)

    return Outputs(
        values={
            "status": ValueSpec(type="enum", label="Check", options=("ok", "warn", "fail")),
            "duration_s": number("Duration"),
            "fps_min": number("FPS"),
            "gripper_cycles_left": integer("Grips L"),
            "gripper_cycles_right": integer("Grips R"),
            "idle_max_s": number("Idle"),
            "takeovers": integer("Takeovers"),
            "returned_ready": ValueSpec(type="boolean", label="Ready"),
        },
        spans={
            "idle": number("Idle"),
            "takeover": ValueSpec(type="text", label="Takeover"),
            "camera_gap": integer("Gap"),
        },
    )


def write_result(root: Path, facts: Facts, result: Result, th: Thresholds, who: str = WHO) -> str:
    """One run of ``quickcheck_rules`` holding this Episode's result; its run id."""
    from xpisode_format.derivations import DerivationWriter, Derived, DerivedSpan

    writer = DerivationWriter(
        root,
        PROGRAM,
        VERSION,
        outputs(),
        started_by=who,
        parameters=th.parameters(),
        description="Rules over one Episode: seal, streams, duration, grippers, stillness, "
        "takeovers, return to ready (scripts/quickcheck.py)",
    )
    duration = facts.duration_ns
    derived = Derived(
        episode_id=facts.episode_id,
        sha256=facts.sha256,
        values=result.values,
        spans=tuple(
            DerivedSpan(
                start_ns=min(s.start_ns, duration),
                end_ns=min(s.end_ns, duration),
                field=s.field,
                value=s.value,
                text=s.text,
            )
            for s in result.spans
        ),
        details={"rules": [r.encode() for r in result.rules]},
    )
    writer.add(derived, duration)
    writer.finish({"status": result.status, "episode_id": facts.episode_id})
    return writer.run.run_id


def alerts_path(root: Path) -> Path:
    return root / "local" / "quickcheck" / "alerts.jsonl"


def append_alerts(
    root: Path, result: Result, at_unix_ns: int | None = None
) -> list[dict[str, Any]]:
    """What warns or fails, one line each (contract 8)."""
    at = at_unix_ns if at_unix_ns is not None else time.time_ns()
    lines = [
        {
            "at_unix_ns": at,
            "episode_id": result.episode_id,
            "level": r.level,
            "rule": r.id,
            "text": r.text,
        }
        for r in result.flagged()
    ]
    if lines:
        path = alerts_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as file:
            for line in lines:
                file.write(json.dumps(line, ensure_ascii=False) + "\n")
    return lines


# The data root


class MeasureCache:
    """Measures of Episodes already read, so learning does not read them again:
    ``<root>/local/quickcheck/measures.jsonl``. Without ``write`` (a dry run, a validation),
    or where the root cannot be written, they are kept in memory only."""

    def __init__(self, root: Path, *, write: bool = True) -> None:
        self.path = root / "local" / "quickcheck" / "measures.jsonl"
        self.write = write
        self.known: dict[str, Measures] = {}
        if self.path.is_file():
            for line in self.path.read_text().splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("version") == VERSION:
                    self.known[entry["episode_id"]] = Measures(
                        entry["episode_id"], entry.get("task"), entry["duration_s"], entry["cycles"]
                    )

    def put(self, measures: Measures) -> None:
        if self.known.get(measures.episode_id) == measures:
            return
        self.known[measures.episode_id] = measures
        if not self.write:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as file:
                file.write(json.dumps(measures.encode()) + "\n")
        except OSError as error:
            print(f"measures kept in memory only: {error}", file=sys.stderr)
            self.write = False


class Checker:
    """Checks Episodes of one data root, learning each task's thresholds as it goes."""

    def __init__(self, root: Path, *, write: bool = True, alerts: bool = True, who: str = WHO):
        self.root = root
        self.write = write
        self.alerts = alerts
        self.who = who
        self.cache = MeasureCache(root, write=write)
        self._tasks: dict[str, str | None] = {
            m.episode_id: m.task for m in self.cache.known.values()
        }

    def task_of(self, episode_id: str) -> str | None:
        if episode_id not in self._tasks:
            from xpisode_format.reader import EpisodeReader
            from xpisode_format.sidecar import read
            from xpisode_format.store import find_episode

            path = find_episode(self.root, episode_id)
            task = None
            if path is not None:
                sidecar = read(path)
                if sidecar is not None:
                    task = (sidecar.get("episode", {}).get("context") or {}).get("task")
                else:
                    try:
                        with EpisodeReader(path) as reader:
                            task = reader.header.context.task
                    except Exception:  # noqa: BLE001 - an unreadable Episode has no task
                        task = None
            self._tasks[episode_id] = task
        return self._tasks[episode_id]

    def judged(self) -> dict[str, bool | None]:
        """Episode to whether its current judgment keeps it."""
        from xpisode_format.judgments import JudgmentReader

        reader = JudgmentReader(self.root)
        found: dict[str, bool | None] = {}
        for episode_id in reader.judged():
            judgment = reader.current(episode_id)
            if judgment is not None:
                found[episode_id] = judgment.keep
        return found

    def measures(self, episode_id: str) -> Measures | None:
        from xpisode_format.store import find_episode

        if episode_id in self.cache.known:
            return self.cache.known[episode_id]
        path = find_episode(self.root, episode_id)
        if path is None:
            return None
        try:
            found = measure(read_facts(path, sha256=False))
        except Exception as error:  # noqa: BLE001 - one bad Episode must not stop learning
            print(f"cannot read {episode_id}: {error}", file=sys.stderr)
            return None
        self.cache.put(found)
        return found

    def kept(self, task: str | None, exclude: Iterable[str] = ()) -> list[Measures]:
        left_out = set(exclude)
        found = []
        for episode_id, keep in self.judged().items():
            if not keep or episode_id in left_out or self.task_of(episode_id) != task:
                continue
            if (m := self.measures(episode_id)) is not None:
                found.append(m)
        return found

    def time_limit(self, facts: Facts) -> float | None:
        collection = facts.collection or {}
        if not collection.get("plan") or not collection.get("revision"):
            return None
        try:
            from xpisode_format.plans import PlanReader

            plan = PlanReader(self.root).revision(
                str(collection["plan"]), str(collection["revision"])
            )
        except Exception:  # noqa: BLE001 - an unreadable plan sets no limit
            return None
        return getattr(plan, "time_limit_s", None) if plan is not None else None

    def thresholds(self, facts: Facts) -> Thresholds:
        kept = self.kept(facts.task, exclude=[facts.episode_id])
        return learn(facts.task, kept, FIXED, self.time_limit(facts))

    def check(self, path: Path) -> tuple[Result, str | None]:
        facts = read_facts(path, sha256=self.write)
        self._tasks[facts.episode_id] = facts.task
        self.cache.put(measure(facts))
        th = self.thresholds(facts)
        result = evaluate(facts, th)
        run = write_result(self.root, facts, result, th, self.who) if self.write else None
        if self.alerts:
            append_alerts(self.root, result)
        return result, run


# Triggers


class Trigger(Protocol):
    def wait(self) -> list[Path]:
        """Episodes sealed since the last call; may block a while."""
        ...


class SealedPoll:
    """Look for newly sealed ``.mcap`` in the store every ``every`` seconds."""

    def __init__(self, root: Path, every: float = 0.5, *, existing: bool = False) -> None:
        self.base = root / "store" / "episodes"
        self.every = every
        self.mtimes: dict[str, int] = {}
        self.seen: set[str] = set()
        if not existing:
            self.seen.update(p.name for p in self._scan())

    def _scan(self) -> Iterator[Path]:
        if not self.base.is_dir():
            return
        for b1 in os.scandir(self.base):
            if not b1.is_dir():
                continue
            for b2 in os.scandir(b1.path):
                if not b2.is_dir():
                    continue
                mtime = b2.stat().st_mtime_ns
                if self.mtimes.get(b2.path) == mtime:
                    continue
                self.mtimes[b2.path] = mtime
                for entry in os.scandir(b2.path):
                    if entry.name.endswith(".mcap"):
                        yield Path(entry.path)

    def wait(self) -> list[Path]:
        time.sleep(self.every)
        found = [p for p in self._scan() if p.name not in self.seen]
        self.seen.update(p.name for p in found)
        return sorted(found, key=lambda p: p.name)


class WritesFeed:
    """The panel's write notifications, ``GET /api/v1/writes`` (contract 6), long-polled."""

    def __init__(self, root: Path, panel: str, wait_s: float = 25.0) -> None:
        self.root = root
        self.url = panel.rstrip("/") + "/api/v1/writes"
        self.wait_s = wait_s
        self.after = 0
        self.epoch: str | None = None

    def wait(self) -> list[Path]:
        query = urllib.parse.urlencode(
            {"after": self.after, "kind": "episode", "wait_s": self.wait_s}
        )
        try:
            with urllib.request.urlopen(f"{self.url}?{query}", timeout=self.wait_s + 10) as reply:
                page = json.loads(reply.read())
        except (OSError, ValueError) as error:
            print(f"writes feed: {error}", file=sys.stderr)
            time.sleep(2.0)
            return []
        if page.get("epoch") != self.epoch:
            restart = self.epoch is not None
            self.epoch, self.after = page.get("epoch"), 0
            if restart:
                return []
        self.after = int(page.get("next", self.after))
        return [
            self.root / str(w["path"])
            for w in page.get("writes", [])
            if w.get("kind") == "episode" and str(w.get("path", "")).endswith(".mcap")
        ]


# Commands


def resolve(root: Path, what: str, *, outside: bool = False) -> Path:
    """The Episode ``what`` names: an id in ``root``, or a ``.mcap`` path, which must be in
    ``root`` unless ``outside`` (a dry run: its result goes nowhere)."""
    from xpisode_format.store import find_episode

    if what.endswith(".mcap"):
        path = Path(os.path.abspath(what))
        store = root / "store"
        inside = path.is_relative_to(store) or path.resolve().is_relative_to(store.resolve())
        if not outside and not inside:  # its result would go into a root it is not in
            raise SystemExit(
                f"{what} is not in {root / 'store'}: give its data root with --root, "
                "or use --dry-run"
            )
        return path
    path = find_episode(root, what.removeprefix("episode:"))
    if path is None:
        raise SystemExit(f"no Episode {what} in {root}")
    return path


def checked(root: Path) -> set[str]:
    """Episodes with a result from this version of the program."""
    from xpisode_format.derivations import DerivationReader

    return {
        episode_id
        for episode_id, (run, _) in DerivationReader(root).latest(PROGRAM).items()
        if run.version == VERSION
    }


def show(result: Result, run: str | None) -> None:
    flagged = result.flagged()
    where = f" run {run}" if run else ""
    print(f"{result.episode_id}: {result.status}{where}")
    for rule in flagged:
        print(f"  {rule.id} {rule.level}: {rule.text}")


RETRIES = 5
"""Times an Episode that could not be checked is tried again, once each later round."""


def watch(
    root: Path, trigger: Trigger, checker: Checker, backfill: bool, rounds: int | None = None
) -> None:
    """Check what ``trigger`` finds, for ``rounds`` rounds (None: for ever). What could not be
    checked (it was being written, a read failed) is tried again in the next rounds."""
    done = checked(root)
    failed: dict[Path, int] = {}
    if backfill:
        from xpisode_format.store import episode_paths

        for path in episode_paths(root):
            if path.stem not in done:
                _check_or_retry(checker, path, done, failed)
    print(f"watching {root / 'store' / 'episodes'}", flush=True)
    while rounds is None or rounds > 0:
        rounds = None if rounds is None else rounds - 1
        for path in [*trigger.wait(), *failed]:
            if path.stem in done or not path.is_file():
                failed.pop(path, None)
                continue
            _check_or_retry(checker, path, done, failed)


def _check_or_retry(checker: Checker, path: Path, done: set[str], failed: dict[Path, int]) -> None:
    if _check_one(checker, path, done):
        failed.pop(path, None)
        return
    failed[path] = failed.get(path, 0) + 1
    if failed[path] > RETRIES:
        print(f"{path.stem}: given up after {RETRIES} retries", file=sys.stderr, flush=True)
        del failed[path]


def _check_one(checker: Checker, path: Path, done: set[str]) -> bool:
    started = time.monotonic()
    try:
        result, run = checker.check(path)
    except Exception as error:  # noqa: BLE001 - keep watching
        print(f"{path.stem}: cannot check: {error}", file=sys.stderr, flush=True)
        return False
    done.add(path.stem)
    show(result, run)
    print(f"  ({time.monotonic() - started:.1f} s)", flush=True)
    return True


def validate(root: Path, task: str | None) -> dict[str, Any]:
    """Flags of R5, R6, R9 on judged Episodes against their judgments; thresholds learned from
    the other kept Episodes (leave one out) and from all of them (in sample)."""
    from xpisode_format.store import find_episode

    checker = Checker(root, write=False, alerts=False)
    judged = checker.judged()
    facts: dict[str, Facts] = {}
    for episode_id, keep in sorted(judged.items()):
        if keep is None or (task is not None and checker.task_of(episode_id) != task):
            continue
        path = find_episode(root, episode_id)
        if path is None:
            continue
        facts[episode_id] = read_facts(path, sha256=False)
        checker.cache.put(measure(facts[episode_id]))
    report: dict[str, Any] = {"judged": len(facts), "failures": 0, "kept": 0}
    rows = []
    for mode in ("loo", "in_sample"):
        caught: dict[str, int] = {"R5": 0, "R6": 0, "R9": 0, "any": 0}
        false: dict[str, int] = {"R5": 0, "R6": 0, "R9": 0, "any": 0}
        for episode_id, f in facts.items():
            exclude = [episode_id] if mode == "loo" else []
            th = learn(f.task, checker.kept(f.task, exclude), FIXED, checker.time_limit(f))
            result = evaluate(f, th)
            keep = judged[episode_id]
            ids = {r.id for r in result.flagged("R5", "R6", "R9")}
            for rule in (*ids, "any") if ids else ():
                (false if keep else caught)[rule] += 1
            if mode == "loo":
                rows.append((episode_id, keep, round(f.duration_ns / 1e9, 1), sorted(ids), result))
        report[mode] = {"caught": caught, "false": false}
    report["failures"] = sum(1 for e in facts if judged[e] is False)
    report["kept"] = sum(1 for e in facts if judged[e])
    report["rows"] = [
        {
            "episode_id": e,
            "keep": k,
            "duration_s": d,
            "flagged": ids,
            "texts": [r.text for r in res.flagged("R5", "R6", "R9")],
        }
        for e, k, d, ids, res in rows
    ]
    statuses: dict[str, dict[str, int]] = {"kept": {}, "failures": {}}
    others: dict[str, int] = {}
    for _, keep, _, _, result in rows:
        side = statuses["kept" if keep else "failures"]
        side[result.status] = side.get(result.status, 0) + 1
        if keep:
            for rule in {r.id for r in result.flagged()} - {"R5", "R6", "R9"}:
                others[rule] = others.get(rule, 0) + 1
    report["status"] = statuses
    report["kept_flagged_by_other_rules"] = others
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, help="data root (default: from pyproject.toml)")
    commands = parser.add_subparsers(dest="command", required=True)
    w = commands.add_parser("watch", help="check each Episode as it is sealed")
    w.add_argument("--backfill", action="store_true", help="first check those not checked yet")
    w.add_argument(
        "--writes", metavar="PANEL", help="follow PANEL's /api/v1/writes instead of polling"
    )
    w.add_argument("--every", type=float, default=0.5, help="seconds between polls")
    c = commands.add_parser("check", help="check these Episodes now")
    c.add_argument("episodes", nargs="+", help="Episode ids or .mcap paths")
    c.add_argument("--dry-run", action="store_true", help="print only; write nothing")
    c.add_argument("--json", action="store_true")
    v = commands.add_parser("validate", help="compare R5, R6, R9 with what people judged")
    v.add_argument("--task")
    v.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = (args.root or data_root(Path.cwd())).resolve()
    if args.command == "watch":
        trigger: Trigger = (
            WritesFeed(root, args.writes) if args.writes else SealedPoll(root, args.every)
        )
        watch(root, trigger, Checker(root), args.backfill)
    elif args.command == "check":
        checker = Checker(root, write=not args.dry_run, alerts=not args.dry_run)
        for what in args.episodes:
            result, run = checker.check(resolve(root, what, outside=args.dry_run))
            if args.json:
                print(
                    json.dumps(
                        {
                            "run": run,
                            "values": result.values,
                            "rules": [r.encode() for r in result.rules],
                        }
                    )
                )
            else:
                show(result, run)
    elif args.command == "validate":
        report = validate(root, args.task)
        if args.json:
            print(json.dumps(report, indent=1))
        else:
            _print_validation(report)
    return 0


def _print_validation(report: Mapping[str, Any]) -> None:
    print(f"{report['judged']} judged: {report['failures']} failures, {report['kept']} kept")
    for mode in ("loo", "in_sample"):
        part = report[mode]
        label = "leave one out" if mode == "loo" else "in sample"
        caught = ", ".join(f"{k} {v}" for k, v in part["caught"].items())
        false = ", ".join(f"{k} {v}" for k, v in part["false"].items())
        print(f"  {label}: failures flagged: {caught}; kept flagged: {false}")
    others = report["kept_flagged_by_other_rules"]
    print(f"  status, all rules: {report['status']}; kept flagged by other rules: {others}")
    for row in report["rows"]:
        if row["flagged"] or row["keep"] is False:
            mark = "kept" if row["keep"] else "FAIL"
            texts = "; ".join(row["texts"]) or "-"
            print(f"  {row['episode_id']} {mark} {row['duration_s']:>6} s  {texts}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
