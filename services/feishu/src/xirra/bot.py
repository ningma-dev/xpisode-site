"""The Feishu bot: messages over the long connection, replies as cards, the daily report.

Long connection: this process dials Feishu; no public address is needed, only outbound HTTPS.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import traceback
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import lark_oapi as lark
from lark_oapi.api.application.v6 import P2ApplicationBotMenuV6
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from xirra import stats
from xirra.answers import Answers
from xirra.config import Config

HELP = """**我能做的**
- **进度**：进行中的采集、评测计划，各还差多少
- **今天** / **昨天**：当天传到 OSS 的录制，按站点和按人
- **人 名字**：某人最近 7 天的采集时长，例如「人 jianping_yin」
- **任务**：所有任务；**任务 名字**：某个任务的中文说明和给模型的指令
- **日报**：现在就发一份日报
- 其他问题直接问，例如「测温棒录了多少条要的？」「SMT 那个数据集是谁建的？」

我只看不改。建任务、计划、数据集，改判断，在面板上做。数据以已经传到 OSS 的为准。"""


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


class Bot:
    def __init__(self, config: Config, answers: Answers, asker: Any | None) -> None:
        self.config = config
        self.answers = answers
        self.asker = asker
        self.client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.seen: OrderedDict[str, None] = OrderedDict()

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

    # the menu

    def on_menu(self, data: P2ApplicationBotMenuV6) -> None:
        event = data.event
        key = event.event_key if event else ""
        open_id = event.operator.operator_id.open_id if event and event.operator and event.operator.operator_id else ""
        if open_id:
            self.pool.submit(self._menu, key, open_id)

    def _menu(self, key: str, open_id: str) -> None:
        try:
            text = {"progress": "进度", "today": "今天", "yesterday": "昨天", "tasks": "任务", "help": "帮助"}.get(key, "帮助")
            answer, title = self.answer(text, "", open_id)
            self.send_user(open_id, card(answer, title))
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
            root = getattr(msg, "root_id", None) or getattr(msg, "parent_id", None)
            session = f"thread:{root}" if root else f"{msg.chat_id}:{open_id}"
            answer, title = self.answer(text, msg.chat_id, open_id, session)
            self.reply(msg.message_id, answer, title)
        except Exception:  # noqa: BLE001 - tell the person, keep running
            traceback.print_exc()
            self.reply(msg.message_id, "出错了，没能回答。已经记在日志里。")

    def answer(self, text: str, chat_id: str, open_id: str = "", session: str = "") -> tuple[str, str | None]:
        t = text.strip()
        a = self.answers
        if t in ("新话题", "重新开始") and self.asker is not None:
            self.asker.forget(session or f"{chat_id}:{open_id}")
            return "好，之前的对话不算了。", None
        if t in ("", "帮助", "help", "?", "？"):
            return HELP, None
        if t == "进度":
            return a.progress(), None
        if t == "今天":
            return a.day(stats.today()), None
        if t == "昨天":
            return a.day(stats.today() - dt.timedelta(days=1)), None
        if m := re.fullmatch(r"人\s+(\S+)", t):
            return a.person(m.group(1)), None
        if t == "任务":
            return a.tasks(), None
        if m := re.fullmatch(r"任务\s+(\S+)", t):
            return a.task(m.group(1)), None
        if t == "日报":
            return a.report(), "日报"
        if t == "群号":
            return f"这个会话的 chat_id：`{chat_id}`", None
        if self.asker is None:
            return "自由提问还没开通（没有配置大模型）。\n\n" + HELP, None
        return self.asker.ask(t, session=session or f"{chat_id}:{open_id}"), None

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
            .register_p2_application_bot_menu_v6(self.on_menu)
            .build()
        )
        lark.ws.Client(self.config.app_id, self.config.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO).start()
