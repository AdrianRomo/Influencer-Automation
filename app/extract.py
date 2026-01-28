import re
import httpx
import trafilatura

HEADERS = {"User-Agent": "mvp-med-audio/0.1 (+https://example.local)"}

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
