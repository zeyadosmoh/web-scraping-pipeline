"""Polite fetcher: encoding, robots.txt, retries and rate limit. Offline (fake session)."""
import pytest
import requests

from scraper.http import FetchError, PoliteFetcher, RobotsDisallowed, decode_html
from tests.conftest import make_response

UA = "test-agent/1.0"


class FakeSession:
    """Returns queued results per URL; an Exception in the queue is raised instead."""

    def __init__(self, routes):
        self.routes = {u: list(v) for u, v in routes.items()}
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append((url, timeout))
        item = self.routes[url].pop(0) if len(self.routes[url]) > 1 else self.routes[url][0]
        if isinstance(item, Exception):
            raise item
        return item


def fetcher(routes, **kw):
    sleeps = []
    f = PoliteFetcher(user_agent=UA, session=FakeSession(routes), sleep=sleeps.append,
                      clock=lambda: 0.0, **kw)
    return f, sleeps


ROBOTS_404 = make_response(404, b"not found")
PAGE = "https://books.toscrape.com/catalogue/x/index.html"
ROBOTS = "https://books.toscrape.com/robots.txt"


# ---- encoding ---------------------------------------------------------------
def test_naive_requests_decoding_produces_mojibake(fixture_bytes):
    """Shows the bug: no charset in the header means requests guesses ISO-8859-1."""
    resp = make_response(200, fixture_bytes("books/detail_sapiens.html"))
    assert "Â£54.23" in resp.text


def test_decode_html_forces_utf8(fixture_bytes):
    resp = make_response(200, fixture_bytes("books/detail_sapiens.html"))
    html = decode_html(resp)
    assert "£54.23" in html and "Â£" not in html


def test_fetcher_returns_utf8_text(fixture_bytes):
    f, _ = fetcher({ROBOTS: [ROBOTS_404], PAGE: [make_response(200, fixture_bytes("books/detail_sapiens.html"))]})
    html = f.get_html(PAGE)
    assert "£54.23" in html and "Â£" not in html


# ---- robots.txt -------------------------------------------------------------
def test_robots_404_means_allowed():
    f, _ = fetcher({ROBOTS: [ROBOTS_404], PAGE: [make_response(200, b"ok")]})
    assert f.get_html(PAGE) == "ok"


def test_robots_disallow_skips_without_fetching():
    robots = make_response(200, b"User-agent: *\nDisallow: /catalogue/\n", "text/plain")
    f, _ = fetcher({ROBOTS: [robots], PAGE: [make_response(200, b"ok")]})
    with pytest.raises(RobotsDisallowed):
        f.get_html(PAGE)
    assert [u for u, _ in f.session.calls] == [ROBOTS]  # page never requested


def test_robots_allow_rules_are_respected():
    robots = make_response(200, b"User-agent: *\nDisallow: /admin/\n", "text/plain")
    f, _ = fetcher({ROBOTS: [robots], PAGE: [make_response(200, b"ok")]})
    assert f.get_html(PAGE) == "ok"


def test_robots_5xx_means_disallowed():
    f, _ = fetcher({ROBOTS: [make_response(503)], PAGE: [make_response(200, b"ok")]}, max_retries=0)
    with pytest.raises(RobotsDisallowed):
        f.get_html(PAGE)


def test_robots_fetched_once_per_host():
    f, _ = fetcher({ROBOTS: [ROBOTS_404], PAGE: [make_response(200, b"ok")]})
    f.get_html(PAGE)
    f.get_html(PAGE)
    assert [u for u, _ in f.session.calls].count(ROBOTS) == 1


# ---- retries, backoff, timeout, user agent ------------------------------------
def test_retries_with_exponential_backoff_then_succeeds():
    page = [requests.Timeout("slow"), make_response(503), make_response(200, b"ok")]
    f, sleeps = fetcher({ROBOTS: [ROBOTS_404], PAGE: page}, max_retries=3, backoff_seconds=1.0, delay_seconds=0)
    assert f.get_html(PAGE) == "ok"
    assert sleeps == [1.0, 2.0]


def test_gives_up_after_max_retries():
    f, sleeps = fetcher({ROBOTS: [ROBOTS_404], PAGE: [requests.ConnectionError("down")]},
                        max_retries=2, backoff_seconds=0.5, delay_seconds=0)
    with pytest.raises(FetchError, match="3 attempts"):
        f.get_html(PAGE)
    assert sleeps == [0.5, 1.0]


def test_404_page_is_not_retried():
    f, _ = fetcher({ROBOTS: [ROBOTS_404], PAGE: [make_response(404)]}, max_retries=3)
    with pytest.raises(FetchError, match="404"):
        f.get_html(PAGE)
    assert [u for u, _ in f.session.calls].count(PAGE) == 1


def test_timeout_and_user_agent_are_sent():
    f, _ = fetcher({ROBOTS: [ROBOTS_404], PAGE: [make_response(200, b"ok")]}, timeout_seconds=7)
    f.get_html(PAGE)
    assert f.session.headers["User-Agent"] == UA
    assert all(t == 7 for _, t in f.session.calls)


def test_rate_limit_waits_between_requests():
    times = iter([0.0, 0.2, 0.2])  # stamp #1 at t=0; check and stamp #2 at t=0.2
    sleeps = []
    f = PoliteFetcher(user_agent=UA, delay_seconds=1.0, sleep=sleeps.append, clock=lambda: next(times),
                      session=FakeSession({ROBOTS: [ROBOTS_404], PAGE: [make_response(200, b"ok")]}))
    f._request(PAGE)
    f._request(PAGE)
    assert sleeps == [pytest.approx(0.8)]
