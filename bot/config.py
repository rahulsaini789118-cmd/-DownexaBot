"""
Centralized configuration loaded from environment variables.
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_id: int = _get_int("ADMIN_ID", 0)
    extra_admin_ids: tuple = tuple(
        int(x) for x in os.getenv("EXTRA_ADMIN_IDS", "").split(",") if x.strip().isdigit()
    )

    database_path: str = os.getenv("DATABASE_PATH", "data/mediafetch.db")

    max_file_size_mb: int = _get_int("MAX_FILE_SIZE_MB", 50)
    download_timeout_seconds: int = _get_int("DOWNLOAD_TIMEOUT_SECONDS", 120)
    max_concurrent_downloads: int = _get_int("MAX_CONCURRENT_DOWNLOADS", 3)
    downloads_dir: str = os.getenv("DOWNLOADS_DIR", "downloads")

    rate_limit_max_requests: int = _get_int("RATE_LIMIT_MAX_REQUESTS", 5)
    rate_limit_window_seconds: int = _get_int("RATE_LIMIT_WINDOW_SECONDS", 60)

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "logs/mediafetch.log")

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    def all_admin_ids(self):
        ids = set(self.extra_admin_ids)
        if self.admin_id:
            ids.add(self.admin_id)
        return ids

    def validate(self):
        errors = []
        if not self.bot_token:
            errors.append("BOT_TOKEN is not set in environment/.env")
        if not self.admin_id:
            errors.append("ADMIN_ID is not set in environment/.env")
        if errors:
            raise RuntimeError("Configuration error(s):\n" + "\n".join(f"- {e}" for e in errors))


settings = Settings()

# Default supported platforms; can be overridden at runtime through admin panel
# and are persisted in the database (see database.get_setting/set_setting).
DEFAULT_SUPPORTED_PLATFORMS = {
    "youtube": True,
    "instagram": True,
    "telegram": True,
    "direct": True,
              }
