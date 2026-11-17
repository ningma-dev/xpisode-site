"""What the bot knows about the team, read from where it already lives; nothing kept by hand.

- Stations: this repo's robots/<station>/ (the first comment line of station.toml says what the
  robot is); robots/_archive/<station>/ is a retired one. Activity comes from the Episodes.
- Tasks: the current versions, with the Chinese summary the translator made for each version,
  so the model can match what people call a task ("测温棒") to its name.
- People: the shared list of names, with what each one did lately (recorded, judged).

Read again at most every few minutes, so a new task, station or person shows up by itself.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xirra import stats
from xirra.panel import Panel

FRESH_S = 600.0
RECENT_DAYS = 30
NO_OPERATOR = "plan"
"""What old Episodes carry as operator when no real person was recorded."""


def site_dir() -> Path | None:
    """The repo this code runs from (services/feishu/src/xirra/ is four levels down)."""
    here = Path(__file__).resolve()
    site = here.parents[4] if len(here.parents) > 4 else None
    return site if site is not None and (site / "robots").is_dir() else None


def stations(site: Path | None) -> dict[str, dict[str, Any]]:
    """Each station's one-line description and whether it is retired, from robots/."""
    out: dict[str, dict[str, Any]] = {}
    if site is None:
        return out
    for folder, retired in ((site / "robots", False), (site / "robots" / "_archive", True)):
        for d in sorted(folder.iterdir()) if folder.is_dir() else []:
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            files = sorted(p.name for p in d.glob("station*.toml"))
            about = ""
            if retired:
                pass  # what is left of a retired station describes leftovers, not the robot
            elif (d / "station.toml").exists():
                first = (d / "station.toml").read_text().splitlines()[:1]
                about = first[0].lstrip("# ").strip().split(". ")[0].rstrip(".") if first else ""
            elif (d / "README.md").exists():
                lines = [ln.strip() for ln in (d / "README.md").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
                about = lines[0] if lines else ""
            out[d.name] = {"about": about, "retired": retired, "station_files": files}
    return out


def retired(site: Path | None) -> set[str]:
    return {name for name, s in stations(site).items() if s["retired"]}


class Team:
    def __init__(self, panel: Panel, site: Path | None, translate: Callable[[str], str | None] | None = None) -> None:
        self.panel = panel
        self.site = site
        self.translate = translate
        self._at = 0.0
        self._text = ""

    def text(self) -> str:
        if time.time() - self._at > FRESH_S or not self._text:
            self._text = self._build()
            self._at = time.time()
        return self._text

    def _build(self) -> str:
        eps = self.panel.episodes()
        plans = stats.plan_rows(self.panel.plans())
        since = (dt.datetime.now(stats.TZ) - dt.timedelta(days=RECENT_DAYS)).timestamp() * 1e9
        recent = [e for e in eps if e["start_unix_ns"] >= since]

        def day(ns: int) -> str:
            return dt.datetime.fromtimestamp(ns / 1e9, stats.TZ).strftime("%m-%d")

        lines = ["## 站点"]
        known = stations(self.site)
        by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in eps:
            by_station[e.get("station") or "?"].append(e)
        for name in sorted(set(known) | set(by_station)):
            s = known.get(name, {})
            mine = by_station.get(name, [])
            bits = [s.get("about") or ""]
            if s.get("retired"):
                bits.append("已退役")
            if len(s.get("station_files", [])) > 1:
                bits.append("站点文件：" + "、".join(s["station_files"]))
            if mine:
                bits.append(f"共 {len(mine)} 条录制，最近一条 {day(max(e['start_unix_ns'] for e in mine))}")
                unnamed = sum(1 for e in mine if e.get("operator") == NO_OPERATOR)
                if unnamed:
                    bits.append(f"其中 {unnamed} 条没记采集员是谁")
            ops = Counter(e.get("operator") for e in recent if e.get("station") == name and e.get("operator") not in (None, NO_OPERATOR))
            if ops:
                bits.append("近 30 天采集员：" + "、".join(ops))
            open_plans = [p["name"] for p in plans if p["station"] == name]
            if open_plans:
                bits.append("进行中的计划：" + "、".join(open_plans))
            lines.append(f"- {name}：" + "；".join(b for b in bits if b))

        lines.append("\n## 任务（中文是机器翻译的概括，用来对应大家的叫法）")
        current: dict[str, dict[str, Any]] = {}
        for t in self.panel.tasks():
            current[t["task"]["name"]] = t["task"]
        for name, task in sorted(current.items()):
            if task.get("archived"):
                continue
            mine = [e for e in eps if e.get("task") == name]
            where = sorted({e.get("station") for e in mine if e.get("station")})
            zh = ""
            if self.translate is not None:
                try:
                    zh = re.sub(r"^(任务)?(概括)?[:：]\s*", "", (self.translate(name) or "").splitlines()[0].strip())
                except Exception:  # noqa: BLE001 - the summary is a help, not a need
                    zh = ""
            bits = [zh or (task.get("instruction") or "")]
            if mine:
                bits.append(f"{len(mine)} 条录制，在 " + "、".join(where))
            lines.append(f"- {name}：" + "；".join(b for b in bits if b))

        lines.append("\n## 人（近 30 天做了什么）")
        rec = Counter(e.get("operator") for e in recent)
        judged = Counter(e.get("judged_by") for e in recent)
        for p in sorted(self.panel.people(), key=lambda p: p["name"]):
            n = p["name"]
            bits = []
            if rec[n]:
                bits.append(f"采集 {rec[n]} 条")
            if judged[n]:
                bits.append(f"判断 {judged[n]} 条")
            lines.append(f"- {n}：" + ("，".join(bits) if bits else "近 30 天没有录制或判断"))
        return "\n".join(lines)
