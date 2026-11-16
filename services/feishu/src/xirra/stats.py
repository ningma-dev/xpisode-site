"""Counts the team asks for, from Episode rows and plans as the panel gives them.

Hours are recorded time (the Episode's duration). "Kept" is what its current judgment kept,
failures included; "unjudged" has no judgment yet. Days are Asia/Shanghai days.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

TZ = dt.timezone(dt.timedelta(hours=8))
NOBODY = "（未记录）"


def day_bounds(day: dt.date) -> tuple[int, int]:
    start = dt.datetime.combine(day, dt.time(), TZ)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp() * 1e9), int(end.timestamp() * 1e9)


def today() -> dt.date:
    return dt.datetime.now(TZ).date()


def within(episodes: list[dict[str, Any]], start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    return [e for e in episodes if start_ns <= e["start_unix_ns"] < end_ns]


@dataclass
class Tally:
    count: int = 0
    seconds: float = 0.0
    kept: int = 0
    kept_seconds: float = 0.0
    dropped: int = 0
    unjudged: int = 0
    tasks: dict[str, int] = field(default_factory=dict)

    def add(self, e: dict[str, Any]) -> None:
        s = (e.get("duration_ns") or 0) / 1e9
        self.count += 1
        self.seconds += s
        if e.get("keep") is True:
            self.kept += 1
            self.kept_seconds += s
        elif e.get("keep") is False:
            self.dropped += 1
        else:
            self.unjudged += 1
        task = e.get("task") or "（无任务）"
        self.tasks[task] = self.tasks.get(task, 0) + 1


def by(episodes: list[dict[str, Any]], key: str) -> dict[str, Tally]:
    out: dict[str, Tally] = defaultdict(Tally)
    for e in episodes:
        name = e.get(key) or NOBODY
        if key == "operator" and name == "plan":
            name = "（旧数据，未记录真人）"
        out[name].add(e)
    return dict(sorted(out.items(), key=lambda kv: -kv[1].seconds))


def hours(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def plan_rows(plans: list[dict[str, Any]], *, open_only: bool = True) -> list[dict[str, Any]]:
    """One line per plan: its name, station, task, how many rows are done of how many."""
    rows = []
    for p in plans:
        plan = p["plan"]
        if open_only and plan.get("state") != "open":
            continue
        total = len(plan.get("rows", []))
        rows.append(
            {
                "plan": plan["plan"],
                "name": plan["name"],
                "station": plan.get("station"),
                "task": (p.get("task") or {}).get("name"),
                "kind": "评测" if plan.get("candidates") else "采集",
                "done": len(p.get("done", [])),
                "pending": len(p.get("pending", [])),
                "total": total,
                "kept": p.get("kept", 0),
                "uploaded": p.get("uploaded", 0),
            }
        )
    return sorted(rows, key=lambda r: (r["station"] or "", r["name"]))
