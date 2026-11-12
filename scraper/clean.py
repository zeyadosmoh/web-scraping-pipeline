"""Clean and validate raw scraped rows.

Valid rows become Product objects. Invalid rows are not dropped silently: each one is
recorded as a Rejection with a reason (the same idea as P5's Import_Log).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Literal

from scraper.books import RawBook

Source = Literal["live", "simulation"]

RATING_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_PRICE_RE = re.compile(r"^£?\s*(\d+(?:\.\d{1,2})?)$")
_STOCK_RE = re.compile(r"^in stock\s*\((\d+)\s+available\)$", re.I)
_UPC_RE = re.compile(r"^[0-9a-zA-Z]{6,32}$")


class CleanError(ValueError):
    """A field failed validation; the message is the reason shown in the log."""


@dataclass(frozen=True)
class Product:
    upc: str
    title: str
    category: str
    price_gbp: Decimal
    stock: int
    rating: int
    url: str
    source: Source
    scraped_at: datetime  # aware, UTC


@dataclass(frozen=True)
class Rejection:
    url: str
    upc: str | None
    field: str
    value: str | None
    reason: str


def clean_text(value: str | None) -> str:
    """Trim and collapse internal whitespace (including NBSP). None becomes ''."""
    return " ".join((value or "").replace("\xa0", " ").split())


def parse_price(text: str | None) -> Decimal:
    t = clean_text(text)
    if not t:
        raise CleanError("price missing")
    if "Â" in t:
        raise CleanError("mojibake in price (page decoded with the wrong encoding)")
    m = _PRICE_RE.match(t)
    if not m:
        raise CleanError("price not in £0.00 format")
    try:
        value = Decimal(m.group(1)).quantize(Decimal("0.01"))
    except InvalidOperation:  # pragma: no cover - regex already guards this
        raise CleanError("price not a number")
    if value <= 0:
        raise CleanError("price must be greater than 0")
    return value


def parse_stock(text: str | None) -> int:
    t = clean_text(text)
    if not t:
        raise CleanError("availability missing")
    if t.lower() == "out of stock":
        return 0
    m = _STOCK_RE.match(t)
    if not m:
        raise CleanError("availability has no stock count")
    return int(m.group(1))


def parse_rating(word: str | None) -> int:
    t = clean_text(word).lower()
    if t not in RATING_WORDS:
        raise CleanError("rating word not One..Five")
    return RATING_WORDS[t]


def clean_book(raw: RawBook, source: Source, scraped_at: datetime) -> Product:
    """Validate one row. Raises CleanError carrying the first failing field."""
    if scraped_at.tzinfo is None:
        raise ValueError("scraped_at must be timezone-aware UTC")

    def field(name: str, fn, value):
        try:
            return fn(value)
        except CleanError as exc:
            exc.field, exc.value = name, value
            raise

    def required_text(v):
        t = clean_text(v)
        if not t:
            raise CleanError("empty")
        return t

    def upc_check(v):
        t = clean_text(v)
        if not _UPC_RE.match(t):
            raise CleanError("UPC missing or malformed")
        return t

    return Product(
        upc=field("upc", upc_check, raw.upc),
        title=field("title", required_text, raw.title),
        category=field("category", required_text, raw.category),
        price_gbp=field("price", parse_price, raw.price_text),
        stock=field("stock", parse_stock, raw.availability_text),
        rating=field("rating", parse_rating, raw.rating_word),
        url=raw.url,
        source=source,
        scraped_at=scraped_at,
    )


def clean_books(raws: Iterable[RawBook], source: Source, scraped_at: datetime
                ) -> tuple[list[Product], list[Rejection]]:
    """Clean many rows, dedupe by UPC (the first one wins), and collect rejections."""
    products: list[Product] = []
    rejections: list[Rejection] = []
    seen: dict[str, str] = {}
    for raw in raws:
        try:
            p = clean_book(raw, source, scraped_at)
        except CleanError as exc:
            rejections.append(Rejection(raw.url, clean_text(raw.upc) or None,
                                        getattr(exc, "field", "?"), getattr(exc, "value", None), str(exc)))
            continue
        if p.upc in seen:
            rejections.append(Rejection(raw.url, p.upc, "upc", p.upc,
                                        f"duplicate UPC (first seen at {seen[p.upc]})"))
            continue
        seen[p.upc] = p.url
        products.append(p)
    return products, rejections
