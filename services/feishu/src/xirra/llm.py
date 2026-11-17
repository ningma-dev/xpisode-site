"""Free questions: an OpenAI-compatible chat model with read-only tools over the panel.

The model only reads. Every answer that states a number or a record names where it came from
(an Episode, plan or dataset id), so a person can check it.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai import OpenAI

from xirra import stats
from xirra.panel import Panel

SYSTEM = """你是机器人团队的数据助手，回答关于 xpisode 数据平台的问题：录制（Episode）、判断、任务、采集和评测计划、数据集。
- 只根据工具查到的事实回答；查不到就直说查不到，不要猜，不要编数字。
- 用简短的中文回答，先给结论。提到具体的录制、计划、数据集时，附上它的 id。
- 数据以已经传到 OSS 的为准；还在机器人电脑上没上传的，你看不到，必要时提醒这一点。
- 时间用北京时间。今天是 {today}。
- 你不能直接修改数据。要建任务、建采集计划、结束计划、建数据集时，调用 propose_* 工具起草一份提议；提议会以卡片发给提问人，由他点"确认"才执行。起草前先用查询工具核对（任务是否存在、站点名、条数）。改判断、删数据你做不了，告诉他在面板上操作。
- 起草任务时：任务名用小写英文和下划线；description 写清摆放（Set-up）、什么时候停止录制、部分得分分档（如有）；success 写录制停止时怎样算成功；instruction 是给模型的一句英文指令。都用英文，和已有任务的写法一致。"""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_episodes",
            "description": "按条件查录制，返回条数、总时长和最多 30 条摘要。日期是北京时间 YYYY-MM-DD。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "station": {"type": "string"},
                    "operator": {"type": "string"},
                    "plan": {"type": "string", "description": "计划 id"},
                    "kept": {"type": "string", "enum": ["true", "false", ""], "description": "true 要，false 不要，空字符串是未判断"},
                    "outcome": {"type": "string", "enum": ["success", "failure"]},
                    "since": {"type": "string"},
                    "until": {"type": "string", "description": "包含这一天"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {"name": "list_plans", "description": "所有计划（采集、评测）及进度。", "parameters": {"type": "object", "properties": {"all": {"type": "boolean", "description": "包括已关闭的"}}}},
    },
    {
        "type": "function",
        "function": {"name": "evaluation", "description": "一个评测计划各候选的试验数、已判断数、成功数。", "parameters": {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}},
    },
    {
        "type": "function",
        "function": {"name": "list_tasks", "description": "所有任务的当前版本：名字、说明、成功标准、给模型的指令。", "parameters": {"type": "object", "properties": {}}},
    },
    {
        "type": "function",
        "function": {"name": "list_datasets", "description": "所有数据集：id、名字、成员数、划分、谁建的、读过它的运行。", "parameters": {"type": "object", "properties": {}}},
    },
    {
        "type": "function",
        "function": {"name": "dataset", "description": "一个数据集的详情。", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    },
]

PROPOSE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "propose_task",
            "description": "起草新任务，或已有任务的新版本（同名即新版本）。提问人确认后才写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "success": {"type": "string"},
                    "instruction": {"type": "string"},
                },
                "required": ["name", "description", "success", "instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "起草一个采集计划：在某站点录某任务若干条示范。确认后才建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "station": {"type": "string"},
                    "count": {"type": "integer"},
                    "name": {"type": "string", "description": "不给就自动取 任务_站点_日期"},
                },
                "required": ["task", "station", "count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_close_plan",
            "description": "起草结束一个计划（录满了或不再录）。确认后才执行。",
            "parameters": {"type": "object", "properties": {"plan": {"type": "string", "description": "计划 id 或名字"}}, "required": ["plan"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_dataset",
            "description": "起草一个数据集：按条件选录制（默认只要判为要的），并划分 train/val。确认后才建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "task": {"type": "string"},
                    "station": {"type": "string"},
                    "operator": {"type": "string"},
                    "plan": {"type": "string"},
                    "exclude_failures": {"type": "boolean", "description": "去掉判为失败的"},
                    "val_fraction": {"type": "number", "description": "默认 0.1"},
                },
                "required": ["name"],
            },
        },
    },
]


HISTORY_S = 24 * 3600
"""A chat's conversation is forgotten after a day without a word."""
THREAD_S = 7 * 24 * 3600
"""A reply thread is one topic: kept a week."""
HISTORY_TURNS = 10


