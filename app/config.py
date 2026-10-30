import os
from pathlib import Path
from typing import List, Optional
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def find_env_file() -> str:
    """Locate the active .env file, prioritizing ENV_FILE, /etc/anonbot/.env, or local .env."""
    explicit = os.environ.get("ANONBOT_ENV_FILE")
    if explicit and Path(explicit).is_file():
        return explicit
    etc_env = Path("/etc/anonbot/.env")
    if etc_env.is_file():
        return str(etc_env)
    local_env = Path(__file__).resolve().parent.parent / ".env"
    return str(local_env)


class Settings(BaseModel):
    bot_token: str = Field(default="")
    bot_username: str = Field(default="")
    super_admins: List[int] = Field(default_factory=list)

    hmac_secret: str = Field(default="")
    fernet_key: str = Field(default="")

    database_url: str = Field(default="sqlite+aiosqlite:///./anonbot.db")
    redis_url: Optional[str] = Field(default=None)

    run_mode: str = Field(default="polling")
    webhook_host: str = Field(default="")
    webhook_path: str = Field(default="/webhook/anonbot")
    webhook_secret: str = Field(default="")
    webapp_host: str = Field(default="127.0.0.1")
    webapp_port: int = Field(default=8080)

    proxy_enabled: bool = Field(default=False)
    proxy_url: Optional[str] = Field(default=None)
    api_base_url: Optional[str] = Field(default=None)

    rate_limit_per_minute: int = Field(default=15)
    delete_window_seconds: int = Field(default=300)
    reveal_daily_limit: int = Field(default=5)
    reveal_cooldown_seconds: int = Field(default=3600)
    auto_ban_reports_threshold: int = Field(default=3)
    auto_ban_duration_hours: int = Field(default=72)
    reaction_emojis: List[str] = Field(default_factory=lambda: ["❤️", "👍", "😂", "🔥", "😢"])
    filter_links_default: bool = Field(default=False)
    maintenance_mode: bool = Field(default=False)

    log_level: str = Field(default="INFO")
    log_dir: str = Field(default="/var/log/anonbot")
    backup_dir: str = Field(default="/var/backups/anonbot")
    env_file_path: str = Field(default="")

    @field_validator("run_mode")
    @classmethod
    def validate_run_mode(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode not in ("polling", "webhook"):
            return "polling"
        return mode

    def validate_secrets(self) -> None:
        if not self.hmac_secret or len(self.hmac_secret) < 16:
            raise ValueError("HMAC_SECRET in .env is missing or shorter than 16 characters.")
        if not self.fernet_key:
            raise ValueError("FERNET_KEY in .env is not set.")
        try:
            Fernet(self.fernet_key.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid FERNET_KEY in .env: {exc}") from exc


def load_settings(env_path: Optional[str] = None) -> Settings:
    target_env = env_path or find_env_file()
    if Path(target_env).is_file():
        load_dotenv(target_env, override=True)

    raw_admins = os.environ.get("SUPER_ADMINS", "")
    admins: List[int] = []
    for part in raw_admins.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            admins.append(int(part))

    raw_emojis = os.environ.get("REACTION_EMOJIS", "❤️,👍,😂,🔥,😢")
    emojis = [e.strip() for e in raw_emojis.split(",") if e.strip()]
    if not emojis:
        emojis = ["❤️", "👍", "😂", "🔥", "😢"]

    def parse_bool(key: str, default: bool = False) -> bool:
        val = os.environ.get(key)
        if val is None:
            return default
        return val.strip().lower() in ("1", "true", "yes", "on")

    def parse_int(key: str, default: int) -> int:
        val = os.environ.get(key, "")
        try:
            return int(val.strip())
        except ValueError:
            return default

    redis_raw = os.environ.get("REDIS_URL", "").strip()
    proxy_raw = os.environ.get("PROXY_URL", "").strip()
    api_base_raw = os.environ.get("API_BASE_URL", "").strip()

    return Settings(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        bot_username=os.environ.get("BOT_USERNAME", "").strip().lstrip("@"),
        super_admins=admins,
        hmac_secret=os.environ.get("HMAC_SECRET", "").strip(),
        fernet_key=os.environ.get("FERNET_KEY", "").strip(),
        database_url=os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./anonbot.db").strip(),
        redis_url=redis_raw if redis_raw else None,
        run_mode=os.environ.get("RUN_MODE", "polling"),
        webhook_host=os.environ.get("WEBHOOK_HOST", "").strip().rstrip("/"),
        webhook_path=os.environ.get("WEBHOOK_PATH", "/webhook/anonbot").strip(),
        webhook_secret=os.environ.get("WEBHOOK_SECRET", "").strip(),
        webapp_host=os.environ.get("WEBAPP_HOST", "127.0.0.1").strip(),
        webapp_port=parse_int("WEBAPP_PORT", 8080),
        proxy_enabled=parse_bool("PROXY_ENABLED", False),
        proxy_url=proxy_raw if proxy_raw else None,
        api_base_url=api_base_raw if api_base_raw else None,
        rate_limit_per_minute=parse_int("RATE_LIMIT_PER_MINUTE", 15),
        delete_window_seconds=parse_int("DELETE_WINDOW_SECONDS", 300),
        reveal_daily_limit=parse_int("REVEAL_DAILY_LIMIT", 5),
        reveal_cooldown_seconds=parse_int("REVEAL_COOLDOWN_SECONDS", 3600),
        auto_ban_reports_threshold=parse_int("AUTO_BAN_REPORTS_THRESHOLD", 3),
        auto_ban_duration_hours=parse_int("AUTO_BAN_DURATION_HOURS", 72),
        reaction_emojis=emojis,
        filter_links_default=parse_bool("FILTER_LINKS_DEFAULT", False),
        maintenance_mode=parse_bool("MAINTENANCE_MODE", False),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
        log_dir=os.environ.get("LOG_DIR", "/var/log/anonbot").strip(),
        backup_dir=os.environ.get("BACKUP_DIR", "/var/backups/anonbot").strip(),
        env_file_path=target_env,
    )


settings = load_settings()
