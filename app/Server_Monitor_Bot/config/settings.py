# 모니터 설정. 스테이지·시크릿은 환경 변수, 나머지는 config.toml에서 읽는다.
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

STAGES = ("local", "dev", "prod")
CONFIG = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class Settings:
    stage: str
    cfg: dict
    db_url: str | None
    qdrant_url: str | None
    qdrant_key: str | None
    telegram_token: str | None
    openai_key: str | None
    buffer_dir: Path

    @property
    def enabled(self) -> bool:
        return self.stage in STAGES and bool(self.cfg["stages"][self.stage]["enabled"])

    @property
    def drill_allowed(self) -> bool:
        return self.stage in STAGES and bool(self.cfg["stages"][self.stage]["drill"])

    def feature(self, name: str) -> bool:
        return self.enabled and bool(self.cfg["stages"][self.stage]["features"].get(name, False))

    def section(self, name: str) -> dict:
        return self.cfg.get(name, {})

    @property
    def policy(self) -> dict[str, dict]:
        return self.cfg.get("policy", {})


def load(env=os.environ, path: Path = CONFIG) -> Settings:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    return Settings(
        stage=env.get("APP_STAGE", "").strip().lower(),
        cfg=cfg,
        db_url=env.get("AI_DB_URL"),
        qdrant_url=env.get("AI_QDRANT_URL"),
        qdrant_key=env.get("AI_QDRANT_API_KEY"),
        telegram_token=env.get("TELEGRAM_BOT_TOKEN"),
        openai_key=env.get("OPENAI_API_KEY_MONITOR") or env.get("OPENAI_API_KEY_1") or env.get("OPENAI_API_KEY"),
        buffer_dir=Path(env.get("SERVER_BOT_DIR", "/tmp/server_bot")),
    )
