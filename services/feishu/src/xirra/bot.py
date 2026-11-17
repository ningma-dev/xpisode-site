"""The Feishu bot: messages over the long connection, replies as cards, the daily report.

Long connection: this process dials Feishu; no public address is needed, only outbound HTTPS.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import tomllib
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import lark_oapi as lark
from lark_oapi.api.application.v6 import P2ApplicationBotMenuV6
from lark_oapi.api.contact.v3 import GetUserRequest
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from xirra import forms, stats
from xirra.answers import Answers
from xirra.config import Config

HELP = """**我能做的**
- **进度**：进行中的采集、评测计划，各还差多少
- **今天** / **昨天**：当天传到 OSS 的录制，按站点和按人
- **我**：我最近 7 天的采集时长（需要先把你的飞书账号对应到人员名单）
- **人 名字**：某人最近 7 天的采集时长，例如「人 jianping_yin」
- **任务**：所有任务；**任务 名字**：某个任务的中文说明和给模型的指令
- **日报**：现在就发一份日报
- **我是谁**：选你在人员名单里的名字
- **建计划** / **建数据集**：发一张表单，选好点「起草」
- 其他问题直接问，例如「测温棒录了多少条要的？」「SMT 那个数据集是谁建的？」
- 也可以让我建东西，例如「在 yam_2 上建一个测温棒的采集计划，100 条」「用 yam_1 判为要的叠碗建一个数据集」「结束 stack_bowls_yam_1_20261114 这个计划」。我会先发一张卡片给你看，你点「确认」才会写入

