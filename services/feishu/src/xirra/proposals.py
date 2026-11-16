"""Writes the bot proposes and a person confirms: a task version, a collection plan, closing
a plan, a dataset. Nothing is written until the person who asked presses 确认 on the card.

Each proposal is checked against the data when it is made (the task exists, the station is
known, how many Episodes a dataset would hold) and once more when it is carried out.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from xirra import stats
from xirra.panel import Panel, Writer

NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
EXPIRE_S = 24 * 3600


@dataclass
class Proposal:
    id: str
    kind: str  # task | plan | close | dataset
    who: str  # the xpisode person it is written as
    open_id: str  # only this Feishu user may confirm it
    body: dict[str, Any]
    summary: str
    made: float = field(default_factory=lambda: dt.datetime.now(stats.TZ).timestamp())
    state: str = "open"  # open | done | cancelled | failed
    result: str = ""


class Proposals:
    def __init__(self, panel: Panel, writer: Writer | None, log: Path) -> None:
        self.panel = panel
        self.writer = writer
        self.log = log
        self.open: dict[str, Proposal] = {}

    # checks shared by the proposing tools

    def _stations(self) -> set[str]:
        known = set(self.panel.facets().get("station", {}))
        known |= {p["plan"].get("station") for p in self.panel.plans() if p["plan"].get("station")}
        return known

    def _task(self, name: str) -> dict[str, Any] | None:
        for t in self.panel.tasks():
            if t["task"]["name"] == name:
                return t["task"]
        return None

    def _add(self, kind: str, who: str, open_id: str, body: dict[str, Any], summary: str) -> Proposal:
        p = Proposal(secrets.token_hex(6), kind, who, open_id, body, summary)
        self.open[p.id] = p
        self._write(p)
        return p

    # the proposals

    def task(self, who: str, open_id: str, name: str, description: str, success: str, instruction: str = "") -> Proposal:
        if not NAME.match(name):
            raise ValueError("任务名只用小写英文字母、数字和下划线，例如 insert_temperature_probe")
        if not description.strip() or not success.strip():
            raise ValueError("任务要有说明（description）和成功标准（success）")
        current = self._task(name)
        verb = "新版本（取代当前版本）" if current else "新任务"
        summary = (
            f"**{verb}：{name}**\n\n**给模型的指令**\n{instruction or '（无）'}\n\n"
            f"**说明**\n{description}\n\n**成功标准**\n{success}"
        )
        body = {"name": name, "description": description, "success": success, "instruction": instruction}
        return self._add("task", who, open_id, body, summary)

    def plan(self, who: str, open_id: str, task: str, station: str, count: int, name: str | None = None) -> Proposal:
        found = self._task(task)
        if found is None:
            raise ValueError(f"没有叫 {task} 的任务；先建任务")
        if station not in self._stations():
            raise ValueError(f"不认识站点 {station}；已知的有 {', '.join(sorted(self._stations()))}")
        if not 1 <= count <= 2000:
            raise ValueError("条数在 1 到 2000 之间")
        name = name or f"{task}_{station}_{stats.today():%Y%m%d}"
        same = [p for p in self.panel.plans() if p["plan"]["name"] == name]
        if same:
            raise ValueError(f"已经有叫 {name} 的计划了，换个名字")
        summary = (
            f"**新采集计划：{name}**\n- 任务：{task}\n- 站点：{station}\n- 条数：{count}\n"
            f"- 给模型的指令：{found.get('instruction') or '（无）'}"
        )
        body = {"name": name, "task": task, "station": station, "demonstrations": count}
        return self._add("plan", who, open_id, body, summary)

    def close(self, who: str, open_id: str, plan: str) -> Proposal:
        found = [p for p in self.panel.plans() if plan in (p["plan"]["plan"], p["plan"]["name"])]
        if not found:
            raise ValueError(f"没有计划 {plan}")
        p = found[0]
        rows = len(p["plan"].get("rows", []))
        summary = f"**结束计划：{p['plan']['name']}**\n- 站点：{p['plan'].get('station')}\n- 进度：{len(p.get('done', []))}/{rows}\n结束后站点面板上不再显示它，录过的数据不受影响。"
        return self._add("close", who, open_id, {"plan": p["plan"]["plan"]}, summary)

    def dataset(
        self, who: str, open_id: str, name: str, filters: dict[str, str], parts: dict[str, float] | None, seed: int = 42
    ) -> Proposal:
        if not NAME.match(name):
            raise ValueError("数据集名只用小写英文字母、数字和下划线")
        allowed = {"task", "station", "operator", "plan", "outcome", "kept", "failed"}
        filters = {k: v for k, v in filters.items() if k in allowed and v is not None}
        filters.setdefault("kept", "true")
        eps = self.panel.episodes(**{k: v for k, v in filters.items() if k != "failed"})
        if filters.get("failed") == "false":
            eps = [e for e in eps if e.get("outcome") != "failure"]
        if not eps:
            raise ValueError("按这些条件一条录制都没有")
        t = stats.Tally()
        for e in eps:
            t.add(e)
        parts = parts or {"train": 0.9, "val": 0.1}
        if abs(sum(parts.values()) - 1) > 1e-6:
            raise ValueError("划分比例加起来要等于 1")
        shown = "，".join(f"{k}={v}" for k, v in filters.items())
        split = "，".join(f"{k} {v:.0%}" for k, v in parts.items())
        summary = (
            f"**新数据集：{name}**\n- 条件：{shown}\n- 约 {t.count} 条，{stats.hours(t.seconds)}"
            f"（按任务：{'，'.join(f'{k} {v}' for k, v in t.tasks.items())}）\n- 划分：{split}，随机种子 {seed}"
        )
        body = {"name": name, "filters": filters, "parts": parts, "seed": seed}
        return self._add("dataset", who, open_id, body, summary)

    # carrying out

    def confirm(self, pid: str, open_id: str) -> Proposal:
        p = self.open.get(pid)
        if p is None:
            raise ValueError("这个提议已经处理过或过期了")
        if p.open_id != open_id:
            raise PermissionError("只有提出它的人能确认")
        if dt.datetime.now(stats.TZ).timestamp() - p.made > EXPIRE_S:
            p.state = "cancelled"
            del self.open[pid]
            raise ValueError("提议已过期（超过一天），重新提一次")
        if self.writer is None:
            raise RuntimeError("还不能写入：服务器上的可写数据目录还没开")
        del self.open[pid]
        try:
            p.result = self._do(p)
            p.state = "done"
        except Exception as error:  # noqa: BLE001 - shown on the card
            p.state = "failed"
            p.result = str(error)
        self._write(p)
        return p

    def cancel(self, pid: str, open_id: str) -> Proposal:
        p = self.open.get(pid)
        if p is None:
            raise ValueError("这个提议已经处理过或过期了")
        if p.open_id != open_id:
            raise PermissionError("只有提出它的人能取消")
        del self.open[pid]
        p.state = "cancelled"
        self._write(p)
        return p

    def _do(self, p: Proposal) -> str:
        assert self.writer is not None
        b = p.body
        if p.kind == "task":
            r = self.writer.save_task(b["name"], description=b["description"], success=b["success"], instruction=b["instruction"], by=p.who)
            return f"已保存任务 {b['name']}，版本 `{r['reference'][:19]}…`"
        if p.kind == "plan":
            r = self.writer.create_plan(by=p.who, notes="建于飞书", **b)
            return f"已建计划 {r['plan']['name']}（`{r['plan']['plan']}`），一两分钟后出现在 {b['station']} 的面板上"
        if p.kind == "close":
            self.writer.revise_plan(b["plan"], by=p.who, state="closed")
            return "已结束这个计划"
        if p.kind == "dataset":
            r = self.writer.create_dataset(created_by=p.who, note="建于飞书", **b)
            ds = r.get("dataset", r)
            parts = {k: len(v) for k, v in (ds.get("parts") or {}).items()}
            return f"已建数据集 {ds['name']}（`{ds['dataset_id']}`），{len(ds['episodes'])} 条，划分 {parts}"
        raise ValueError(p.kind)

    def _write(self, p: Proposal) -> None:
        with self.log.open("a") as f:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
