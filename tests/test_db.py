"""DB-level tests on a throwaway database (scraper_test) in the P6 container."""
from decimal import Decimal

import pytest

from scraper import db
from scraper.crawl import FixtureFetcher
from scraper.pipeline import SIMULATION_DIR, run_scrape, run_simulation
from scraper.state import Checkpoint

R1, R2 = SIMULATION_DIR / "round1", SIMULATION_DIR / "round2"
pytestmark = pytest.mark.db


def scrape(conn, root, source="live", limit=None):
    return run_scrape(conn, FixtureFetcher(root), source=source, limit=limit, checkpoint=Checkpoint.load(None))


def history_count(conn, source):
    return conn.execute("SELECT count(*) FROM price_stock_history WHERE source=%s", (source,)).fetchone()[0]


def test_first_run_then_unchanged_run_adds_no_history(pg_conn):
    first = scrape(pg_conn, R1)
    assert first.run.complete and len(first.changes) == 8 and {c.change_type for c in first.changes} == {"new"}
    assert history_count(pg_conn, "live") == 8
    second = scrape(pg_conn, R1)
    assert second.changes == [] and history_count(pg_conn, "live") == 8


def test_full_run_flags_removed_and_keeps_row(pg_conn):
    scrape(pg_conn, R1)
    out = scrape(pg_conn, R2)
    kinds = {c.upc: c.change_type for c in out.changes}
    assert sorted(kinds.values()) == ["new", "price", "price", "price+stock", "removed", "stock", "stock"]
    removed = [c for c in out.changes if c.change_type == "removed"]
    row = pg_conn.execute("SELECT removed_at FROM products WHERE source='live' AND upc=%s",
                          (removed[0].upc,)).fetchone()
    assert row is not None and row[0] is not None           # marked, never deleted
    assert [l.field for l in out.log] == ["price"]           # the invalid row is logged with a reason
    # the invalid row's product is NOT flagged removed (its UPC was still seen)
    assert out.removal_checked
    products = db.fetch_products(pg_conn)
    assert len(products) == 9 and sum(1 for p in products if not p.active) == 1


def test_limit_run_never_flags_removed(pg_conn):
    scrape(pg_conn, R1)
    out = scrape(pg_conn, R2, limit=3)
    assert not out.run.complete and not out.removal_checked
    assert all(c.change_type != "removed" for c in out.changes)
    assert pg_conn.execute("SELECT count(*) FROM products WHERE removed_at IS NOT NULL").fetchone()[0] == 0


def test_history_only_on_change(pg_conn):
    scrape(pg_conn, R1)
    scrape(pg_conn, R2)
    # 8 baseline + 5 changed + 1 new; unchanged/removed/rejected rows add nothing
    assert history_count(pg_conn, "live") == 14
    sapiens = pg_conn.execute(
        """SELECT price_gbp, stock FROM price_stock_history WHERE source='live' AND upc='4165285e1663650f'
           ORDER BY recorded_at""").fetchall()
    assert sapiens == [(Decimal("54.23"), 20), (Decimal("49.99"), 7)]


def test_db_isolates_live_and_simulation(pg_conn):
    scrape(pg_conn, R1, source="live")
    sims = run_simulation(pg_conn)
    assert len(sims[1].changes) == 7
    # the live baseline did not move: a live re-run of round1 sees no changes
    assert scrape(pg_conn, R1, source="live").changes == []
    # report reads default to live and contain no simulated rows
    assert {p.source for p in db.fetch_products(pg_conn)} == {"live"}
    assert all(p.active for p in db.fetch_products(pg_conn))
    assert len(db.fetch_products(pg_conn, "simulation")) == 9
    live_run = db.latest_run(pg_conn)
    assert live_run.source == "live" and db.fetch_changes(pg_conn, live_run.run_id) == []
    # a live run id cannot be used to read simulated changes, and vice versa
    sim_run = db.latest_run(pg_conn, "simulation")
    assert db.fetch_changes(pg_conn, sim_run.run_id, "live") == []
    # same UPC exists once per source
    n = pg_conn.execute("SELECT count(*) FROM products WHERE upc='4165285e1663650f'").fetchone()[0]
    assert n == 2


def test_reset_simulation_never_touches_live(pg_conn):
    scrape(pg_conn, R1, source="live")
    run_simulation(pg_conn)
    run_simulation(pg_conn)  # replayable
    assert history_count(pg_conn, "live") == 8
    assert len(db.fetch_products(pg_conn, "live")) == 8


def test_save_run_rejects_mixed_sources(pg_conn):
    out = scrape(pg_conn, R1, source="simulation")
    from dataclasses import replace
    bad_run = replace(out.run, run_id="x", source="live")
    with pytest.raises(ValueError, match="simulation row"):
        db.save_run(pg_conn, bad_run, out.products, [], [], [])


def test_run_is_committed_and_visible_from_a_new_connection(pg_conn):
    """Regression: a run must be really committed, not just visible inside its own transaction."""
    from scraper.config import load_settings
    from tests.conftest import TEST_DB
    scrape(pg_conn, R1)
    pg_conn.close()  # closing must not roll anything back
    other = db.connect(load_settings(), dbname=TEST_DB)
    try:
        assert other.info.transaction_status.name == "IDLE"
        assert len(db.fetch_products(other)) == 8
        assert db.latest_run(other).rows_valid == 8
    finally:
        other.close()
