"""Product page URL ingestion.

Scrapes one product per URL. Prefers schema.org ``Product`` JSON-LD (which most
e-commerce platforms emit), falls back to OpenGraph tags, then to trafilatura
metadata + body text. Reuses the SSRF guard from :mod:`app.extract`.
"""
from __future__ import annotations

import json
import re
from typing import Iterator, Optional

import httpx

from app.extract import HEADERS, _validate_article_url, extract_article_text
from app.ingest.base import IngestAdapter, NormalizedProduct, clean_text, parse_price

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)


def _iter_jsonld_objects(html: str) -> Iterator[dict]:
    """Yield every JSON object found in <script type=ld+json> blocks, flattening @graph."""
    for block in _JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            if isinstance(obj.get("@graph"), list):
                for node in obj["@graph"]:
                    if isinstance(node, dict):
                        yield node
            else:
                yield obj


def _is_product(obj: dict) -> bool:
    t = obj.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == "product" for x in t)
    return str(t).lower() == "product"


def _from_jsonld(html: str) -> Optional[dict]:
    """Extract product fields from the first schema.org Product object, if any."""
    for obj in _iter_jsonld_objects(html):
        if not _is_product(obj):
            continue
        offers = obj.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        image = obj.get("image")
        if isinstance(image, dict):
            image = image.get("url")
        if isinstance(image, list):
            images = [str(i.get("url") if isinstance(i, dict) else i) for i in image]
        elif image:
            images = [str(image)]
        else:
            images = []
        return {
            "title": obj.get("name"),
            "description": obj.get("description"),
            "external_id": obj.get("sku") or obj.get("productID") or obj.get("mpn"),
            "category": obj.get("category"),
            "price": (offers or {}).get("price"),
            "currency": (offers or {}).get("priceCurrency"),
            "image_urls": images,
        }
    return None


def _from_og(html: str) -> dict:
    """Extract OpenGraph / product meta tags as a fallback."""
    tags = {k.lower(): v for k, v in _META_RE.findall(html)}
    images = [tags[k] for k in ("og:image", "og:image:url") if tags.get(k)]
    return {
        "title": tags.get("og:title") or tags.get("twitter:title"),
        "description": tags.get("og:description") or tags.get("description"),
        "price": tags.get("product:price:amount") or tags.get("og:price:amount"),
        "currency": tags.get("product:price:currency") or tags.get("og:price:currency"),
        "image_urls": images,
    }


class UrlIngestAdapter(IngestAdapter):
    source_type = "url"

    def __init__(self, urls: list[str], timeout: float = 20.0) -> None:
        super().__init__()
        self._urls = urls
        self._timeout = timeout

    def _scrape_one(self, url: str) -> Optional[NormalizedProduct]:
        _validate_article_url(url)  # raises on SSRF / bad scheme
        try:
            with httpx.Client(headers=HEADERS, timeout=self._timeout, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            self.errors.append(f"{url}: fetch failed ({exc!r}).")
            return None

        data = _from_jsonld(html) or {}
        og = _from_og(html)
        # JSON-LD wins; OG fills gaps.
        for key, value in og.items():
            if not data.get(key):
                data[key] = value

        title = clean_text(data.get("title"))
        description = clean_text(data.get("description"))

        # Last resort: pull body text so analysis still has something to work with.
        if not description:
            description = clean_text(extract_article_text(url))

        if not title:
            self.errors.append(f"{url}: could not determine a product title.")
            return None

        price, parsed_currency = parse_price(data.get("price"))
        currency = clean_text(data.get("currency")) or parsed_currency
        images = [str(i) for i in (data.get("image_urls") or []) if i]

        return NormalizedProduct(
            title=title,
            external_id=clean_text(data.get("external_id")),
            url=url,
            description=description,
            price=price,
            currency=currency,
            category=clean_text(data.get("category")),
            image_urls=images,
        )

    def fetch(self) -> Iterator[NormalizedProduct]:
        for url in self._urls:
            url = (url or "").strip()
            if not url:
                continue
            product = self._scrape_one(url)
            if product is not None:
                yield product
