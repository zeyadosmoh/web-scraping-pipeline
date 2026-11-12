"""Crawler over the saved simulation catalogue (no network)."""
from pathlib import Path

import pytest

from scraper.crawl import CrawlAborted, FixtureFetcher, crawl
from scraper.http import FetchError
from scraper.state import Checkpoint

ROUND1 = Path(__file__).resolve().parents[1] / "data" / "simulation" / "round1"
START = "https://books.toscrape.com/catalogue/page-1.html"


def test_full_crawl_is_complete():
    r = crawl(FixtureFetcher(ROUND1), START, limit=None, checkpoint=Checkpoint.load(None))
    assert len(r.raws) == 8 and r.pages_visited == 2 and r.reached_end
    assert r.complete and r.failures == []


def test_limit_crawl_is_never_complete():
    f = FixtureFetcher(ROUND1)
    r = crawl(f, START, limit=3, checkpoint=Checkpoint.load(None))
    assert len(r.raws) == 3 and not r.complete
    assert len([u for u in f.requested if "/page-" not in u]) == 3  # no extra fetches


def test_limit_bigger_than_catalogue_is_still_not_complete():
    r = crawl(FixtureFetcher(ROUND1), START, limit=10_000, checkpoint=Checkpoint.load(None))
    assert len(r.raws) == 8 and r.reached_end and not r.complete


class FlakyFetcher(FixtureFetcher):
    """Fails on a given URL until `heal()` is called."""
    broken: set = set()

    def get_html(self, url):
        if url in self.broken:
            self.requested.append(url)
            raise FetchError("simulated outage")
        return super().get_html(url)


def test_failed_detail_page_makes_run_incomplete():
    f = FlakyFetcher(ROUND1)
    f.broken = {"https://books.toscrape.com/catalogue/soumission_998/index.html"}
    r = crawl(f, START, limit=None, checkpoint=Checkpoint.load(None))
    assert len(r.raws) == 7 and r.reached_end and not r.complete
    assert r.failures[0]["url"].endswith("soumission_998/index.html")


def test_abort_then_resume_finishes_complete(tmp_path):
    ck_path = tmp_path / "checkpoint.json"
    f = FlakyFetcher(ROUND1)
    f.broken = {"https://books.toscrape.com/catalogue/page-2.html"}  # list page outage
    ck = Checkpoint.load(ck_path); ck.run_id = "run-1"
    with pytest.raises(CrawlAborted):
        crawl(f, START, limit=None, checkpoint=ck)
    # restart: same checkpoint on disk, outage over
    f2 = FixtureFetcher(ROUND1)
    ck2 = Checkpoint.load(ck_path)
    r = crawl(f2, START, limit=None, checkpoint=ck2)
    assert r.resumed and r.complete and len(r.raws) == 8
    assert r.detail_fetched == 4  # page-1's four books came from staging, not re-fetched
    assert START not in f2.requested


def test_resumed_run_that_stops_early_is_not_complete(tmp_path):
    ck_path = tmp_path / "checkpoint.json"
    f = FlakyFetcher(ROUND1)
    f.broken = {"https://books.toscrape.com/catalogue/page-2.html"}
    ck = Checkpoint.load(ck_path); ck.run_id = "run-1"
    with pytest.raises(CrawlAborted):
        crawl(f, START, limit=None, checkpoint=ck)
    with pytest.raises(CrawlAborted):  # still down on resume
        crawl(f, START, limit=None, checkpoint=Checkpoint.load(ck_path))
    assert Checkpoint.load(ck_path).exists  # kept for the next attempt


def test_consecutive_detail_failures_abort():
    f = FlakyFetcher(ROUND1)
    f.broken = {f"https://books.toscrape.com/catalogue/{s}/index.html" for s in (
        "a-light-in-the-attic_1000", "tipping-the-velvet_999", "soumission_998", "sharp-objects_997",
        "sapiens-a-brief-history-of-humankind_996")}
    with pytest.raises(CrawlAborted, match="in a row"):
        crawl(f, START, limit=None, checkpoint=Checkpoint.load(None))
