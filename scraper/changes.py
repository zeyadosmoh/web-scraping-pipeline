"""Change detection between runs, with strict live/simulation isolation.

Rules:
- Each source has its own baseline. A live run is compared only with earlier *live*
  history, and a simulation run only with earlier *simulation* history.
- A history row is written only for a new product or a changed price or stock.
- Reports include only live rows by default (`select_for_report`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Literal, Protocol, TypeVar

from scraper.clean import Product, Source

ChangeType = Literal["new", "price", "stock", "price+stock"]


@dataclass(frozen=True)
class HistoryRecord:
    """One row of price_stock_history."""
    upc: str
    price_gbp: Decimal
    stock: int
    source: Source
    recorded_at: datetime  # aware UTC


@dataclass(frozen=True)
class Change:
    upc: str
    title: str
    category: str
    change_type: ChangeType
    old_price: Decimal | None
    new_price: Decimal
    old_stock: int | None
    new_stock: int
    source: Source
    detected_at: datetime

    @property
    def price_delta(self) -> Decimal | None:
        return None if self.old_price is None else self.new_price - self.old_price


Snapshot = dict[str, tuple[Decimal, int]]  # upc -> (price, stock)


class _HasSource(Protocol):
    source: Source


T = TypeVar("T", bound=_HasSource)


def filter_source(items: Iterable[T], source: Source) -> list[T]:
    return [i for i in items if i.source == source]


def select_for_report(items: Iterable[T], include_simulation: bool = False) -> list[T]:
    """Rows that go into a report: live only, unless simulation was explicitly asked for."""
    items = list(items)
    return items if include_simulation else filter_source(items, "live")


def latest_state(history: Iterable[HistoryRecord], source: Source) -> Snapshot:
    """Latest (price, stock) per UPC, built from history of one source only."""
    latest: dict[str, HistoryRecord] = {}
    for rec in filter_source(history, source):
        cur = latest.get(rec.upc)
        if cur is None or rec.recorded_at >= cur.recorded_at:
            latest[rec.upc] = rec
    return {upc: (r.price_gbp, r.stock) for upc, r in latest.items()}


def _single_source(products: list[Product]) -> Source | None:
    sources = {p.source for p in products}
    if len(sources) > 1:
        raise ValueError(f"a run must not mix sources: {sorted(sources)}")
    return next(iter(sources), None)


def detect_changes(products: Iterable[Product], previous: Snapshot) -> list[Change]:
    """Compare this run's products with the previous snapshot of the same source."""
    products = list(products)
    _single_source(products)
    changes: list[Change] = []
    for p in products:
        old = previous.get(p.upc)
        if old is None:
            kind: ChangeType = "new"
            old_price = old_stock = None
        else:
            old_price, old_stock = old
            price_changed, stock_changed = p.price_gbp != old_price, p.stock != old_stock
            if not (price_changed or stock_changed):
                continue
            kind = "price+stock" if price_changed and stock_changed else ("price" if price_changed else "stock")
        changes.append(Change(p.upc, p.title, p.category, kind, old_price, p.price_gbp,
                              old_stock, p.stock, p.source, p.scraped_at))
    return changes


def history_rows_to_insert(changes: Iterable[Change]) -> list[HistoryRecord]:
    """One history row per change (new product or changed price/stock). Unchanged rows get none."""
    return [HistoryRecord(c.upc, c.new_price, c.new_stock, c.source, c.detected_at) for c in changes]


def run_changes(products: Iterable[Product], history: Iterable[HistoryRecord]
                ) -> tuple[list[Change], list[HistoryRecord]]:
    """Convenience: baseline from the run's own source -> changes -> history rows to insert."""
    products = list(products)
    source = _single_source(products)
    if source is None:
        return [], []
    changes = detect_changes(products, latest_state(history, source))
    return changes, history_rows_to_insert(changes)
