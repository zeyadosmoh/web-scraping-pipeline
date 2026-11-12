"""quotes.toscrape.com/js/: parsing saved rendered HTML, cleaning, and the browser fetcher.

All offline. The browser test renders the saved RAW page in real headless Chromium, with
every request answered from fixtures (page.route), and is skipped if Chromium is missing.
"""
from pathlib import Path

import pytest

from scraper.http import FetchError, RobotsDisallowed
from scraper.quotes_js import (BrowserFetcher, RawQuote, clean_quotes, parse_quotes_page, scrape_quotes)
from tests.conftest import make_response, missing

FX = Path(__file__).parent / "fixtures" / "quotes"
P1 = "https://quotes.toscrape.com/js/"
P10 = "https://quotes.toscrape.com/js/page/10/"


def read(name):
    return (FX / name).read_text(encoding="utf-8")


# ---- parsing --------------------------------------------------------------------
def test_raw_html_has_no_quotes_js_rendering_is_required():
    quotes, _ = parse_quotes_page(read("js_page1_raw.html"), P1)
    assert quotes == []


def test_rendered_page_parses_ten_quotes_and_next_link():
    quotes, nxt = parse_quotes_page(read("js_page1_rendered.html"), P1)
    assert len(quotes) == 10
    q = quotes[0]
    assert q.author == "Albert Einstein"
    assert q.text.startswith("“The world as we have created it")
    assert q.tags == ("change", "deep-thoughts", "thinking", "world")
    assert nxt == "https://quotes.toscrape.com/js/page/2/"


def test_last_page_has_no_next():
    quotes, nxt = parse_quotes_page(read("js_page10_rendered.html"), P10)
    assert len(quotes) == 10 and nxt is None


# ---- cleaning -----------------------------------------------------------------
def test_clean_quotes_strips_curly_quotes_dedupes_and_logs():
    raws = [
        RawQuote("p", "  “A  thought.” ", " Ann ", ("Life", "life", " ")),
        RawQuote("p", "“A thought.”", "Ann", ()),          # duplicate (same text and author)
        RawQuote("p", "“”", "Bob", ()),                     # empty text
        RawQuote("p", "“Something.”", None, ()),            # no author
    ]
    quotes, rejected = clean_quotes(raws)
    assert [(q.text, q.author, q.tags) for q in quotes] == [("A thought.", "Ann", ("life",))]
    assert [r.reason for r in rejected] == [
        "duplicate quote (same text and author)", "quote text missing", "author missing"]


def test_fixture_pages_clean_without_rejections():
    raws = parse_quotes_page(read("js_page1_rendered.html"), P1)[0] + \
        parse_quotes_page(read("js_page10_rendered.html"), P10)[0]
    quotes, rejected = clean_quotes(raws)
    assert len(quotes) == 20 and rejected == []
    assert not any(q.text[0] in "“\"" for q in quotes)


# ---- browser fetcher politeness (fake page, no browser) -----------------------------
class FakeResp:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, script):
        self.script, self.calls, self.html = list(script), [], ""

    def goto(self, url, timeout=None, wait_until=None):
        self.calls.append((url, timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        self.html = item[1]
        return FakeResp(item[0])

    def wait_for_selector(self, sel, timeout=None):
        pass

    def content(self):
        return self.html


class RobotsSession:
    def __init__(self, robots):
        self.headers, self.robots = {}, robots

    def get(self, url, timeout=None):
        return self.robots


def browser(script, robots=make_response(404), **kw):
    sleeps, page = [], FakePage(script)
    f = BrowserFetcher(user_agent="ua/1", session=RobotsSession(robots), sleep=sleeps.append,
                       clock=lambda: 0.0, page_factory=lambda: page, delay_seconds=0, **kw)
    return f.open(), page, sleeps


def test_browser_fetcher_retries_with_backoff():
    from playwright.sync_api import TimeoutError as PWTimeout
    f, page, sleeps = browser([PWTimeout("slow"), (503, ""), (200, "<p>ok</p>")], backoff_seconds=1.0)
    assert f.get_html(P1) == "<p>ok</p>"
    assert sleeps == [1.0, 2.0] and len(page.calls) == 3
    assert page.calls[0][1] == f.timeout_seconds * 1000


def test_browser_fetcher_respects_robots_disallow():
    robots = make_response(200, b"User-agent: *\nDisallow: /js/\n", "text/plain")
    f, page, _ = browser([(200, "x")], robots=robots)
    with pytest.raises(RobotsDisallowed):
        f.get_html(P1)
    assert page.calls == []


def test_browser_fetcher_404_not_retried():
    f, page, _ = browser([(404, "")], max_retries=3)
    with pytest.raises(FetchError, match="404"):
        f.get_html(P1)
    assert len(page.calls) == 1


def test_scrape_quotes_follows_pagination_with_max_pages():
    pages = {P1: read("js_page1_rendered.html"), P10: read("js_page10_rendered.html")}

    class Stub:
        def get_html(self, url):
            return pages.get(url, pages[P10])

    r = scrape_quotes(Stub(), max_pages=2)
    assert r.pages == 2 and len(r.raws) == 20 and r.failures == []


# ---- real headless Chromium, offline -------------------------------------------------
def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.browser
def test_real_browser_renders_saved_raw_page_offline():
    if not _chromium_available():
        missing("Playwright Chromium not installed", "REQUIRE_BROWSER")
    from playwright.sync_api import sync_playwright
    raw, jq = read("js_page1_raw.html"), (FX / "jquery.js").read_bytes()
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()

        def handle(route):
            u = route.request.url
            if u.endswith("/static/jquery.js"):
                route.fulfill(status=200, body=jq, content_type="application/javascript")
            elif u.rstrip("/") == P1.rstrip("/"):
                route.fulfill(status=200, body=raw, content_type="text/html; charset=utf-8")
            else:
                route.abort()  # css etc.: never touch the network
        page.route("**/*", handle)
        f = BrowserFetcher(user_agent="ua/1", session=RobotsSession(make_response(404)),
                           page_factory=lambda: page, delay_seconds=0, timeout_seconds=10).open()
        html = f.get_html(P1)
        b.close()
    quotes, nxt = parse_quotes_page(html, P1)
    assert len(quotes) == 10 and quotes[0].author == "Albert Einstein"
    assert nxt == "https://quotes.toscrape.com/js/page/2/"
