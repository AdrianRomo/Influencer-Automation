"""RSS ingestion, conformed to the adapter interface.

This wraps the existing RSS flow (still parsed with feedparser, the same way
``tasks.py`` does it) so the article pipeline now rides the same seam as the
catalog pipeline. Each feed entry becomes a NormalizedProduct whose ``url`` +
``description`` feed the existing extraction/script stages unchanged.

It is intentionally non-invasive: ``tasks.py`` is not modified yet. This adapter
is the foundation that a later refactor can route the RSS task through.
"""
from __future__ import annotations

from typing import Iterator, Optional

import feedparser

from app.ingest.base import IngestAdapter, NormalizedProduct, clean_text


def _entry_link(entry) -> Optional[str]:
    link = entry.get("link")
    if link:
        return link
    links = entry.get("links") or []
    for item in links:
        if isinstance(item, dict) and item.get("href"):
            return item["href"]
    return None


def _entry_summary(entry) -> Optional[str]:
    # feedparser exposes summary / description / content[].value
    if entry.get("summary"):
        return entry["summary"]
    content = entry.get("content") or []
    if content and isinstance(content, list):
        first = content[0]
        if isinstance(first, dict):
            return first.get("value")
    return entry.get("description")


class RssIngestAdapter(IngestAdapter):
    source_type = "rss"

    def __init__(self, rss_url: str, limit: Optional[int] = None) -> None:
        super().__init__()
        self._rss_url = rss_url
        self._limit = limit

    def fetch(self) -> Iterator[NormalizedProduct]:
        feed = feedparser.parse(self._rss_url)
        if getattr(feed, "bozo", 0) and not feed.entries:
            self.errors.append(f"{self._rss_url}: feed could not be parsed.")
            return

        entries = feed.entries[: self._limit] if self._limit else feed.entries
        for entry in entries:
            title = clean_text(entry.get("title"))
            if not title:
                continue
            yield NormalizedProduct(
                title=title,
                url=_entry_link(entry),
                external_id=entry.get("id") or _entry_link(entry),
                description=clean_text(_entry_summary(entry)),
                attributes={"published": entry.get("published")},
            )
