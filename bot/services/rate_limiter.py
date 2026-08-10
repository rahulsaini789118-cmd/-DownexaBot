"""
Simple sliding-window rate limiter backed by the database, plus an
in-memory concurrency guard for active downloads per user.
"""
from bot.config import settings
from bot.database import db

_active_downloads_per_user: dict[int, int] = {}


async def is_rate_limited(user_id: int) -> tuple[bool, int]:
    """Returns (is_limited, current_count_in_window)."""
    count = await db.count_recent_requests(user_id, settings.rate_limit_window_seconds)
    return count >= settings.rate_limit_max_requests, count


async def record_request(user_id: int):
    await db.record_request(user_id)


def has_active_download(user_id: int) -> bool:
    return _active_downloads_per_user.get(user_id, 0) > 0


def mark_download_start(user_id: int):
    _active_downloads_per_user[user_id] = _active_downloads_per_user.get(user_id, 0) + 1


def mark_download_end(user_id: int):
    if user_id in _active_downloads_per_user:
        _active_downloads_per_user[user_id] = max(0, _active_downloads_per_user[user_id] - 1)
        if _active_downloads_per_user[user_id] == 0:
            del _active_downloads_per_user[user_id]
