"""
Database layer using aiosqlite.

Design notes:
- All access goes through this module's functions (no raw SQL elsewhere),
  so swapping SQLite for PostgreSQL later only requires rewriting this
  file's internals (e.g. with asyncpg), not the handlers that call it.
- Placeholders use "?" (SQLite style). If migrating to PostgreSQL with
  asyncpg, change placeholders to "$1, $2..." and swap the connection layer.
"""
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import aiosqlite

from bot.config import settings, DEFAULT_SUPPORTED_PLATFORMS

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at INTEGER NOT NULL,
    last_active_at INTEGER NOT NULL,
    is_blocked INTEGER NOT NULL DEFAULT 0,
    downloads_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    platform TEXT,
    media_type TEXT,
    status TEXT NOT NULL,      -- success | failed | rejected
    error_reason TEXT,
    file_size_bytes INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (user_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_limit_log (
    user_id INTEGER NOT NULL,
    ts REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_rate_limit_user ON rate_limit_log(user_id);
"""


class Database:
    def __init__(self, path: str = settings.database_path):
        self.path = path

    async def init(self):
        db_dir = os.path.dirname(self.path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
            # Seed default settings if not present
            for key in ("max_file_size_mb",):
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(settings.max_file_size_mb)),
                )
            for platform, enabled in DEFAULT_SUPPORTED_PLATFORMS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (f"platform_{platform}", "1" if enabled else "0"),
                )
            await db.commit()

    @asynccontextmanager
    async def connect(self):
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    # ---------- Users ----------

    async def upsert_user(self, user_id: int, username: Optional[str], first_name: Optional[str]):
        now = int(time.time())
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, first_name, joined_at, last_active_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_active_at=excluded.last_active_at
                """,
                (user_id, username, first_name, now, now),
            )
            await db.commit()

    async def is_blocked(self, user_id: int) -> bool:
        async with self.connect() as db:
            cur = await db.execute("SELECT is_blocked FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return bool(row["is_blocked"]) if row else False

    async def set_blocked(self, user_id: int, blocked: bool):
        async with self.connect() as db:
            await db.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))
            await db.commit()

    async def all_user_ids(self):
        async with self.connect() as db:
            cur = await db.execute("SELECT user_id FROM users")
            rows = await cur.fetchall()
            return [r["user_id"] for r in rows]

    async def total_users(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM users")
            row = await cur.fetchone()
            return row["c"]

    async def active_users(self, since_seconds: int = 7 * 24 * 3600) -> int:
        cutoff = int(time.time()) - since_seconds
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM users WHERE last_active_at >= ?", (cutoff,))
            row = await cur.fetchone()
            return row["c"]

    # ---------- Downloads ----------

    async def log_download(
        self,
        user_id: int,
        url: str,
        platform: Optional[str],
        media_type: Optional[str],
        status: str,
        error_reason: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
    ):
        now = int(time.time())
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO downloads (user_id, url, platform, media_type, status, error_reason, file_size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, url, platform, media_type, status, error_reason, file_size_bytes, now),
            )
            if status == "success":
                await db.execute(
                    "UPDATE users SET downloads_count = downloads_count + 1 WHERE user_id=?", (user_id,)
                )
            elif status == "failed":
                await db.execute(
                    "UPDATE users SET failed_count = failed_count + 1 WHERE user_id=?", (user_id,)
                )
            await db.commit()

    async def total_downloads(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='success'")
            row = await cur.fetchone()
            return row["c"]

    async def total_failed(self) -> int:
        async with self.connect() as db:
            cur = await db.execute("SELECT COUNT(*) as c FROM downloads WHERE status='failed'")
            row = await cur.fetchone()
            return row["c"]

    async def recent_activity(self, limit: int = 10):
        async with self.connect() as db:
            cur = await db.execute(
                "SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return await cur.fetchall()

    # ---------- Settings (runtime-configurable) ----------

    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with self.connect() as db:
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            return row["value"] if row else default

    async def set_setting(self, key: str, value: str):
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            await db.commit()

    async def get_max_file_size_mb(self) -> int:
        val = await self.get_setting("max_file_size_mb", str(settings.max_file_size_mb))
        try:
            return int(val)
        except (TypeError, ValueError):
            return settings.max_file_size_mb

    async def get_enabled_platforms(self) -> dict:
        result = {}
        async with self.connect() as db:
            for platform in DEFAULT_SUPPORTED_PLATFORMS:
                cur = await db.execute("SELECT value FROM settings WHERE key=?", (f"platform_{platform}",))
                row = await cur.fetchone()
                result[platform] = bool(int(row["value"])) if row else True
        return result

    async def set_platform_enabled(self, platform: str, enabled: bool):
        await self.set_setting(f"platform_{platform}", "1" if enabled else "0")

    # ---------- Rate limiting ----------

    async def record_request(self, user_id: int):
        async with self.connect() as db:
            await db.execute("INSERT INTO rate_limit_log (user_id, ts) VALUES (?, ?)", (user_id, time.time()))
db = Database()
