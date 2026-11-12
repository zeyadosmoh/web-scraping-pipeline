"""Command line: init-db | scrape | export | run-all.

  python -m scraper scrape [--limit N] [--fresh] [--simulate-changes]
  python -m scraper export [--source live|simulation]
  python -m scraper run-all [--limit N] [--fresh] [--simulate-changes]

A full live scrape (no --limit) writes a checkpoint. If it is interrupted, running the same
command again resumes it. --fresh throws the checkpoint away. --limit runs never checkpoint
and never flag removals.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from scraper import db
from scraper.config import Settings, load_settings
from scraper.crawl import CrawlAborted
from scraper.export import write_csv, write_excel
from scraper.http import PoliteFetcher
from scraper.pipeline import RunOutcome, run_scrape, run_simulation
from scraper.report import ReportData
from scraper.state import Checkpoint
from scraper.timeutil import utc_now

log = logging.getLogger("scraper")
EXIT_ABORTED = 2


def setup_logging(out_dir: Path) -> Path:
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"run_{utc_now():%Y%m%dT%H%M%SZ}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fmt.converter = __import__("time").gmtime  # log timestamps in UTC
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stderr), logging.FileHandler(path, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return path


def print_outcome(o: RunOutcome) -> None:
    r = o.run
    kinds: dict[str, int] = {}
    for c in o.changes:
        kinds[c.change_type] = kinds.get(c.change_type, 0) + 1
    print(f"\n[{r.source}] run {r.run_id}")
    print(f"  duration        {r.duration_seconds:.1f} s{'  (resumed)' if r.resumed else ''}")
    print(f"  rows scraped    {r.rows_scraped}   valid {r.rows_valid}   rejected {r.rows_rejected}"
          f"   fetch failures {r.fetch_failures}")
    print(f"  complete run    {'yes' if r.complete else 'no'}"
          f"   removal check {'done' if o.removal_checked else 'skipped (partial run)'}")
    print(f"  changes         {kinds or 'none'}")
    reasons: dict[str, int] = {}
    for row in o.log:
        reasons[row.reason] = reasons.get(row.reason, 0) + 1
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  rejected: {n} x {reason}")


def cmd_scrape(args, settings: Settings, conn) -> list[RunOutcome]:
    if args.simulate_changes:
        outcomes = run_simulation(conn)
    else:
        ck_path = None if args.limit is not None else settings.output_dir / "state" / "live" / "checkpoint.json"
        ck = Checkpoint.load(ck_path)
        if args.fresh and ck.exists:
            log.info("--fresh: discarding checkpoint of run %s", ck.run_id)
            ck.clear()
        fetcher = PoliteFetcher(user_agent=settings.user_agent,
                                delay_seconds=args.delay if args.delay is not None else settings.delay_seconds,
                                timeout_seconds=settings.timeout_seconds, max_retries=settings.max_retries,
                                backoff_seconds=settings.backoff_seconds)
        outcomes = [run_scrape(conn, fetcher, source="live", limit=args.limit, checkpoint=ck)]
    for o in outcomes:
        print_outcome(o)
    return outcomes


def build_report(conn, source) -> ReportData:
    run = db.latest_run(conn, source)
    if run is None:
        raise SystemExit(f"no {source} runs in the database yet: run `scrape` first")
    return ReportData(source, run, db.fetch_products(conn, source), db.fetch_changes(conn, run.run_id, source),
                      db.fetch_log(conn, run.run_id, source), generated_at=utc_now())


def cmd_export(source: str, settings: Settings, conn) -> list[Path]:
    data = build_report(conn, source)
    out = settings.output_dir
    stamp = data.run.finished_at.strftime("%Y%m%dT%H%M%SZ")
    xlsx = write_excel(data, out / "reports" / f"report_{source}_{stamp}.xlsx", settings.report_timezone)
    latest = out / "reports" / f"report_{source}_latest.xlsx"
    shutil.copyfile(xlsx, latest)
    csvs = write_csv(data, out / "csv" / source)
    print(f"\n[{source}] report written:\n  {xlsx}\n  {latest}")
    for p in csvs:
        print(f"  {p}")
    return [xlsx, latest, *csvs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scraper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables (idempotent)")
    for name in ("scrape", "run-all"):
        p = sub.add_parser(name, help="scrape books.toscrape.com" + (" then export" if name == "run-all" else ""))
        p.add_argument("--limit", type=int, help="stop after N books (quick run; never flags removals)")
        p.add_argument("--fresh", action="store_true", help="ignore an unfinished checkpoint")
        p.add_argument("--delay", type=float, help="seconds between requests (default from .env)")
        p.add_argument("--simulate-changes", action="store_true",
                       help="replay the saved two-round simulation instead of the live site")
    p = sub.add_parser("export", help="write Excel + CSV from the database")
    p.add_argument("--source", choices=["live", "simulation"], default="live")
    args = ap.parse_args(argv)

    settings = load_settings()
    if getattr(args, "limit", None) is not None and args.limit <= 0:
        ap.error("--limit must be positive")
    log_path = setup_logging(settings.output_dir)
    conn = db.connect(settings)
    try:
        db.init_schema(conn)
        if args.cmd == "init-db":
            print("schema ready")
        elif args.cmd == "export":
            cmd_export(args.source, settings, conn)
        else:
            try:
                cmd_scrape(args, settings, conn)
            except CrawlAborted as exc:
                log.error("%s", exc)
                print(f"\nRun aborted: {exc}\nRe-run the same command to resume (log: {log_path})")
                return EXIT_ABORTED
            if args.cmd == "run-all":
                cmd_export("simulation" if args.simulate_changes else "live", settings, conn)
    finally:
        conn.close()
    print(f"\nlog: {log_path}")
    return 0
