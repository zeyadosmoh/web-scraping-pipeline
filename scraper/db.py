"""PostgreSQL storage with the same live/simulation isolation as changes.py.

- `source` is part of every key (products PK is (source, upc)), so a simulated row can
  never overwrite or shadow a live one.
- Every read function takes `source` and defaults to "live".
- Removed products get `removed_at`; rows are never deleted. The one exception is
  `reset_simulation()`, which clears only source='simulation' so the demo is repeatable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from scraper.changes import Change, HistoryRecord, KnownProduct
from scraper.clean import Product, Rejection, Source
from scraper.config import Settings
from scraper.report import LogRow, ReportProduct, RunInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id          text PRIMARY KEY,
    source          text NOT NULL CHECK (source IN ('live', 'simulation')),
    started_at      timestamptz NOT NULL,
    finished_at     timestamptz NOT NULL,
    limit_n         integer,
    complete        boolean NOT NULL,
    resumed         boolean NOT NULL,
    rows_scraped    integer NOT NULL,
    rows_valid      integer NOT NULL,
    rows_rejected   integer NOT NULL,
    fetch_failures  integer NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    source          text NOT NULL CHECK (source IN ('live', 'simulation')),
    upc             text NOT NULL,
    title           text NOT NULL,
    category        text NOT NULL,
    price_gbp       numeric(10,2) NOT NULL CHECK (price_gbp > 0),
    stock           integer NOT NULL CHECK (stock >= 0),
    rating          smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    url             text NOT NULL,
    first_seen_at   timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    removed_at      timestamptz,
    PRIMARY KEY (source, upc)
);

CREATE TABLE IF NOT EXISTS price_stock_history (
    id              bigserial PRIMARY KEY,
    source          text NOT NULL,
    upc             text NOT NULL,
    price_gbp       numeric(10,2) NOT NULL,
    stock           integer NOT NULL,
    recorded_at     timestamptz NOT NULL,
    run_id          text NOT NULL REFERENCES scrape_runs(run_id),
    FOREIGN KEY (source, upc) REFERENCES products(source, upc)
);
CREATE INDEX IF NOT EXISTS ix_history_latest ON price_stock_history (source, upc, recorded_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS change_log (
    id              bigserial PRIMARY KEY,
    run_id          text NOT NULL REFERENCES scrape_runs(run_id),
    source          text NOT NULL,
    upc             text NOT NULL,
    title           text NOT NULL,
    category        text NOT NULL,
    change_type     text NOT NULL CHECK (change_type IN ('new','price','stock','price+stock','removed')),
    old_price       numeric(10,2),
    new_price       numeric(10,2),
    old_stock       integer,
    new_stock       integer,
    detected_at     timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS import_log (
    id              bigserial PRIMARY KEY,
    run_id          text NOT NULL REFERENCES scrape_runs(run_id),
    source          text NOT NULL,
    logged_at       timestamptz NOT NULL,
    url             text NOT NULL,
    upc             text,
    field           text NOT NULL,
    value           text,
    reason          text NOT NULL
);
"""


def connect(settings: Settings, dbname: str | None = None) -> psycopg.Connection:
    if not settings.pg_password:
        raise RuntimeError("POSTGRES_PASSWORD is not set (put it in .env)")
    # autocommit=True is required: with psycopg 3 in non-autocommit mode, the first read opens an
    # implicit transaction and `conn.transaction()` then only creates a SAVEPOINT, so nothing is
    # committed and closing the connection silently rolls the whole run back.
    # With autocommit, every `with conn.transaction():` block is a real BEGIN ... COMMIT.
    conn = psycopg.connect(host=settings.pg_host, port=settings.pg_port, dbname=dbname or settings.pg_db,
                           user=settings.pg_user, password=settings.pg_password, connect_timeout=5,
                           autocommit=True)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(SCHEMA)


# ---- reads used by the scrape (baseline) ---------------------------------------
def load_latest_history(conn, source: Source = "live") -> list[HistoryRecord]:
    rows = conn.execute(
        """SELECT DISTINCT ON (upc) upc, price_gbp, stock, source, recorded_at
             FROM price_stock_history WHERE source = %s
            ORDER BY upc, recorded_at DESC, id DESC""", (source,)).fetchall()
    return [HistoryRecord(*r) for r in rows]


def load_active_products(conn, source: Source = "live") -> list[KnownProduct]:
    rows = conn.execute(
        """SELECT upc, title, category, price_gbp, stock FROM products
            WHERE source = %s AND removed_at IS NULL ORDER BY upc""", (source,)).fetchall()
    return [KnownProduct(*r) for r in rows]