数据以已经传到 OSS 的为准。"""


def card_body(markdown: str, title: str | None = None, buttons: list[dict[str, Any]] | None = None, color: str = "blue") -> dict[str, Any]:
    body: dict[str, Any] = {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "elements": [{"tag": "markdown", "content": markdown[:28000]}],
    }
    if buttons:
        body["elements"].append({"tag": "action", "actions": buttons})
    if title:
        body["header"] = {"title": {"tag": "plain_text", "content": title}, "template": color}
    return body


def card(markdown: str, title: str | None = None) -> str:
    return json.dumps(card_body(markdown, title), ensure_ascii=False)


def proposal_card(p: Any) -> str:
    buttons = [
        {"tag": "button", "text": {"tag": "plain_text", "content": "确认"}, "type": "primary", "value": {"proposal": p.id, "do": "confirm"}},
        {"tag": "button", "text": {"tag": "plain_text", "content": "取消"}, "type": "default", "value": {"proposal": p.id, "do": "cancel"}},
    ]
    note = f"\n\n以 **{p.who}** 的名义写入；只有提出的人能确认，一天内有效。"
    return json.dumps(card_body(p.summary + note, "请确认", buttons, "orange"), ensure_ascii=False)


def outcome_card(p: Any) -> str:
    title, color = {"done": ("已完成", "green"), "cancelled": ("已取消", "grey"), "failed": ("没有成功", "red")}[p.state]
    text = p.summary + ("\n\n" + p.result if p.result else "")
    return json.dumps(card_body(text, title, None, color), ensure_ascii=False)


class Bot:
    def __init__(self, config: Config, answers: Answers, asker: Any | None, proposals: Any | None = None) -> None:
        self.config = config
        self.answers = answers
        self.asker = asker
        self.proposals = proposals
        self.client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.names: dict[str, str] = {}
        self.people_path = config.state_dir / "people.toml"
        self.asked: set[str] = set()
        """Who was shown the "who are you" card since start; the card comes once, not every message."""

    # who

    def people(self) -> dict[str, str]:
        """Feishu open_id -> xpisode name, as people picked themselves (STATE_DIR/people.toml)."""
        return {k: str(v) for k, v in tomllib.loads(self.people_path.read_text()).items()} if self.people_path.exists() else {}

    def person(self, open_id: str) -> str | None:
        return self.people().get(open_id)

    def whoami_card(self, open_id: str) -> str:
        names = sorted(p["name"] for p in self.answers.panel.people())
        can_add = self.proposals is not None and self.proposals.writer is not None
        return forms.whoami_form(names, self.feishu_name(open_id), can_add)

    def _whoami(self, fields: dict[str, Any], open_id: str, message_id: str) -> None:
        try:
            new = str(fields.get("new") or "").strip().lower()
            name = new or str(fields.get("name") or "").strip()
            if not name:
                raise ValueError("选一个名字，或填一个新名字。")
            names = {p["name"] for p in self.answers.panel.people()}
            if name not in names:
                if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
                    raise ValueError("新名字用小写英文、数字和下划线，例如 jianping_yin。")
                if self.proposals is None or self.proposals.writer is None:
                    raise ValueError("现在还不能往名单里加名字，先选一个已有的。")
                self.proposals.writer.add_person(name, by=name)
            mapping = self.people()
            taken = [k for k, v in mapping.items() if v == name and k != open_id]
            if taken:
                raise ValueError(f"{name} 已经对应了别的飞书账号。如果那是你的旧账号，找负责人。")
            mapping[open_id] = name
            tmp = self.people_path.with_suffix(".tmp")
            tmp.write_text("# Feishu open_id = xpisode name; people pick themselves in the bot\n" + "".join(f'"{k}" = "{v}"\n' for k, v in sorted(mapping.items())))
            tmp.replace(self.people_path)
            print(f"[bot] {open_id} is {name}", flush=True)
            self.patch(message_id, json.dumps(card_body(f"好，你是 **{name}**。发「我」看你最近的采集。", "记住了", None, "green"), ensure_ascii=False))
        except Exception as error:  # noqa: BLE001 - show it on the card
            self.patch(message_id, json.dumps(card_body(f"{error}\n\n发「我是谁」再试。", "没有记上", None, "red"), ensure_ascii=False))

    def feishu_name(self, open_id: str) -> str:
        if open_id not in self.names:
            name = ""
            try:
                r = self.client.contact.v3.user.get(
                    GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
                )
                if r.success() and r.data and r.data.user:
                    name = r.data.user.name or ""
            except Exception:  # noqa: BLE001 - a name is a nicety
                pass
            self.names[open_id] = name
        return self.names[open_id]

    # sending

    def reply(self, message_id: str, markdown: str, title: str | None = None) -> None:
        r = self.client.im.v1.message.reply(
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(ReplyMessageRequestBody.builder().msg_type("interactive").content(card(markdown, title)).build())
            .build()
        )
        if not r.success():
            print(f"[bot] reply failed: {r.code} {r.msg}", flush=True)

    def reply_raw(self, message_id: str, content: str) -> None:
        r = self.client.im.v1.message.reply(
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(ReplyMessageRequestBody.builder().msg_type("interactive").content(content).build())
            .build()
        )
        if not r.success():
            print(f"[bot] reply failed: {r.code} {r.msg}", flush=True)

    def patch(self, message_id: str, content: str) -> None:
        r = self.client.im.v1.message.patch(
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(PatchMessageRequestBody.builder().content(content).build())
            .build()
        )
        if not r.success():
            print(f"[bot] patch failed: {r.code} {r.msg}", flush=True)

    # buttons

    def on_card(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """Answer at once (Feishu waits 3 s), carry the write out after, then update the card."""
        event = data.event
        value = (event.action.value or {}) if event and event.action else {}
        pid, do = value.get("proposal"), value.get("do")
        open_id = event.operator.open_id if event and event.operator else ""
        message_id = event.context.open_message_id if event and event.context else ""
        if value.get("form") == "whoami":
            fields = (event.action.form_value or {}) if event and event.action else {}
            self.pool.submit(self._whoami, fields, open_id, message_id)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "记下了…"}})
        if value.get("form") and self.proposals is not None:
            fields = (event.action.form_value or {}) if event and event.action else {}
            self.pool.submit(self._form, value["form"], fields, open_id, message_id)
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "起草中…"}})
        if not pid or self.proposals is None:
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "这张卡片已失效"}})
        self.pool.submit(self._act, pid, do, open_id, message_id)
        return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "处理中…" if do == "confirm" else "取消中…"}})

    def _act(self, pid: str, do: str, open_id: str, message_id: str) -> None:
        try:
            p = self.proposals.confirm(pid, open_id) if do == "confirm" else self.proposals.cancel(pid, open_id)
            self.patch(message_id, outcome_card(p))
        except PermissionError as error:
            print(f"[bot] {error} ({open_id})", flush=True)
        except Exception as error:  # noqa: BLE001 - show it on the card
            traceback.print_exc()
            self.patch(message_id, json.dumps(card_body(str(error), "没有执行", None, "red"), ensure_ascii=False))

    def _form(self, kind: str, fields: dict[str, Any], open_id: str, message_id: str) -> None:
        who = self.person(open_id)
        try:
            if who is None:
                raise PermissionError("我还不知道你是谁，不能建东西。发「我是谁」选一下。")
            if kind == "plan":
                count = int(str(fields.get("count", "")).strip())
                p = self.proposals.plan(who, open_id, fields["task"], fields["station"], count)
            else:
                filters = {"task": fields["task"], "station": fields.get("station") or None}
                if fields.get("failures", "去掉") == "去掉":
                    filters["failed"] = "false"
                val = float(str(fields.get("val") or "0.1"))
                station = fields.get("station") or "all"
                name = (fields.get("name") or "").strip() or f"{fields['task']}_{station}_{stats.today():%Y%m%d}"
                parts = {"train": round(1 - val, 6), "val": val} if val > 0 else {"train": 1.0}
                p = self.proposals.dataset(who, open_id, name, filters, parts)
            self.patch(message_id, proposal_card(p))
        except Exception as error:  # noqa: BLE001 - show it on the card
            self.patch(message_id, json.dumps(card_body(f"{error}\n\n重新发「建计划」或「建数据集」再试。", "没有起草", None, "red"), ensure_ascii=False))

    def form_card(self, kind: str) -> str:
        tasks = sorted({t["task"]["name"] for t in self.answers.panel.tasks() if not t["task"].get("archived")})
        stations = sorted(s for s in self.proposals._stations() if s not in self.answers.hide)
        return forms.plan_form(tasks, stations) if kind == "plan" else forms.dataset_form(tasks, stations)

    # the menu

    def on_menu(self, data: P2ApplicationBotMenuV6) -> None:
        event = data.event
        key = event.event_key if event else ""
        open_id = event.operator.operator_id.open_id if event and event.operator and event.operator.operator_id else ""
        if open_id:
            self.pool.submit(self._menu, key, open_id)

    def _menu(self, key: str, open_id: str) -> None:
        try:
            who = self.person(open_id)
            if key in ("plan_form", "dataset_form"):
                content = self.form_card(key.removesuffix("_form"))
            elif key == "me" and who is None:
                content = self.whoami_card(open_id)
            else:
                text = {"progress": "进度", "today": "今天", "yesterday": "昨天", "me": "我", "tasks": "任务", "help": "帮助"}.get(key, "帮助")
                answer, title, _ = self.answer(text, who, "", open_id)
                content = card(answer, title)
            self.send_user(open_id, content)
        except Exception:  # noqa: BLE001 - keep running
            traceback.print_exc()

    def send_user(self, open_id: str, content: str) -> None:
        r = self.client.im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(CreateMessageRequestBody.builder().receive_id(open_id).msg_type("interactive").content(content).build())
            .build()
        )
        if not r.success():
            print(f"[bot] send to user failed: {r.code} {r.msg}", flush=True)

    def send(self, chat_id: str, markdown: str, title: str | None = None) -> bool:
        r = self.client.im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(card(markdown, title)).build()
            )
            .build()
        )
        if not r.success():
            print(f"[bot] send failed: {r.code} {r.msg}", flush=True)
        return r.success()

    # receiving

    def on_message(self, data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message
        if msg.message_id in self.seen:  # Feishu may deliver an event twice
            return
        self.seen[msg.message_id] = None
        while len(self.seen) > 1000:
            self.seen.popitem(last=False)
        open_id = data.event.sender.sender_id.open_id
        self.pool.submit(self._handle, msg, open_id)

    def _handle(self, msg: Any, open_id: str) -> None:
        try:
            if msg.message_type != "text":
                self.reply(msg.message_id, "我现在只看得懂文字消息。")
                return
            text = json.loads(msg.content).get("text", "")
            text = re.sub(r"@_user_\d+", "", text).strip()
            who = self.person(open_id)
            if text in ("我是谁", "我是") or (who is None and open_id not in self.asked):
                self.asked.add(open_id)
                self.reply_raw(msg.message_id, self.whoami_card(open_id))
                if text in ("我是谁", "我是"):
                    return
            if text in ("建计划", "建采集计划", "建数据集") and self.proposals is not None:
                self.reply_raw(msg.message_id, self.form_card("dataset" if "数据集" in text else "plan"))
                return
            root = getattr(msg, "root_id", None) or getattr(msg, "parent_id", None)
            session = f"thread:{root}" if root else f"{msg.chat_id}:{open_id}"
            answer, title, made = self.answer(text, who, msg.chat_id, open_id, session)
            self.reply(msg.message_id, answer, title)
            for p in made:
                self.reply_raw(msg.message_id, proposal_card(p))
        except Exception:  # noqa: BLE001 - tell the person, keep running
            traceback.print_exc()
            self.reply(msg.message_id, "出错了，没能回答。已经记在日志里。")

    def answer(
        self, text: str, who: str | None, chat_id: str, open_id: str = "", session: str = ""
    ) -> tuple[str, str | None, list[Any]]:
        t = text.strip()
        a = self.answers
        if t in ("新话题", "重新开始") and self.asker is not None:
            self.asker.forget(session or f"{chat_id}:{open_id}")
            return "好，之前的对话不算了。", None, []
        if t in ("", "帮助", "help", "?", "？"):
            return HELP, None, []
        if t == "进度":
            return a.progress(), None, []
        if t == "今天":
            return a.day(stats.today()), None, []
        if t == "昨天":
            return a.day(stats.today() - dt.timedelta(days=1)), None, []
        if t in ("我", "我的"):
            if who is None:
                return "我还不知道你在人员名单里叫什么。发「我是谁」，在卡片里选一下。", None, []
            return a.person(who), None, []
        if m := re.fullmatch(r"人\s+(\S+)", t):
            return a.person(m.group(1)), None, []
        if t == "任务":
            return a.tasks(), None, []
        if m := re.fullmatch(r"任务\s+(\S+)", t):
            return a.task(m.group(1)), None, []
        if t == "日报":
            return a.report(), "日报", []
        if t == "群号":
            return f"这个会话的 chat_id：`{chat_id}`", None, []
        if self.asker is None:
            return "自由提问还没开通（没有配置大模型）。\n\n" + HELP, None, []
        answer, made = self.asker.ask(t, who, open_id, self.proposals, session=session or f"{chat_id}:{open_id}")
        return answer, None, made

    # the daily report

    def report_loop(self) -> None:
        if not self.config.report_chat_id:
            print("[bot] no REPORT_CHAT_ID: no daily report", flush=True)
            return
        sent_path = self.config.state_dir / "report_sent"
        hour, minute = map(int, self.config.report_at.split(":"))
        while True:
            now = dt.datetime.now(stats.TZ)
            sent = sent_path.read_text().strip() if sent_path.exists() else ""
            wait = 30
            if (now.hour, now.minute) >= (hour, minute) and sent != now.date().isoformat():
                try:
                    if self.send(self.config.report_chat_id, self.answers.report(), f"日报 {now:%m月%d日}"):
                        sent_path.write_text(now.date().isoformat())
                    else:
                        wait = 600  # e.g. the bot is not in the group yet: try again in 10 min
                except Exception:  # noqa: BLE001 - try again later
                    traceback.print_exc()
                    wait = 600
            time.sleep(wait)

    # running

    def run(self) -> None:
        threading.Thread(target=self.report_loop, name="report", daemon=True).start()
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.on_message)
            .register_p2_card_action_trigger(self.on_card)
            .register_p2_application_bot_menu_v6(self.on_menu)
            .build()
        )
        lark.ws.Client(self.config.app_id, self.config.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO).start()
