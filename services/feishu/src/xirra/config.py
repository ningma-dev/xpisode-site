"""Settings, from one env file outside the repo (secrets never enter git).

The file (default ~/.config/xirra/env, mode 600) holds KEY=value lines:

    FEISHU_APP_ID, FEISHU_APP_SECRET      the Feishu self-built app
    PANEL_URL                             the panel the bot reads (http://127.0.0.1:8090)
    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL  an OpenAI-compatible chat API; empty: no free questions
    REPORT_CHAT_ID                        the group the daily report goes to; empty: none
    REPORT_AT                             HH:MM, Asia/Shanghai (default 09:00)
    STATE_DIR                             where caches and logs go (default ~/.local/state/xirra)
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV = Path.home() / ".config" / "xirra" / "env"


@dataclass(frozen=True)
class Config:
    app_id: str
    app_secret: str
    panel_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    report_chat_id: str
    report_at: str
    state_dir: Path

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"{path} is readable by others; chmod 600 {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load(path: Path | None = None) -> Config:
    path = Path(os.environ.get("XIRRA_ENV", path or DEFAULT_ENV))
    v = {**read_env(path), **{k: os.environ[k] for k in os.environ if k.startswith(("FEISHU_", "LLM_", "PANEL_", "REPORT_", "STATE_"))}}
    state = Path(v.get("STATE_DIR") or Path.home() / ".local" / "state" / "xirra")
    state.mkdir(parents=True, exist_ok=True)
    return Config(
        app_id=v.get("FEISHU_APP_ID", ""),
        app_secret=v.get("FEISHU_APP_SECRET", ""),
        panel_url=v.get("PANEL_URL", "http://127.0.0.1:8090").rstrip("/"),
        llm_base_url=v.get("LLM_BASE_URL", ""),
        llm_api_key=v.get("LLM_API_KEY", ""),
        llm_model=v.get("LLM_MODEL", ""),
        report_chat_id=v.get("REPORT_CHAT_ID", ""),
        report_at=v.get("REPORT_AT", "09:00"),
        state_dir=state,
    )
