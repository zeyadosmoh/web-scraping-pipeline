"""quotes.toscrape.com/js/: a JavaScript-rendered practice site, scraped with Playwright.

The raw HTML has no quotes in it; they are built in the browser from an inline `var data`
by jQuery. So pages are rendered in headless Chromium and the *rendered* DOM is parsed.

Politeness is the same as for the books site. BrowserFetcher subclasses PoliteFetcher and
reuses its robots.txt check (fetched with plain HTTP), its rate limit, and its
retry/backoff loop. The browser context sends the same clear User-Agent.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraper.clean import clean_text
from scraper.http import FetchError, PoliteFetcher, RobotsDisallowed

log = logging.getLogger(__name__)

QUOTES_START = "https://quotes.toscrape.com/js/"
_CURLY = "“”\"'‘’"


@dataclass(frozen=True)
class RawQuote:
    page_url: str
    text: str | None
    author: str | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Quote:
    text: str
    author: str
    tags: tuple[str, ...]
    page_url: str


@dataclass(frozen=True)
class QuoteRejection:
    page_url: str
    text: str | None
    reason: str


def parse_quotes_page(html: str, page_url: str) -> tuple[list[RawQuote], str | None]:
    """Parse a RENDERED page. Returns (quotes, absolute next-page URL or None)."""
    soup = BeautifulSoup(html, "lxml")
    quotes = []
    for q in soup.select("div.quote"):
        text, author = q.select_one("span.text"), q.select_one("small.author")
        quotes.append(RawQuote(page_url,
                               text.get_text(strip=True) if text else None,
                               author.get_text(strip=True) if author else None,
                               tuple(a.get_text(strip=True) for a in q.select("div.tags a.tag"))))
    nxt = soup.select_one("li.next a[href]")
    return quotes, (urljoin(page_url, nxt["href"]) if nxt else None)


def clean_quotes(raws: list[RawQuote]) -> tuple[list[Quote], list[QuoteRejection]]:
    """Trim, strip the decorative curly quotes, require text and author, dedupe by (text, author)."""
    out: list[Quote] = []
    rejected: list[QuoteRejection] = []
    seen: set[tuple[str, str]] = set()
    for r in raws:
        text = clean_text(r.text).strip(_CURLY).strip()
        author = clean_text(r.author)
        if not text:
            rejected.append(QuoteRejection(r.page_url, r.text, "quote text missing"))
            continue
        if not author:
            rejected.append(QuoteRejection(r.page_url, r.text, "author missing"))
            continue
        key = (text.casefold(), author.casefold())
        if key in seen:
            rejected.append(QuoteRejection(r.page_url, r.text, "duplicate quote (same text and author)"))
            continue
        seen.add(key)
        tags = tuple(dict.fromkeys(t for t in (clean_text(t).lower() for t in r.tags) if t))
        out.append(Quote(text, author, tags, r.page_url))
    return out, rejected


@dataclass
class BrowserFetcher(PoliteFetcher):
    """Renders pages in headless Chromium with the PoliteFetcher rules.

    `page_factory` is injectable for tests. By default `open()` launches Playwright.
    """
    wait_selector: str = "div.quote"
    page_factory: Callable[[], object] | None = None
    _pw: object = field(default=None, init=False, repr=False)
    _browser: object = field(default=None, init=False, repr=False)
    _page: object = field(default=None, init=False, repr=False)

    def open(self) -> "BrowserFetcher":
        if self.page_factory is not None:
            self._page = self.page_factory()
            return self
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        ctx = self._browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
        self._page = ctx.new_page()
        return self

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        self._browser = self._pw = self._page = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def get_html(self, url: str) -> str:
        if not self.allowed(url):
            log.warning("robots.txt disallows %s: skipped", url)
            raise RobotsDisallowed(url)
        if self._page is None:
            raise RuntimeError("BrowserFetcher not opened (use `with BrowserFetcher(...) as f:`)")
        try:
            from playwright.sync_api import Error as PlaywrightError
            retry_on: tuple = (PlaywrightError, TimeoutError)
        except ImportError:  # pragma: no cover
            retry_on = (TimeoutError,)
        timeout_ms = self.timeout_seconds * 1000

        def attempt():
            resp = self._page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            status = resp.status if resp is not None else 0
            if status == 200:
                self._page.wait_for_selector(self.wait_selector, timeout=timeout_ms)
            return self._page.content(), status

        html, status = self._retrying(url, attempt, retry_on=retry_on)
        if status != 200:
            raise FetchError(f"GET {url} returned HTTP {status}")
        return html


@dataclass
class QuotesResult:
    raws: list[RawQuote]
    failures: list[dict]
    pages: int


def scrape_quotes(fetcher, start_url: str = QUOTES_START, max_pages: int | None = None) -> QuotesResult:
    raws: list[RawQuote] = []
    failures: list[dict] = []
    url, pages = start_url, 0
    while url and (max_pages is None or pages < max_pages):
        try:
            html = fetcher.get_html(url)
        except (RobotsDisallowed, FetchError) as exc:
            failures.append({"url": url, "reason": f"{type(exc).__name__}: {exc}"})
            break
        pages += 1
        found, url = parse_quotes_page(html, url)
        if not found:
            failures.append({"url": url or start_url, "reason": "no quotes in rendered page"})
        raws.extend(found)
    return QuotesResult(raws, failures, pages)


def write_quotes_csv(quotes: list[Quote], rejected: list[QuoteRejection], failures: list[dict],
                     out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    q_path, log_path = out_dir / "quotes.csv", out_dir / "quotes_import_log.csv"
    with q_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["text", "author", "tags", "page_url"])
        w.writerows([[q.text, q.author, ";".join(q.tags), q.page_url] for q in quotes])
    with log_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["page_url", "value", "reason"])
        w.writerows([[r.page_url, r.text, r.reason] for r in rejected])
        w.writerows([[f["url"], "", f["reason"]] for f in failures])
    return [q_path, log_path]