# ---- write one run atomically --------------------------------------------------
def save_run(conn, run: RunInfo, products: Iterable[Product], changes: Iterable[Change],
             history: Iterable[HistoryRecord], log: Iterable[LogRow]) -> None:
    """Store a whole run in ONE explicit transaction (real BEGIN/COMMIT on the autocommit
    connection): either every row of the run is committed, or none is."""
    products, changes, history, log = list(products), list(changes), list(history), list(log)
    for item in [*products, *changes, *history, *log]:
        if item.source != run.source:
            raise ValueError(f"run {run.run_id} is {run.source} but got a {item.source} row")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO scrape_runs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (run.run_id, run.source, run.started_at, run.finished_at, run.limit, run.complete, run.resumed,
             run.rows_scraped, run.rows_valid, run.rows_rejected, run.fetch_failures))
        cur.executemany(
            """INSERT INTO products (source, upc, title, category, price_gbp, stock, rating, url,
                                     first_seen_at, last_seen_at, removed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
               ON CONFLICT (source, upc) DO UPDATE SET
                 title = EXCLUDED.title, category = EXCLUDED.category, price_gbp = EXCLUDED.price_gbp,
                 stock = EXCLUDED.stock, rating = EXCLUDED.rating, url = EXCLUDED.url,
                 last_seen_at = EXCLUDED.last_seen_at, removed_at = NULL""",
            [(p.source, p.upc, p.title, p.category, p.price_gbp, p.stock, p.rating, p.url,
              p.scraped_at, p.scraped_at) for p in products])
        cur.executemany(
            """INSERT INTO price_stock_history (source, upc, price_gbp, stock, recorded_at, run_id)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            [(h.source, h.upc, h.price_gbp, h.stock, h.recorded_at, run.run_id) for h in history])
        cur.executemany(
            """UPDATE products SET removed_at = %s WHERE source = %s AND upc = %s AND removed_at IS NULL""",
            [(c.detected_at, c.source, c.upc) for c in changes if c.change_type == "removed"])
        cur.executemany(
            """INSERT INTO change_log (run_id, source, upc, title, category, change_type,
                                       old_price, new_price, old_stock, new_stock, detected_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(run.run_id, c.source, c.upc, c.title, c.category, c.change_type, c.old_price, c.new_price,
              c.old_stock, c.new_stock, c.detected_at) for c in changes])
        cur.executemany(
            """INSERT INTO import_log (run_id, source, logged_at, url, upc, field, value, reason)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(run.run_id, r.source, r.logged_at, r.url, r.upc, r.field, r.value, r.reason) for r in log])


def reset_simulation(conn) -> None:
    """Remove simulated data only (so the demo can be replayed). Live rows are never touched."""
    with conn.transaction():
        for table in ("change_log", "import_log", "price_stock_history", "products", "scrape_runs"):
            conn.execute(f"DELETE FROM {table} WHERE source = 'simulation'")


# ---- reads used by reports (live-only by default) --------------------------------
def latest_run(conn, source: Source = "live") -> RunInfo | None:
    row = conn.execute(
        """SELECT run_id, source, started_at, finished_at, limit_n, complete, resumed,
                  rows_scraped, rows_valid, rows_rejected, fetch_failures
             FROM scrape_runs WHERE source = %s ORDER BY finished_at DESC LIMIT 1""", (source,)).fetchone()
    return RunInfo(*row) if row else None


def fetch_products(conn, source: Source = "live") -> list[ReportProduct]:
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            """SELECT upc, title, category, price_gbp, stock, rating, url, source,
                      first_seen_at, last_seen_at, removed_at
                 FROM products WHERE source = %s
                ORDER BY removed_at IS NOT NULL, category, title""", (source,)).fetchall()
    return [ReportProduct(**r) for r in rows]


def fetch_changes(conn, run_id: str, source: Source = "live") -> list[Change]:
    rows = conn.execute(
        """SELECT upc, title, category, change_type, old_price, new_price, old_stock, new_stock,
                  source, detected_at
             FROM change_log WHERE run_id = %s AND source = %s
            ORDER BY CASE change_type WHEN 'removed' THEN 0 WHEN 'price+stock' THEN 1 WHEN 'price' THEN 2
                                      WHEN 'stock' THEN 3 ELSE 4 END, category, title""",
        (run_id, source)).fetchall()
    return [Change(*r) for r in rows]


def fetch_log(conn, run_id: str, source: Source = "live") -> list[LogRow]:
    rows = conn.execute(
        """SELECT logged_at, url, upc, field, value, reason, source FROM import_log
            WHERE run_id = %s AND source = %s ORDER BY id""", (run_id, source)).fetchall()
    return [LogRow(*r) for r in rows]


def rejection_to_log(r: Rejection, source: Source, at: datetime) -> LogRow:
    return LogRow(at, r.url, r.upc, r.field, r.value, r.reason, source)
