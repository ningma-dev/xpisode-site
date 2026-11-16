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

from xirra import stats
from xirra.answers import Answers
from xirra.config import Config

HELP = """**我能做的**
- **进度**：进行中的采集、评测计划，各还差多少
- **今天** / **昨天**：当天传到 OSS 的录制，按站点和按人
- **我**：我最近 7 天的采集时长（需要先把你的飞书账号对应到人员名单）
- **人 名字**：某人最近 7 天的采集时长，例如「人 jianping_yin」
- **任务**：所有任务；**任务 名字**：某个任务的中文说明和给模型的指令
- **日报**：现在就发一份日报
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
        self.unknown_path = config.state_dir / "unknown_people.jsonl"

    # who

    def person(self, open_id: str) -> str | None:
        """The xpisode name a Feishu user maps to, from people.toml (open_id = "name")."""
        if self.people_path.exists():
            mapping = tomllib.loads(self.people_path.read_text())
            if open_id in mapping:
                return str(mapping[open_id])
        return None

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
            if who is None:
                self._remember(open_id)
            answer, title, made = self.answer(text, who, msg.chat_id, open_id)
            self.reply(msg.message_id, answer, title)
            for p in made:
                self.reply_raw(msg.message_id, proposal_card(p))
        except Exception:  # noqa: BLE001 - tell the person, keep running
            traceback.print_exc()
            self.reply(msg.message_id, "出错了，没能回答。已经记在日志里。")

    def _remember(self, open_id: str) -> None:
        line = json.dumps({"open_id": open_id, "feishu_name": self.feishu_name(open_id)}, ensure_ascii=False)
        known = self.unknown_path.read_text() if self.unknown_path.exists() else ""
        if open_id not in known:
            with self.unknown_path.open("a") as f:
                f.write(line + "\n")

    def answer(self, text: str, who: str | None, chat_id: str, open_id: str = "") -> tuple[str, str | None, list[Any]]:
        t = text.strip()
        a = self.answers
        if t in ("新话题", "重新开始") and self.asker is not None:
            self.asker.history.pop(f"{chat_id}:{open_id}", None)
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
                return "我还不知道你在人员名单里叫什么。请让负责人把你的飞书账号对应上。", None, []
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
        answer, made = self.asker.ask(t, who, open_id, self.proposals, session=f"{chat_id}:{open_id}")
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
            .build()
        )
        lark.ws.Client(self.config.app_id, self.config.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO).start()
