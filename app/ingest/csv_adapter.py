"""CSV catalog ingestion.

Accepts a raw CSV (string or bytes) plus an optional column map. When no map is
given, common header names are auto-detected so a clean Shopify/Woo/Merchant
export imports with zero config. Unmapped columns are preserved under
``attributes`` so nothing is silently dropped.
"""
from __future__ import annotations

import csv
import io
from typing import Iterator, Optional

from app.ingest.base import IngestAdapter, NormalizedProduct, clean_text, parse_price

# Canonical field → accepted header aliases (lowercased, stripped).
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "product", "product name", "product_title", "item"),
    "external_id": ("id", "sku", "product id", "product_id", "variant id", "handle"),
    "url": ("url", "link", "product url", "product_link", "permalink"),
    "description": ("description", "body", "body (html)", "desc", "details", "summary"),
    "price": ("price", "variant price", "cost", "amount"),
    "currency": ("currency", "currency code"),
    "category": ("category", "type", "product type", "product_category", "collection"),
    "image": ("image", "image url", "image src", "image_link", "image_url", "photo"),
}


def _auto_map(headers: list[str]) -> dict[str, str]:
    """Build {canonical_field: actual_header} from header aliases."""
    lookup = {h.strip().lower(): h for h in headers if h}
    mapping: dict[str, str] = {}
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                mapping[field_name] = lookup[alias]
                break
    return mapping


class CsvIngestAdapter(IngestAdapter):
    source_type = "csv"

    def __init__(self, content, column_map: Optional[dict[str, str]] = None) -> None:
        """``column_map`` maps canonical field -> CSV header. Omit to auto-detect."""
        super().__init__()
        if isinstance(content, bytes):
            # Tolerate a UTF-8 BOM from Excel exports.
            content = content.decode("utf-8-sig", errors="replace")
        self._content = content
        self._explicit_map = column_map

    def fetch(self) -> Iterator[NormalizedProduct]:
        reader = csv.DictReader(io.StringIO(self._content))
        if not reader.fieldnames:
            self.errors.append("CSV has no header row.")
            return

        mapping = self._explicit_map or _auto_map(list(reader.fieldnames))
        if "title" not in mapping:
            self.errors.append(
                "Could not find a product title/name column. "
                f"Headers seen: {reader.fieldnames}. Provide a column_map."
            )
            return

        mapped_headers = set(mapping.values())

        for row_num, row in enumerate(reader, start=2):  # row 1 is the header
            title = clean_text(row.get(mapping["title"]))
            if not title:
                self.errors.append(f"Row {row_num}: missing title, skipped.")
                continue

            price, parsed_currency = parse_price(row.get(mapping.get("price", "")))
            currency = clean_text(row.get(mapping.get("currency", ""))) or parsed_currency

            image = clean_text(row.get(mapping.get("image", "")))
            image_urls = [image] if image else []

            # Anything not explicitly mapped is preserved as a free-form attribute.
            attributes = {
                h: v for h, v in row.items()
                if h and h not in mapped_headers and v not in (None, "")
            }

            yield NormalizedProduct(
                title=title,
                external_id=clean_text(row.get(mapping.get("external_id", ""))),
                url=clean_text(row.get(mapping.get("url", ""))),
                description=clean_text(row.get(mapping.get("description", ""))),
                price=price,
                currency=currency,
                category=clean_text(row.get(mapping.get("category", ""))),
                image_urls=image_urls,
                attributes=attributes,
            )
