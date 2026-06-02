"""Ingestion adapters: pluggable front-end for the generation engine.

    from app.ingest import get_adapter, NormalizedProduct

    adapter = get_adapter("csv", content=file_bytes)
    products = adapter.collect()
    if adapter.errors:
        ...  # surface the import report

Registered source types map to their adapter class. Phase-2 sources
(shopify, woocommerce, merchant, meta) are not registered yet — calling
``get_adapter`` for them raises a clear error.
"""
from __future__ import annotations

from app.ingest.base import IngestAdapter, NormalizedProduct, clean_text, parse_price
from app.ingest.csv_adapter import CsvIngestAdapter
from app.ingest.rss_adapter import RssIngestAdapter
from app.ingest.url_adapter import UrlIngestAdapter

_ADAPTERS: dict[str, type[IngestAdapter]] = {
    CsvIngestAdapter.source_type: CsvIngestAdapter,
    UrlIngestAdapter.source_type: UrlIngestAdapter,
    RssIngestAdapter.source_type: RssIngestAdapter,
}

# Declared-but-unbuilt sources, so the error message is helpful rather than a KeyError.
_PLANNED = {"shopify", "woocommerce", "merchant", "meta", "manual"}


def get_adapter(source_type: str, **kwargs) -> IngestAdapter:
    """Construct the adapter for ``source_type``. Extra kwargs go to its __init__."""
    cls = _ADAPTERS.get(source_type)
    if cls is None:
        if source_type in _PLANNED:
            raise NotImplementedError(
                f"Ingestion source {source_type!r} is planned but not implemented yet."
            )
        raise ValueError(
            f"Unknown ingestion source {source_type!r}. "
            f"Available: {sorted(_ADAPTERS)}."
        )
    return cls(**kwargs)


def available_sources() -> list[str]:
    return sorted(_ADAPTERS)


__all__ = [
    "IngestAdapter",
    "NormalizedProduct",
    "clean_text",
    "parse_price",
    "CsvIngestAdapter",
    "UrlIngestAdapter",
    "RssIngestAdapter",
    "get_adapter",
    "available_sources",
]