def _date_ns(text: str, end: bool = False) -> int:
    d = dt.date.fromisoformat(text)
    start, stop = stats.day_bounds(d)
    return stop if end else start


class Asker:
    def __init__(
        self, panel: Panel, base_url: str, api_key: str, model: str, log: Path, hide: set[str], team: Any = None
    ) -> None:
        self.panel = panel
        self.team = team
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=120)
        self.model = model
        self.log = log
        self.hide = hide
        self.history_path = log.with_name("history.json")
        self.history: dict[str, tuple[float, list[dict[str, Any]]]] = (
            {k: (v[0], v[1]) for k, v in json.loads(self.history_path.read_text()).items()}
            if self.history_path.exists()
            else {}
        )
        """Per conversation (a reply thread, else a chat and a person): the recent turns, as plain
        question and answer, kept on disk so a restart forgets nothing."""
        self.tools: dict[str, Callable[..., Any]] = {
            "search_episodes": self._search,
            "list_plans": self._plans,
            "evaluation": lambda plan: self.panel.evaluation(plan),
            "list_tasks": self._tasks,
            "list_datasets": self._datasets,
            "dataset": self._dataset,
        }

    # tools

    def _search(self, since: str | None = None, until: str | None = None, **filters: Any) -> dict[str, Any]:
        eps = self.panel.episodes(**filters)
        eps = [e for e in eps if e.get("station") not in self.hide or filters.get("station")]
        if since:
            eps = [e for e in eps if e["start_unix_ns"] >= _date_ns(since)]
        if until:
            eps = [e for e in eps if e["start_unix_ns"] < _date_ns(until, end=True)]
        total = stats.Tally()
        for e in eps:
            total.add(e)
        sample = [
            {
                "id": e["episode_id"],
                "time": dt.datetime.fromtimestamp(e["start_unix_ns"] / 1e9, stats.TZ).strftime("%Y-%m-%d %H:%M"),
                "station": e["station"],
                "task": e.get("task"),
                "operator": e.get("operator"),
                "seconds": round((e.get("duration_ns") or 0) / 1e9),
                "keep": e.get("keep"),
                "outcome": e.get("outcome"),
                "judged_by": e.get("judged_by"),
                "plan": e.get("plan_id"),
            }
            for e in sorted(eps, key=lambda e: -e["start_unix_ns"])[:30]
        ]
        return {
            "count": total.count,
            "hours": round(total.seconds / 3600, 2),
            "kept": total.kept,
            "dropped": total.dropped,
            "unjudged": total.unjudged,
            "by_task": total.tasks,
            "latest": sample,
        }

    def _plans(self, all: bool = False) -> list[dict[str, Any]]:
        return stats.plan_rows(self.panel.plans(), open_only=not all)

    def _tasks(self) -> list[dict[str, Any]]:
        out = {}
        for t in self.panel.tasks():
            task = t["task"]
            out[task["name"]] = {k: task.get(k) for k in ("name", "description", "success", "instruction", "archived")}
        return list(out.values())

    def _datasets(self) -> list[dict[str, Any]]:
        out = []
        for d in self.panel.datasets():
            ds = d.get("dataset", d)
            out.append(
                {
                    "id": ds.get("dataset_id"),
                    "name": ds.get("name"),
                    "members": len(ds.get("episodes", [])),
                    "parts": {k: len(v) for k, v in (ds.get("parts") or {}).items()},
                    "created_by": ds.get("created_by"),
                    "selection": ds.get("selection"),
                    "note": ds.get("note"),
                    "read_by": [r.get("run") if isinstance(r, dict) else r for r in d.get("readers", d.get("runs", []))][:20],
                }
            )
        return out

    def _dataset(self, id: str) -> dict[str, Any]:
        d = self.panel.dataset(id)
        ds = d.get("dataset", d)
        ds = {k: v for k, v in ds.items() if k != "episodes"}
        ds["members"] = len(d.get("dataset", d).get("episodes", []))
        return {**{k: v for k, v in d.items() if k != "dataset"}, "dataset": ds}

    # the loop

    def ask(
        self, question: str, who: str | None, open_id: str = "", proposals: Any = None, session: str = ""
    ) -> tuple[str, list[Any]]:
        """The answer, and the proposals made on the way (each becomes a card to confirm)."""
        made: list[Any] = []
        tools = TOOLS + (PROPOSE_TOOLS if proposals is not None else [])

        def propose(kind: str, **args: Any) -> dict[str, Any]:
            if who is None:
                return {"error": "还不知道提问人是谁，不能写入；让他发「我是谁」选一下"}
            if kind == "task":
                p = proposals.task(who, open_id, **args)
            elif kind == "plan":
                p = proposals.plan(who, open_id, **args)
            elif kind == "close":
                p = proposals.close(who, open_id, **args)
            else:
                filters = {k: args.get(k) for k in ("task", "station", "operator", "plan")}
                if args.get("exclude_failures"):
                    filters["failed"] = "false"
                val = float(args.get("val_fraction") or 0.1)
                p = proposals.dataset(who, open_id, args["name"], filters, {"train": 1 - val, "val": val} if val else None)
            made.append(p)
            return {"proposal": p.id, "summary": p.summary, "note": "已作为卡片发给提问人，等他确认"}

        local = {
            "propose_task": lambda **a: propose("task", **a),
            "propose_plan": lambda **a: propose("plan", **a),
            "propose_close_plan": lambda **a: propose("close", **a),
            "propose_dataset": lambda **a: propose("dataset", **a),
        }
        at, before = self.history.get(session, (0.0, []))
        if time.time() - at > (THREAD_S if session.startswith("thread:") else HISTORY_S):
            before = []
        asked = {"role": "user", "content": (f"（提问人：{who}）" if who else "") + question}
        known = ""
        if self.team is not None:
            try:
                known = "\n\n团队现状（从记录里读出，可能几分钟前的）：\n" + self.team.text()
            except Exception:  # noqa: BLE001 - answer without it
                known = ""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM.format(today=stats.today().isoformat()) + known},
            *before,
            asked,
        ]
        for _ in range(8):
            reply = self.client.chat.completions.create(model=self.model, messages=messages, tools=tools)
            msg = reply.choices[0].message
            if not msg.tool_calls:
                answer = msg.content or "（没有回答）"
                self._log(question, who, answer)
                if session:
                    kept = [*before, asked, {"role": "assistant", "content": answer}][-HISTORY_TURNS * 2 :]
                    self.history[session] = (time.time(), kept)
                    self._save_history()
                return answer, made
            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                    fn = self.tools.get(call.function.name) or local[call.function.name]
                    result = fn(**args)
                except Exception as error:  # noqa: BLE001 - the model sees the failure
                    result = {"error": str(error) or repr(error)}
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False, default=str)[:30000]}
                )
        return "这个问题查了太多步还没有结论，换个问法试试。", made

    def forget(self, session: str) -> None:
        self.history.pop(session, None)
        self._save_history()

    def _save_history(self) -> None:
        now = time.time()
        self.history = {k: v for k, v in self.history.items() if now - v[0] < THREAD_S}
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.history, ensure_ascii=False))
        tmp.replace(self.history_path)

    def _log(self, question: str, who: str | None, answer: str) -> None:
        with self.log.open("a") as f:
            f.write(json.dumps({"at": dt.datetime.now(stats.TZ).isoformat(), "who": who, "q": question, "a": answer}, ensure_ascii=False) + "\n")


class Translator:
    """Chinese for a task's text, made once per task version and kept in STATE_DIR."""

    def __init__(self, panel: Panel, client: OpenAI | None, model: str, cache: Path) -> None:
        self.panel = panel
        self.client = client
        self.model = model
        self.cache_path = cache
        self.cache: dict[str, str] = json.loads(cache.read_text()) if cache.exists() else {}

    def __call__(self, name: str) -> str | None:
        found = [t for t in self.panel.tasks() if t["task"]["name"] == name]
        if not found or self.client is None:
            return None
        ref, task = found[-1]["reference"], found[-1]["task"]
        if ref in self.cache:
            return self.cache[ref]
        text = f"任务说明：\n{task.get('description', '')}\n\n成功标准：\n{task.get('success', '')}"
        reply = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "把机器人采集任务的说明译成简洁准确的中文，给不懂英文的采集员看。保留数字和分档。第一行写一句话概括任务。只输出译文。"},
                {"role": "user", "content": text},
            ],
        )
        zh = (reply.choices[0].message.content or "").strip() + "\n（机器翻译，以英文原文为准）"
        self.cache[ref] = zh
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1))
        return zh
