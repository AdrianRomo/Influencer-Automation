import ipaddress
import re
from urllib.parse import urlparse

import httpx
import trafilatura

HEADERS = {"User-Agent": "mvp-med-audio/0.1 (+https://example.local)"}

# ── SSRF guard ────────────────────────────────────────────────────────────────

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_article_url(url: str) -> None:
    """Reject schemes and addresses that could enable SSRF."""
    try:
        p = urlparse(url)
    except Exception:
        raise ValueError("Malformed URL")
    if p.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")
    host = p.hostname or ""
    if not host:
        raise ValueError("URL must have a hostname")
    # Block bare hostnames without dots (e.g. "redis", "db", "worker")
    if "." not in host and ":" not in host:
        raise ValueError("Bare hostnames are not allowed")
    try:
        addr = ipaddress.ip_address(host)
        for net in _PRIVATE_RANGES:
            if addr in net:
                raise ValueError("Private/loopback addresses are not allowed")
    except ValueError as exc:
        if "Private" in str(exc) or "loopback" in str(exc) or "Bare" in str(exc):
            raise
        # Not an IP address — hostname-based URLs are allowed

_MIN_WORDS = 120   # below this, extraction likely failed (tweak)
_MAX_CHARS = 20000 # cap so you don’t feed huge junk to the summarizer

def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()

def _good_enough(text: str) -> bool:
    if not text:
        return False
    words = len(text.split())
    return words >= _MIN_WORDS

def extract_article_text(url: str, fallback_text: str | None = None) -> str:
    _validate_article_url(url)
    # 1) First: trafilatura direct fetch (fast path)
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if text:
                text = _clean(text)[:_MAX_CHARS]
                if _good_enough(text):
                    return text
    except Exception:
        pass

    # 2) Second: manual fetch with content-type detection
    try:
        with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()

            # PDFs or other non-HTML: fallback to RSS summary for now
            if "application/pdf" in ctype:
                return _clean(fallback_text or "")

            text = trafilatura.extract(
                r.text,
                url=str(r.url),
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if text:
                text = _clean(text)[:_MAX_CHARS]
                if _good_enough(text):
                    return text
    except Exception:
        pass

    # 3) Final fallback
    return _clean(fallback_text or "")
