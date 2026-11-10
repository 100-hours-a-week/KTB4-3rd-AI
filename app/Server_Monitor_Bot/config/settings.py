# 모니터 설정. 시크릿은 환경 변수(스테이지는 AI_DB_URL의 DB 이름 ai_<stage>), 나머지는 config.toml에서 읽는다.
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

STAGES = ("local", "dev", "stg", "prod")
DB_NAME = re.compile(r"^ai_(local|dev|stg|prod)$")
CONFIG = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class Settings:
    stage: str
    cfg: dict
    db_url: str | None
    qdrant_url: str | None
    qdrant_key: str | None
    telegram_token: str | None
    admin_ids: tuple[int, ...]
    openai_key: str | None
    buffer_dir: Path
    discord_token: str | None = None
    discord_channel_id: int | None = None
    discord_admin_ids: tuple[int, ...] = ()

    @property
    def enabled(self) -> bool:
        return self.stage in STAGES and bool(self.cfg["stages"][self.stage]["enabled"])

    @property
    def drill_allowed(self) -> bool:
        return self.stage in STAGES and bool(self.cfg["stages"][self.stage]["drill"])

    def feature(self, name: str) -> bool:
        return self.enabled and bool(self.cfg["stages"][self.stage]["features"].get(name, False))

    def is_admin(self, user_id: int | None) -> bool:
        return user_id is not None and (user_id in self.admin_ids or user_id in self.discord_admin_ids)

    def section(self, name: str) -> dict:
        return self.cfg.get(name, {})

    @property
    def policy(self) -> dict[str, dict]:
        return self.cfg.get("policy", {})


def db_name(db_url: str | None) -> str:
    return urlparse(db_url or "").path.lstrip("/")


def stage_of(db_url: str | None) -> str:
    m = DB_NAME.match(db_name(db_url))
    return m.group(1) if m else ""


def admin_ids(raw: str | None) -> tuple[int, ...]:
    ids = (x.strip() for x in (raw or "").split(","))
    return tuple(dict.fromkeys(int(x) for x in ids if x.lstrip("-").isdigit()))


def load(env=os.environ, path: Path = CONFIG) -> Settings:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    return Settings(
        stage=stage_of(env.get("AI_DB_URL")),
        cfg=cfg,
        db_url=env.get("AI_DB_URL"),
        qdrant_url=env.get("AI_QDRANT_URL"),
        qdrant_key=env.get("AI_QDRANT_API_KEY"),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN"),
        admin_ids=admin_ids(env.get("Admin_Telegram_ID")),
        openai_key=env.get("OPENAI_API_KEY_MONITOR") or env.get("OPENAI_API_KEY_1") or env.get("OPENAI_API_KEY"),
        buffer_dir=Path(env.get("SERVER_BOT_DIR") or "/tmp/server_bot"),
        discord_token=env.get("DISCORD_BOT_TOKEN") or None,
        discord_channel_id=int(env["DISCORD_CHANNEL_ID"]) if str(env.get("DISCORD_CHANNEL_ID", "")).isdigit() else None,
        discord_admin_ids=admin_ids(env.get("Admin_Discord_ID")),
    )
