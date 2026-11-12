# P6 · Web Scraping Pipeline

Scrapes products and prices from **practice websites built for scraping**, cleans and validates them, stores them in PostgreSQL with price/stock history, and produces a formatted Excel report plus CSV. Runs are scheduled, and the pipeline tracks what changed between runs.

> **Status:** Phase A is done: scaffold, polite fetcher, parsing, cleaning and change detection, all tested on saved HTML. The database, export, CLI, Playwright and scheduling come in later phases.

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

## Architecture (planned)

```
fetch (robots, rate limit, retries) -> parse -> clean/validate -> detect changes
      -> PostgreSQL (products + price_stock_history) -> Excel / CSV
```

| Module | Role |
|---|---|
| `scraper/http.py` | Polite fetcher. Always decodes as UTF-8, because the site sends no charset and `£` would otherwise become `Â£` |
| `scraper/books.py` | Parsers for list and detail pages |
| `scraper/clean.py` | Price to `Decimal` GBP, stock text to `int`, rating word to 1-5, trimming, dedupe by UPC, rejected rows with a reason |
| `scraper/changes.py` | Change detection and live/simulation isolation |
| `scraper/state.py` | Checkpoint file for resuming after a failure |
| `scraper/timeutil.py` | Stores times in UTC and shows them in Africa/Cairo time |

## Data rules

- All timestamps are stored in **UTC**. The Excel report shows them in **Africa/Cairo** time.
- Every row has a `source` of `live` or `simulation`. Reports (All products, Summary, CSV) **include only `live` rows by default**, so simulated data never mixes into real numbers.
- A history row is written only when a product is first seen or when its price or stock changes.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # adjust values; .env is never committed
.venv/bin/pytest
```

## Usage

_TODO (Phase B): `scrape`, `export`, `run-all`, `--limit`, `--simulate-changes`, `--no-db`._

## Scheduling

_TODO (Phase C): cron and systemd timer for WSL, and a scheduled GitHub Actions workflow._

## Tests

These run entirely on saved HTML fixtures in `tests/fixtures/` and make no network calls.
