"""Change detection and live/simulation isolation (no network, no DB)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scraper.changes import (HistoryRecord, detect_changes, latest_state, run_changes,
                             select_for_report)
from scraper.clean import Product

T0 = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=1)
D = Decimal


def prod(upc, price, stock, source="live", at=T1):
    return Product(upc=upc, title=f"Book {upc}", category="Poetry", price_gbp=D(price), stock=stock,
                   rating=3, url=f"https://books.toscrape.com/{upc}", source=source, scraped_at=at)


def hist(upc, price, stock, source="live", at=T0):
    return HistoryRecord(upc, D(price), stock, source, at)


# ---- detection ----------------------------------------------------------------
def test_first_run_everything_is_new_and_gets_history():
    changes, rows = run_changes([prod("a", "10.00", 5), prod("b", "20.00", 1)], history=[])
    assert [c.change_type for c in changes] == ["new", "new"]
    assert [(r.upc, r.price_gbp, r.stock) for r in rows] == [("a", D("10.00"), 5), ("b", D("20.00"), 1)]


def test_unchanged_rows_produce_no_history():
    changes, rows = run_changes([prod("a", "10.00", 5)], history=[hist("a", "10.00", 5)])
    assert changes == [] and rows == []


@pytest.mark.parametrize("price,stock,kind", [
    ("12.50", 5, "price"), ("10.00", 3, "stock"), ("9.99", 0, "price+stock"),
])
def test_change_types(price, stock, kind):
    changes, rows = run_changes([prod("a", price, stock)], history=[hist("a", "10.00", 5)])
    assert len(changes) == 1 and len(rows) == 1
    c = changes[0]
    assert c.change_type == kind
    assert (c.old_price, c.old_stock, c.new_price, c.new_stock) == (D("10.00"), 5, D(price), stock)
    assert c.price_delta == D(price) - D("10.00")


def test_latest_state_uses_most_recent_record():
    h = [hist("a", "10.00", 5, at=T0), hist("a", "11.00", 4, at=T0 + timedelta(hours=1))]
    assert latest_state(h, "live") == {"a": (D("11.00"), 4)}


def test_detect_changes_is_pure_against_snapshot():
    changes = detect_changes([prod("a", "10.00", 5), prod("b", "1.00", 1)], {"a": (D("10.00"), 5)})
    assert [(c.upc, c.change_type) for c in changes] == [("b", "new")]


# ---- simulation isolation -------------------------------------------------------
def test_simulation_history_never_affects_live_baseline():
    history = [hist("a", "10.00", 5, "live"), hist("a", "99.00", 0, "simulation", at=T0 + timedelta(hours=2))]
    assert latest_state(history, "live") == {"a": (D("10.00"), 5)}
    changes, rows = run_changes([prod("a", "10.00", 5, "live")], history)
    assert changes == [] and rows == []  # the newer simulated record did not count


def test_simulation_compares_only_with_simulation_baseline():
    history = [hist("a", "10.00", 5, "live"), hist("a", "10.00", 5, "simulation")]
    changes, rows = run_changes([prod("a", "7.50", 2, "simulation")], history)
    assert [(c.change_type, c.source) for c in changes] == [("price+stock", "simulation")]
    assert all(r.source == "simulation" for r in rows)


def test_a_run_cannot_mix_sources():
    with pytest.raises(ValueError, match="mix sources"):
        run_changes([prod("a", "1.00", 1, "live"), prod("b", "1.00", 1, "simulation")], [])


def test_reports_are_live_only_by_default():
    products = [prod("a", "10.00", 5, "live"), prod("b", "20.00", 0, "live"),
                prod("s", "999.00", 0, "simulation")]
    report = select_for_report(products)
    assert {p.source for p in report} == {"live"}
    # live numbers are unaffected by the simulated row
    assert sum(p.price_gbp for p in report) / len(report) == D("15.00")
    assert sum(1 for p in report if p.stock == 0) == 1
    assert len(select_for_report(products, include_simulation=True)) == 3


def test_changes_report_is_live_only_by_default():
    live_changes, _ = run_changes([prod("a", "11.00", 5, "live")], [hist("a", "10.00", 5, "live")])
    sim_changes, _ = run_changes([prod("a", "5.00", 5, "simulation")], [hist("a", "10.00", 5, "simulation")])
    combined = live_changes + sim_changes
    assert [c.source for c in select_for_report(combined)] == ["live"]
