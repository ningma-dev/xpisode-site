"""xirra bot | xirra ask "问题" | xirra report | xirra check"""

from __future__ import annotations

import argparse

from openai import OpenAI

from xirra import config as config_module
from xirra import team as team_module
from xirra.answers import Answers
from xirra.panel import Panel


def build(cfg: config_module.Config) -> tuple[Answers, object | None, object]:
    from xirra.llm import Asker, Translator
    from xirra.panel import Writer
    from xirra.proposals import Proposals

    panel = Panel(cfg.panel_url)
    writer = Writer(cfg.write_panel_url) if cfg.write_panel_url else None
    proposals = Proposals(panel, writer, cfg.state_dir / "proposals.jsonl")
    site = team_module.site_dir()
    hide = team_module.retired(site)
    translate = None
    asker = None
    if cfg.has_llm:
        client = OpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=120)
        translate = Translator(panel, client, cfg.llm_model, cfg.state_dir / "translations.json")
        team = team_module.Team(panel, site, translate)
        asker = Asker(panel, cfg.llm_base_url, cfg.llm_api_key, cfg.llm_model, cfg.state_dir / "questions.jsonl", hide, team)
    return Answers(panel, hide, translate), asker, proposals


def main() -> int:
    parser = argparse.ArgumentParser(prog="xirra")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bot", help="run the Feishu bot (long connection) and the daily report")
    ask = sub.add_parser("ask", help="answer one message as the bot would, printed here")
    ask.add_argument("text")
    ask.add_argument("--who")
    sub.add_parser("report", help="print the daily report")
    sub.add_parser("check", help="check the settings, the panel and the model")
    args = parser.parse_args()
    cfg = config_module.load()
    answers, asker, proposals = build(cfg)

    if args.command == "report":
        print(answers.report())
    elif args.command == "ask":
        from xirra.bot import Bot

        bot = Bot.__new__(Bot)  # answering needs no Feishu connection
        bot.answers, bot.asker, bot.proposals = answers, asker, proposals
        text, _, made = Bot.answer(bot, args.text, args.who, "local", "local")
        print(text)
        for p in made:
            print(f"\n--- 待确认的提议 {p.id} ---\n{p.summary}")
    elif args.command == "check":
        print("panel:", cfg.panel_url, "episodes", sum(answers.panel.facets()["station"].values()))
        print("feishu app:", "set" if cfg.app_id and cfg.app_secret else "MISSING")
        print("model:", cfg.llm_model if cfg.has_llm else "not set (free questions off)")
        print("report chat:", cfg.report_chat_id or "not set (no daily report)")
        print("writes:", cfg.write_panel_url or "not set (the bot cannot write)")
    elif args.command == "bot":
        if not (cfg.app_id and cfg.app_secret):
            raise SystemExit("FEISHU_APP_ID and FEISHU_APP_SECRET are not set")
        from xirra.bot import Bot

        Bot(cfg, answers, asker, proposals).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
