"""
Download orchestration.

Two paths:
1. yt-dlp for YouTube / Instagram / Telegram public posts — yt-dlp itself
   refuses (or fails cleanly) on private, login-walled, DRM, or otherwise
   restricted content, and we never pass cookies/auth to it, so it can only
   ever fetch what's genuinely public.
2. Plain streamed HTTP download for direct media file URLs, with strict
   size limits enforced during the stream (not just from headers, since
   headers can lie).

Nothing in this module attempts to bypass authentication, DRM, or platform
restrictions. If a fetch fails or is blocked, it is reported as a failure,
never retried through an alternate/bypass method.
"""
import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import aiohttp
import yt_dlp

from bot.config import settings
from bot.database import db
from bot.services.validator import resolves_to_public_ip
from bot.utils.logger import logger

_download_semaphore = asyncio.Semaphore(settings.max_concurrent_downloads)


@dataclass
class DownloadResult:
    success: bool
    file_path: Optional[str] = None
    title: Optional[str] = None
    media_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    error: Optional[str] = None


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name).strip()
    return name[:100] or f"media_{uuid.uuid4().hex[:8]}"


async def _run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _ytdlp_extract_and_download(url: str, out_dir: str, max_bytes: int) -> dict:
    outtmpl = os.path.join(out_dir, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "best[filesize<{}]/best".format(max_bytes),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": settings.download_timeout_seconds,
        "max_filesize": max_bytes,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise yt_dlp.utils.DownloadError("No media info returned.")
        filepath = ydl.prepare_filename(info)
        return {
            "filepath": filepath,
            "title": info.get("title") or "media",
            "ext": info.get("ext"),
            "is_video": info.get("vcodec", "none") != "none",
        }


async def _current_max_bytes() -> int:
    mb = await db.get_max_file_size_mb()
    return mb * 1024 * 1024


async def download_via_ytdlp(url: str, out_dir: str) -> DownloadResult:
    max_bytes = await _current_max_bytes()
    try:
        async with _download_semaphore:
            info = await asyncio.wait_for(
                _run_blocking(_ytdlp_extract_and_download, url, out_dir, max_bytes),
                timeout=settings.download_timeout_seconds,
            )
    except asyncio.TimeoutError:
        return DownloadResult(success=False, error="Download timed out.")
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if any(term in msg for term in ("private", "login", "sign in", "cookies", "premieres", "drm", "paywall", "subscribers-only")):
            return DownloadResult(success=False, error="This content is private, login-protected, or restricted. It cannot be downloaded.")
        if "unavailable" in msg or "not available" in msg:
            return DownloadResult(success=False, error="This media is unavailable.")
        return DownloadResult(success=False, error="Could not download this media (unsupported or restricted).")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected yt-dlp error")
        return DownloadResult(success=False, error=f"Unexpected error while downloading: {e}")

    filepath = info["filepath"]
    if not os.path.exists(filepath):
        return DownloadResult(success=False, error="Download completed but file was not found.")

    size = os.path.getsize(filepath)
    if size > max_bytes:
        os.remove(filepath)
        return DownloadResult(success=False, error=f"File exceeds the {max_bytes // (1024*1024)}MB limit.")

    media_type = "video" if info["is_video"] else "audio"
    return DownloadResult(
        success=True,
        file_path=filepath,
        title=info["title"],
        media_type=media_type,
        file_size_bytes=size,
    )


async def download_direct_file(url: str, out_dir: str) -> DownloadResult:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not resolves_to_public_ip(parsed.hostname or ""):
        return DownloadResult(success=False, error="This host could not be safely resolved.")

    max_bytes = await _current_max_bytes()
    filename = _safe_filename(os.path.basename(parsed.path) or f"file_{uuid.uuid4().hex[:8]}")
    dest_path = os.path.join(out_dir, filename)

    timeout = aiohttp.ClientTimeout(total=settings.download_timeout_seconds)
    try:
        async with _download_semaphore:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
                    if resp.status != 200:
                        return DownloadResult(success=False, error=f"Server returned HTTP {resp.status}.")

                    content_length = resp.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        return DownloadResult(
                            success=False, error=f"File exceeds the {max_bytes // (1024*1024)}MB limit."
                        )

                    total = 0
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            total += len(chunk)
                            if total > max_bytes:
                                f.close()
                                os.remove(dest_path)
                                return DownloadResult(
                                    success=False,
                                    error=f"File exceeds the {max_bytes // (1024*1024)}MB limit.",
                                )
                            f.write(chunk)

                    content_type = resp.headers.get("Content-Type", "")
                    media_type = "video" if "video" in content_type else (
                        "image" if "image" in content_type else "audio" if "audio" in content_type else "file"
                    )

        return DownloadResult(
            success=True, file_path=dest_path, title=filename, media_type=media_type, file_size_bytes=total
        )
    except asyncio.TimeoutError:
        return DownloadResult(success=False, error="Download timed out.")
    except aiohttp.ClientError as e:
        return DownloadResult(success=False, error=f"Network error while downloading: {e}")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected direct-download error")
        return DownloadResult(success=False, error=f"Unexpected error: {e}")


async def download_media(url: str, platform: str, user_id: int) -> DownloadResult:
    out_dir = os.path.join(settings.downloads_dir, str(user_id), uuid.uuid4().hex[:8])
    os.makedirs(out_dir, exist_ok=True)

    if platform in ("youtube", "instagram", "telegram"):
        return await download_via_ytdlp(url, out_dir)
    elif platform == "direct":
        return await download_direct_file(url, out_dir)
    else:
        return DownloadResult(success=False, error="Unsupported platform.")


def cleanup_path(file_path: Optional[str]):
    if not file_path:
        return
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        parent = os.path.dirname(file_path)
        if parent and os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)
    except OSError:
        logger.warning("Failed to clean up temp file: %s", file_path)


async def periodic_cleanup(max_age_seconds: int = 3600):
    base = settings.downloads_dir
    if not os.path.isdir(base):
        return
    now = time.time()
    for root, dirs, files in os.walk(base, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                if now - os.path.getmtime(path) > max_age_seconds:
                    os.remove(path)
            except OSError:
                pass
        for d in dirs:
            dpath = os.path.join(root, d)
            try:
                if not os.listdir(dpath):
                    os.rmdir(dpath)
            except OSError:
                pass
