"""Report data model and Summary calculations (pure, no I/O).

The same ReportData feeds Excel and CSV, whether it comes from PostgreSQL or from memory.
Averages are exact Decimals. Excel shows 2 decimals but the cell keeps the full value.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from scraper.changes import Change
from scraper.clean import Source


@dataclass(frozen=True)
class ReportProduct:
    upc: str
    title: str
    category: str
    price_gbp: Decimal
    stock: int
    rating: int
    url: str
    source: Source
    first_seen_at: datetime
    last_seen_at: datetime
    removed_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.removed_at is None


@dataclass(frozen=True)
class LogRow:
    """One Import_Log line: a rejected row or a failed or skipped URL."""
    logged_at: datetime
    url: str
    upc: str | None
    field: str
    value: str | None
    reason: str
    source: Source


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    source: Source
    started_at: datetime
    finished_at: datetime
    limit: int | None
    complete: bool
    resumed: bool
    rows_scraped: int
    rows_valid: int
    rows_rejected: int
    fetch_failures: int

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class CategoryStats:
    category: str
    products: int
    avg_price: Decimal
    min_price: Decimal
    max_price: Decimal
    out_of_stock: int
    units_in_stock: int


@dataclass(frozen=True)
class Summary:
    active_products: int
    removed_products: int
    in_stock: int
    out_of_stock: int
    units_in_stock: int
    avg_price: Decimal | None
    min_price: Decimal | None
    max_price: Decimal | None
    categories: int
    rating_counts: dict[int, int]
    by_category: list[CategoryStats]
    changes_by_type: dict[str, int]


@dataclass
class ReportData:
    source: Source
    run: RunInfo | None
    products: list[ReportProduct]
    changes: list[Change]
    log: list[LogRow]
    generated_at: datetime
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        mixed = {r.source for r in [*self.products, *self.changes, *self.log]} - {self.source}
        if mixed:
            raise ValueError(f"{self.source} report contains rows from other sources: {sorted(mixed)}")

    @property
    def is_simulation(self) -> bool:
        return self.source == "simulation"


def _avg(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def summarize(data: ReportData) -> Summary:
    active = [p for p in data.products if p.active]
    groups: dict[str, list[ReportProduct]] = defaultdict(list)
    for p in active:
        groups[p.category].append(p)
    by_cat = [CategoryStats(cat, len(ps), _avg([p.price_gbp for p in ps]),
                            min(p.price_gbp for p in ps), max(p.price_gbp for p in ps),
                            sum(1 for p in ps if p.stock == 0), sum(p.stock for p in ps))
              for cat, ps in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    prices = [p.price_gbp for p in active]
    ratings = Counter(p.rating for p in active)
    return Summary(
        active_products=len(active),
        removed_products=sum(1 for p in data.products if not p.active),
        in_stock=sum(1 for p in active if p.stock > 0),
        out_of_stock=sum(1 for p in active if p.stock == 0),
        units_in_stock=sum(p.stock for p in active),
        avg_price=_avg(prices),
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        categories=len(groups),
        rating_counts={r: ratings.get(r, 0) for r in range(1, 6)},
        by_category=by_cat,
        changes_by_type=dict(Counter(c.change_type for c in data.changes)),
    )
