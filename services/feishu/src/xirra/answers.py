"""The fixed answers: daily report, progress, a person's hours, tasks. Plain Chinese markdown."""

from __future__ import annotations

import datetime as dt
from typing import Any

from xirra import stats
from xirra.panel import Panel


class Answers:
    def __init__(self, panel: Panel, hide_stations: set[str], translate: Any = None) -> None:
        self.panel = panel
        self.hide = hide_stations
        self.translate = translate  # task name -> Chinese text, or None

    def _visible(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if r.get("station") and r["station"] not in self.hide]

    # progress

    def progress(self) -> str:
        rows = self._visible(stats.plan_rows(self.panel.plans()))
        if not rows:
            return "现在没有进行中的计划。"
        lines = ["**进行中的计划**"]
        for r in rows:
            left = r["total"] - r["done"]
            wait = f"，{r['pending']} 条待判断" if r["pending"] else ""
            state = "已完成" if left == 0 else f"还差 {left}"
            lines.append(
                f"- {r['station']}｜{r['kind']}｜{r['task']}：{r['done']}/{r['total']}，{state}{wait}"
            )
        return "\n".join(lines)

    # a day

    def day(self, day: dt.date) -> str:
        start, end = stats.day_bounds(day)
        eps = [e for e in self.panel.episodes() if e.get("station") not in self.hide]
        today_eps = stats.within(eps, start, end)
        label = f"{day:%m月%d日}"
        if not today_eps:
            return f"{label}没有新的录制传到 OSS 上。"
        lines = [f"**{label}的采集**（以已传到 OSS 的为准）"]
        for station, t in stats.by(today_eps, "station").items():
            lines.append(f"- {station}：{t.count} 条，{stats.hours(t.seconds)}" + self._tasks(t))
        lines.append("")
        lines.append("**按人**")
        for person, t in stats.by(today_eps, "operator").items():
            extra = f"，未判 {t.unjudged}" if t.unjudged else ""
            lines.append(
                f"- {person}：录 {stats.hours(t.seconds)}（{t.count} 条），要 {t.kept} 条{extra}"
            )
        return "\n".join(lines)

    def _tasks(self, t: stats.Tally) -> str:
        parts = [f"{k} {v}" for k, v in sorted(t.tasks.items(), key=lambda kv: -kv[1])]
        return "（" + "，".join(parts) + "）" if parts else ""

    def report(self) -> str:
        yesterday = stats.today() - dt.timedelta(days=1)
        parts = [self.day(yesterday), "", self.progress()]
        unjudged = [
            e for e in self.panel.episodes(kept="")
            if e.get("station") not in self.hide
        ]
        if unjudged:
            parts += ["", f"OSS 上还有 {len(unjudged)} 条录制没有判断。"]
        return "\n".join(parts)

    # a person

    def person(self, name: str, days: int = 7) -> str:
        end_day = stats.today()
        start, _ = stats.day_bounds(end_day - dt.timedelta(days=days - 1))
        _, end = stats.day_bounds(end_day)
        eps = stats.within(self.panel.episodes(operator=name), start, end)
        if not eps:
            return f"{name} 最近 {days} 天没有传到 OSS 上的录制。"
        lines = [f"**{name} 最近 {days} 天**（以已传到 OSS 的为准）"]
        per_day: dict[str, stats.Tally] = {}
        for e in eps:
            d = dt.datetime.fromtimestamp(e["start_unix_ns"] / 1e9, stats.TZ).strftime("%m-%d")
            per_day.setdefault(d, stats.Tally()).add(e)
        total = stats.Tally()
        for e in eps:
            total.add(e)
        for d, t in sorted(per_day.items()):
            lines.append(f"- {d}：{stats.hours(t.seconds)}，{t.count} 条，要 {t.kept} 条")
        lines.append(f"合计：{stats.hours(total.seconds)}，{total.count} 条，要 {total.kept} 条，未判 {total.unjudged} 条")
        return "\n".join(lines)

    # tasks

    def tasks(self) -> str:
        current = {}
        for t in self.panel.tasks():
            task = t["task"]
            if not task.get("archived"):
                current[task["name"]] = task
        if not current:
            return "还没有任务。"
        lines = ["**任务**"]
        for name, task in sorted(current.items()):
            zh = self.translate(name) if self.translate else None
            first = (zh or task.get("description", "")).splitlines()[0] if (zh or task.get("description")) else ""
            lines.append(f"- **{name}**：{first}")
        return "\n".join(lines)

    def task(self, name: str) -> str:
        found = [t["task"] for t in self.panel.tasks() if t["task"]["name"] == name]
        if not found:
            return f"没有叫 {name} 的任务。"
        task = found[-1]
        zh = self.translate(name) if self.translate else None
        lines = [f"**{name}**"]
        if zh:
            lines += [zh, "", "英文原文（给模型的指令）："]
        lines.append(task.get("instruction") or "（无）")
        if not zh:
            lines += ["", task.get("description", ""), "", "成功：" + task.get("success", "")]
        return "\n".join(lines)
