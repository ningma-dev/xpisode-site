"""Form cards: pick from lists and press a button, instead of typing a request.

Cards use Feishu's card schema 2.0, whose ``form`` sends every field at once when its submit
button is pressed (the callback's ``form_value``). Submitting drafts a proposal; the card then
turns into the usual confirm card.
"""

from __future__ import annotations

import json
from typing import Any


def _text(content: str) -> dict[str, str]:
    return {"tag": "plain_text", "content": content}


def _select(name: str, label: str, options: list[str], *, default: str | None = None, required: bool = True) -> dict[str, Any]:
    field: dict[str, Any] = {
        "tag": "select_static",
        "name": name,
        "placeholder": _text(label),
        "options": [{"text": _text(o), "value": o} for o in options],
        "required": required,
        "width": "fill",
    }
    if default in options:
        field["initial_option"] = default
    return field


def _input(name: str, label: str, default: str = "", *, required: bool = True) -> dict[str, Any]:
    return {
        "tag": "input",
        "name": name,
        "placeholder": _text(label),
        "default_value": default,
        "required": required,
        "width": "fill",
    }


def _form(kind: str, title: str, hint: str, fields: list[dict[str, Any]], submit: str) -> str:
    card = {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {"title": _text(title), "template": "blue"},
        "body": {
            "elements": [
                {"tag": "markdown", "content": hint},
                {
                    "tag": "form",
                    "name": f"{kind}_form",
                    "elements": [
                        *fields,
                        {
                            "tag": "button",
                            "name": "submit",
                            "text": _text(submit),
                            "type": "primary",
                            "form_action_type": "submit",
                            "behaviors": [{"type": "callback", "value": {"form": kind}}],
                        },
                    ],
                },
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def plan_form(tasks: list[str], stations: list[str]) -> str:
    return _form(
        "plan",
        "建采集计划",
        "选任务和站点，填条数。提交后会再给你看一遍，确认才建。",
        [
            _select("task", "任务", tasks),
            _select("station", "站点", stations),
            _input("count", "条数", "100"),
        ],
        "起草",
    )


def dataset_form(tasks: list[str], stations: list[str]) -> str:
    return _form(
        "dataset",
        "建数据集",
        "只收判为「要」的录制。站点不选就是所有站点。提交后会先告诉你有多少条，确认才建。",
        [
            _select("task", "任务", tasks),
            _select("station", "站点（可不选）", stations, required=False),
            _select("failures", "判为失败的", ["去掉", "保留"], default="去掉"),
            _input("val", "验证集比例", "0.1"),
            _input("name", "名字（可不填，自动生成）", "", required=False),
        ],
        "起草",
    )
