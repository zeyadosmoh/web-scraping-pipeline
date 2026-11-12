"""Crawl the books catalogue: list pages -> detail pages, with --limit and resume.

Completeness rule (used to decide whether "removed" may be flagged):
  complete = no --limit AND the last list page was reached AND no URL failed or was
             skipped by robots.txt. A resumed run counts as complete only when it
             finishes the whole catalogue; rows from earlier attempts come from staging.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from scraper.books import RawBook, parse_detail_page, parse_list_page
from scraper.http import FetchError, RobotsDisallowed
from scraper.state import Checkpoint

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 5


class Fetcher(Protocol):
    def get_html(self, url: str) -> str: ...


class CrawlAborted(Exception):
    """Too many failures in a row (or a list page failed). The checkpoint is kept for resume."""


@dataclass
class CrawlResult:
    raws: list[RawBook]
    failures: list[dict]          # {"url", "reason"}
    reached_end: bool
    limit: int | None
    resumed: bool
    pages_visited: int = 0
    detail_fetched: int = 0       # fetched in THIS attempt (not from staging)

    @property
    def complete(self) -> bool:
        return self.limit is None and self.reached_end and not self.failures


@dataclass
class FixtureFetcher:
    """Serves saved HTML for URLs under base_url (used by --simulate-changes and tests)."""
    root: Path
    base_url: str = "https://books.toscrape.com/"
    requested: list[str] = field(default_factory=list)

    def get_html(self, url: str) -> str:
        self.requested.append(url)
        if not url.startswith(self.base_url):
            raise FetchError(f"outside fixture base: {url}")
        path = Path(self.root) / url[len(self.base_url):]
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FetchError(f"fixture not found: {path}") from None


def crawl(fetcher: Fetcher, start_url: str, *, limit: int | None, checkpoint: Checkpoint) -> CrawlResult:
    resumed = checkpoint.exists and bool(checkpoint.done_urls or checkpoint.next_list_url)
    raws = checkpoint.staged_rows()
    page_url = checkpoint.next_list_url or start_url
    result = CrawlResult(raws, checkpoint.failures, reached_end=False, limit=limit, resumed=resumed)
    if resumed:
        log.info("resuming run %s: %d rows staged, next list page %s", checkpoint.run_id, len(raws), page_url)
    consecutive = 0

    def fail(url: str, reason: str) -> None:
        nonlocal consecutive
        log.warning("failed %s: %s", url, reason)
        checkpoint.failures.append({"url": url, "reason": reason})
        checkpoint.save()

    while page_url:
        if limit is not None and len(raws) >= limit:
            break
        try:
            html = fetcher.get_html(page_url)
        except RobotsDisallowed:
            fail(page_url, "robots.txt disallows this list page")
            break
        except FetchError as exc:
            checkpoint.save()
            raise CrawlAborted(f"list page failed, run can be resumed: {exc}") from exc
        result.pages_visited += 1
        detail_urls, next_url = parse_list_page(html, page_url)

        stopped_early = False
        for url in detail_urls:
            if limit is not None and len(raws) >= limit:
                stopped_early = True
                break
            if checkpoint.is_done(url):
                continue
            try:
                raw = parse_detail_page(fetcher.get_html(url), url)
            except RobotsDisallowed:
                fail(url, "robots.txt disallows this page")
                continue
            except FetchError as exc:
                fail(url, f"fetch failed: {exc}")
                consecutive += 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    raise CrawlAborted(f"{consecutive} detail pages failed in a row; run can be resumed")
                continue
            consecutive = 0
            checkpoint.stage(raw)
            checkpoint.mark_done(url)
            checkpoint.save()
            raws.append(raw)
            result.detail_fetched += 1
        if stopped_early:
            break
        page_url = next_url
        checkpoint.next_list_url = next_url
        checkpoint.save()
        if next_url is None:
            result.reached_end = True

    return result
