# P6 · Web Scraping Pipeline

Scrapes products and prices from **practice websites built for scraping**, cleans and validates them, stores them in PostgreSQL with price/stock history, and produces a formatted Excel report plus CSV. Runs are scheduled, and the pipeline tracks what changed between runs.

> **Status:** Phase B is done: PostgreSQL, change tracking (including removed products), the Excel/CSV report, the CLI and a full live run. Phase C (Playwright for the JavaScript site, scheduling, GitHub Actions) is next.

## Ethics & legality

- **Only practice sites are scraped.** Both targets were built to be scraped:
  - [books.toscrape.com](https://books.toscrape.com): 1,000 fictional books. Every page carries this notice: *"This is a demo website for web scraping purposes. Prices and ratings here were randomly assigned and have no real meaning."*
  - [quotes.toscrape.com/js/](https://quotes.toscrape.com/js/): a JavaScript-rendered sandbox from the same project.
- **How robots.txt is handled:** it is fetched once per host before any page is requested.
  - **404 or another 4xx** means no rules, so every path is allowed. This is the standard behavior (RFC 9309). Both sites return 404 as of 2026-11.
  - **5xx or unreachable** means everything on that host is treated as disallowed, which is the conservative choice.
  - **A disallowed path** is skipped and logged. It is never fetched.
- **Rate limit:** there is a fixed delay between requests (`SCRAPER_DELAY_SECONDS`, 1 s by default).
- **Retries** use exponential backoff and apply only to timeouts, connection errors, 429 and 5xx. Every request has a timeout.
- **User-Agent:** a clear one that identifies the project. The scraper never pretends to be a browser.
- **No personal data** is collected.

## Architecture

```
crawl (robots, rate limit, retries, checkpoint) -> parse -> clean/validate -> detect changes
      -> PostgreSQL (one transaction per run) -> Excel / CSV (live-only by default)
```

| Module | Role |
|---|---|
| `scraper/http.py` | Polite fetcher. Always decodes as UTF-8, because the site sends no charset and `£` would otherwise become `Â£` |
| `scraper/books.py` | Parsers for list and detail pages |
| `scraper/clean.py` | Price to `Decimal` GBP, stock text to `int`, rating word to 1-5, trimming, dedupe by UPC, rejected rows with a reason |
| `scraper/changes.py` | Change detection (new, price, stock, price+stock, removed) with a separate baseline per source |
| `scraper/crawl.py` | Pagination, `--limit`, resume from staged rows, and the "complete run" rule |
| `scraper/state.py` | Atomic checkpoint plus staged raw rows, so a resumed run still has the whole catalogue |
| `scraper/db.py` | PostgreSQL schema and queries. `source` is part of every key, and reads default to `live` |
| `scraper/pipeline.py` | One run: crawl, clean, compare, then store everything in one transaction |
| `scraper/report.py`, `scraper/export.py` | Summary calculations (exact Decimals), Excel and CSV |
| `scraper/cli.py` | `init-db`, `scrape`, `export`, `run-all` |
| `scraper/timeutil.py` | Stores times in UTC and shows them in Africa/Cairo time |

## Data rules

- All timestamps are stored in **UTC**. The Excel report shows them in **Africa/Cairo** time.
- Every row has a `source` of `live` or `simulation`. Reports (All products, Summary, CSV) **include only `live` rows by default**, so simulated data never mixes into real numbers.
- A history row is written only when a product is first seen or when its price or stock changes.
- **Removed products** are flagged only after a **complete** run: no `--limit`, every list page reached, no failed or robots-skipped URL, and every rejected row still had a UPC. A resumed run counts only if it finishes the whole catalogue. Removed products get `removed_at` and are never deleted. A rejected row is *not* treated as removed.
- Invalid rows are never dropped silently. Each one goes to `import_log` and the **Import_Log** sheet with the field, the raw value and a reason.

### Database (PostgreSQL 16, container `p6-scraper-pg` on `127.0.0.1:5435`)

| Table | Purpose |
|---|---|
| `products` | Current state per `(source, upc)`: price, stock, rating, first/last seen, `removed_at` |
| `price_stock_history` | Append-only. A row is added only for a new product or a price/stock change |
| `change_log` | What changed in each run, including `removed` |
| `import_log` | Rejected rows and failed URLs, with reasons |
| `scrape_runs` | One row per run: timings, limit, whether it was complete, counts |

## Change-tracking demo (simulation)

books.toscrape.com prices never change, so `--simulate-changes` replays two rounds of **edited copies of saved pages** (`data/simulation/`, rebuilt with `scripts/build_simulation.py`). Round 2 has 2 price changes, 2 stock changes (one sold out), 1 price+stock change, 1 removed book, 1 new book and 1 invalid price.

- Simulated rows are stored with `source = 'simulation'`. They have their own baseline and never touch live rows.
- The simulation report is a separate file (`report_simulation_*.xlsx`) with a red **SIMULATION** banner on every sheet.
- Each replay resets only the simulation rows, so the demo can be repeated.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then set POSTGRES_PASSWORD; .env is never committed
docker compose up -d      # p6-scraper-pg on 127.0.0.1:5435
.venv/bin/python -m scraper init-db
.venv/bin/pytest
```

## Usage

```bash
.venv/bin/python -m scraper run-all --limit 20          # quick run: 20 books, then Excel and CSV
.venv/bin/python -m scraper run-all                      # full catalogue (about 18 min at a 1 s delay)
.venv/bin/python -m scraper run-all --simulate-changes   # change-tracking demo (simulation report)
.venv/bin/python -m scraper scrape [--limit N] [--fresh] [--delay S]
.venv/bin/python -m scraper export [--source live|simulation]
```

- If a full run is interrupted, it exits with code 2 and keeps `outputs/state/live/checkpoint.json`. Running the same command again resumes it, and `--fresh` starts over.
- Outputs:
  - `outputs/reports/report_<source>_<UTC stamp>.xlsx` and `report_<source>_latest.xlsx`
  - `outputs/csv/<source>/{products,changes,import_log}_<source>.csv` (UTF-8 with BOM, times in UTC ISO-8601)
  - `outputs/logs/run_*.log`

### Excel report

| Sheet | Contents |
|---|---|
| All products | Every product with its £ price, stock, rating, Active/Removed status, and first/last seen and removed times (Africa/Cairo) |
| Changes since last run | new / price / stock / price+stock / removed, with old and new values and the £ difference, colour-coded |
| Summary | Per category: count, average/min/max price, out of stock, units. Also run facts and totals |
| Import_Log | Rejected rows and failed URLs, with the field, raw value and reason |

Every sheet has a frozen header, an autofilter, £ number formats and an explicit left-to-right layout (safe in Arabic/RTL Excel), and every time header names its timezone.

## Scheduling

_TODO (Phase C): cron and systemd timer for WSL, and a scheduled GitHub Actions workflow._

## Tests

These run entirely on saved HTML fixtures (`tests/fixtures/`, `data/simulation/`) and make no network calls. The DB tests (`-m db`) use a throwaway `scraper_test` database in the P6 container and are skipped automatically when it isn't reachable.

```bash
.venv/bin/pytest            # everything
.venv/bin/pytest -m "not db"
```
