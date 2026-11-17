"""xirra bot | xirra ask "问题" | xirra report | xirra check"""

from __future__ import annotations

import argparse

from openai import OpenAI

from xirra import config as config_module
from xirra import team as team_module
from xirra.answers import Answers
from xirra.panel import Panel


def build(cfg: config_module.Config) -> tuple[Answers, object | None]:
    from xirra.llm import Asker, Translator

    panel = Panel(cfg.panel_url)
    site = team_module.site_dir()
    hide = team_module.retired(site)
    translate = None
    asker = None
    if cfg.has_llm:
        client = OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=120)
        translate = Translator(panel, client, cfg.llm_model, cfg.state_dir / "translations.json")
        team = team_module.Team(panel, site, translate)
        asker = Asker(panel, cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, cfg.state_dir / "questions.jsonl", hide, team)
    return Answers(panel, hide, translate), asker


def main() -> int:
    parser = argparse.ArgumentParser(prog="xirra")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bot", help="run the Feishu bot (long connection) and the daily report")
    ask = sub.add_parser("ask", help="answer one message as the bot would, printed here")
    ask.add_argument("text")
    sub.add_parser("report", help="print the daily report")
    sub.add_parser("check", help="check the settings, the panel and the model")
    args = parser.parse_args()
    cfg = config_module.load()
    answers, asker = build(cfg)

    if args.command == "report":
        print(answers.report())
    elif args.command == "ask":
        from xirra.bot import Bot

        bot = Bot.__new__(Bot)  # answering needs no Feishu connection
        bot.answers, bot.asker = answers, asker
        text, _ = Bot.answer(bot, args.text, "local", "local")
        print(text)
    elif args.command == "check":
        print("panel:", cfg.panel_url, "episodes", sum(answers.panel.facets()["station"].values()))
        print("feishu app:", "set" if cfg.app_id and cfg.app_secret else "MISSING")
        print("model:", cfg.llm_model if cfg.has_llm else "not set (free questions off)")
        print("report chat:", cfg.report_chat_id or "not set (no daily report)")
    elif args.command == "bot":
        if not (cfg.app_id and cfg.app_secret):
            raise SystemExit("FEISHU_APP_ID and FEISHU_APP_SECRET are not set")
        from xirra.bot import Bot

        Bot(cfg, answers, asker).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
