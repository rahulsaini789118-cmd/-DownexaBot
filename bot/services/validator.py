"""
URL validation, platform detection, and SSRF-risk reduction.
"""
import ipaddress
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import validators

ALLOWED_SCHEMES = {"http", "https"}

PLATFORM_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"),
    "instagram": ("instagram.com", "www.instagram.com"),
    "telegram": ("t.me", "telegram.me", "telegram.org"),
}

DIRECT_MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".mp4", ".mov", ".mkv", ".webm", ".avi",
    ".mp3", ".wav", ".ogg", ".m4a",
)


@dataclass
class URLAnalysis:
    is_valid: bool
    platform: Optional[str] = None
    reason: Optional[str] = None
    normalized_url: Optional[str] = None


def _is_private_or_reserved(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def resolves_to_public_ip(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        if _is_private_or_reserved(addr):
            return False
    return True


def detect_platform(hostname: str, path: str) -> str:
    hostname = hostname.lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(hostname == d or hostname.endswith("." + d) for d in domains):
            return platform
    if any(path.lower().endswith(ext) for ext in DIRECT_MEDIA_EXTENSIONS):
        return "direct"
    return "unsupported"


def analyze_url(raw_url: str) -> URLAnalysis:
    raw_url = raw_url.strip()

    if not validators.url(raw_url):
        return URLAnalysis(is_valid=False, reason="That doesn't look like a valid URL.")

    parsed = urlparse(raw_url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        return URLAnalysis(is_valid=False, reason="Only http/https URLs are supported.")

    if not parsed.hostname:
        return URLAnalysis(is_valid=False, reason="URL has no valid host.")

    hostname = parsed.hostname.lower()
    if hostname in ("localhost",) or hostname.endswith(".local"):
        return URLAnalysis(is_valid=False, reason="Local/internal addresses are not allowed.")

    try:
        ipaddress.ip_address(hostname)
        if _is_private_or_reserved(hostname):
            return URLAnalysis(is_valid=False, reason="Private/internal IP addresses are not allowed.")
    except ValueError:
        pass

    if not resolves_to_public_ip(hostname):
        return URLAnalysis(is_valid=False, reason="This host could not be safely resolved to a public address.")

    platform = detect_platform(hostname, parsed.path or "")
    if platform == "unsupported":
        return URLAnalysis(
            is_valid=False,
            reason="This source isn't supported. Send a YouTube, Instagram, Telegram, or direct media URL.",
        )

    return URLAnalysis(is_valid=True, platform=platform, normalized_url=raw_url)
