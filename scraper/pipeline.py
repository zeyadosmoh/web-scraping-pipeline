"""One scrape run: crawl -> clean -> compare with this source's baseline -> store atomically."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scraper import db
from scraper.changes import Change, detect_changes, detect_removed, history_rows_to_insert, latest_state
from scraper.clean import Product, Source, clean_books
from scraper.crawl import CrawlResult, Fetcher, FixtureFetcher, crawl
from scraper.report import LogRow, RunInfo
from scraper.state import Checkpoint
from scraper.timeutil import utc_now

log = logging.getLogger(__name__)

BOOKS_START = "https://books.toscrape.com/catalogue/page-1.html"
SIMULATION_DIR = Path(__file__).resolve().parents[1] / "data" / "simulation"


@dataclass
class RunOutcome:
    run: RunInfo
    products: list[Product]
    changes: list[Change]
    log: list[LogRow]
    removal_checked: bool


def new_run_id(source: Source) -> str:
    return f"{source}-{utc_now():%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"


def run_scrape(conn, fetcher: Fetcher, *, source: Source, limit: int | None,
               checkpoint: Checkpoint, start_url: str = BOOKS_START) -> RunOutcome:
    if not checkpoint.exists:
        checkpoint.run_id = new_run_id(source)
        checkpoint.params = {"source": source, "start_url": start_url, "started_at": utc_now().isoformat()}
        checkpoint.save()
    started_at = datetime.fromisoformat(checkpoint.params["started_at"])

    result: CrawlResult = crawl(fetcher, start_url, limit=limit, checkpoint=checkpoint)
    scraped_at = utc_now()
    products, rejections = clean_books(result.raws, source, scraped_at)

    log_rows = [LogRow(scraped_at, f["url"], None, "fetch", None, f["reason"], source) for f in result.failures]
    log_rows += [db.rejection_to_log(r, source, scraped_at) for r in rejections]

    changes = detect_changes(products, latest_state(db.load_latest_history(conn, source), source))
    # Removal is only safe when every row on the site is accounted for: a complete crawl, and
    # every rejected row still has a UPC (so we know it exists and was not removed).
    removal_checked = result.complete and all(r.upc for r in rejections)
    seen_upcs = {p.upc for p in products} | {r.upc for r in rejections if r.upc}
    changes += detect_removed(db.load_active_products(conn, source), seen_upcs,
                              complete=removal_checked, source=source, detected_at=scraped_at)

    run = RunInfo(run_id=checkpoint.run_id, source=source, started_at=started_at, finished_at=utc_now(),
                  limit=limit, complete=result.complete, resumed=result.resumed,
                  rows_scraped=len(result.raws), rows_valid=len(products), rows_rejected=len(rejections),
                  fetch_failures=len(result.failures))
    db.save_run(conn, run, products, changes, history_rows_to_insert(changes), log_rows)
    checkpoint.clear()
    log.info("run %s stored: %d valid, %d rejected, %d failures, %d changes, complete=%s",
             run.run_id, run.rows_valid, run.rows_rejected, run.fetch_failures, len(changes), run.complete)
    return RunOutcome(run, products, changes, log_rows, removal_checked)


def run_simulation(conn) -> list[RunOutcome]:
    """Replay the two-round simulation catalogue. Touches only source='simulation' rows."""
    db.reset_simulation(conn)
    outcomes = []
    for rnd in ("round1", "round2"):
        log.info("simulation %s from %s", rnd, SIMULATION_DIR / rnd)
        outcomes.append(run_scrape(conn, FixtureFetcher(SIMULATION_DIR / rnd), source="simulation",
                                   limit=None, checkpoint=Checkpoint.load(None)))
    return outcomes
