"""Ingestion adapter layer.

Every content source — a CSV upload, a product page URL, a Shopify store, an RSS
feed — is normalized into a stream of :class:`NormalizedProduct` records by an
:class:`IngestAdapter`. Downstream, a single upsert path turns those records into
``products`` rows, and the existing generation engine treats each product as a
"subject" exactly the way it already treats an article.

This is the one architectural seam that lets the article→video tool and the new
catalog→ad product share a single pipeline: pluggable front, single engine.

Adding a new source = writing one adapter subclass. Nothing downstream changes.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional

# Common currency symbols → ISO code, for price parsing.
_CURRENCY_SYMBOLS = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "MX$": "MXN",
    "R$": "BRL",
}

_PRICE_RE = re.compile(r"[-+]?\d[\d.,]*")


def clean_text(text: Optional[str], max_chars: int = 8000) -> Optional[str]:
    """Collapse whitespace and cap length. Returns None for empty input."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:max_chars]


def parse_price(raw) -> tuple[Optional[float], Optional[str]]:
    """Best-effort parse of a price field into (amount, currency_iso).

    Handles ``"$19.99"``, ``"19,99 €"``, ``"USD 19.99"``, plain numbers, and
    None. Currency is inferred from a leading/trailing symbol or 3-letter code.
    """
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, (int, float)):
        return float(raw), None

    s = str(raw).strip()
    currency: Optional[str] = None

    # ISO code form, e.g. "USD 19.99" / "19.99 EUR"
    code_match = re.search(r"\b([A-Z]{3})\b", s)
    if code_match:
        currency = code_match.group(1)

    # Symbol form
    if currency is None:
        for sym, code in _CURRENCY_SYMBOLS.items():
            if sym in s:
                currency = code
                break

    num_match = _PRICE_RE.search(s)
    if not num_match:
        return None, currency

    num = num_match.group(0)
    # Normalize decimal separators: treat the last , or . as the decimal point.
    if "," in num and "." in num:
        # Whichever comes last is the decimal separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        # "19,99" → decimal; "1,999" → thousands. Decide by digit grouping.
        if re.match(r"^\d{1,3}(,\d{3})+$", num):
            num = num.replace(",", "")
        else:
            num = num.replace(",", ".")

    try:
        return float(num), currency
    except ValueError:
        return None, currency


@dataclass
class NormalizedProduct:
    """Source-agnostic product record produced by every adapter."""

    title: str
    external_id: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    category: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)

    def dedupe_key(self) -> str:
        """Stable key for upsert. Mirrors the article ``(source, url)`` pattern."""
        return self.external_id or self.url or self.title

    @property
    def primary_image_url(self) -> Optional[str]:
        return self.image_urls[0] if self.image_urls else None

    def is_valid(self) -> bool:
        """Minimum bar to be a usable product: a non-empty title."""
        return bool(self.title and self.title.strip())


class IngestAdapter(ABC):
    """Base class for all ingestion sources.

    Subclasses set ``source_type`` and implement :meth:`fetch`. They may append
    human-readable problems to :attr:`errors` (used to build the import report)
    without aborting the whole batch — one bad row shouldn't sink the upload.
    """

    source_type: str = "base"

    def __init__(self) -> None:
        self.errors: list[str] = []

    @abstractmethod
    def fetch(self) -> Iterator[NormalizedProduct]:
        """Yield normalized products. Implementations should be lazy where possible."""
        raise NotImplementedError

    def collect(self) -> list[NormalizedProduct]:
        """Eagerly drain :meth:`fetch`, skipping invalid records (logged to errors)."""
        out: list[NormalizedProduct] = []
        for product in self.fetch():
            if product.is_valid():
                out.append(product)
            else:
                self.errors.append(f"Skipped record with no title: {product!r}")
        return out
