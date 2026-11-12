"""Polite HTTP fetcher: robots.txt, rate limit, retries with backoff, timeouts, clear User-Agent.

books.toscrape.com serves ``Content-Type: text/html`` with no charset. ``requests`` then
falls back to ISO-8859-1 and "£" becomes "Â£". The bytes really are UTF-8, so the fetcher
always decodes with ``force_encoding`` (UTF-8 by default).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    """The URL is disallowed by robots.txt; callers skip and log it."""


class FetchError(Exception):
    """The request still failed after all retries."""


def decode_html(resp: requests.Response, encoding: str = "utf-8") -> str:
    """Decode the response body with an explicit encoding, ignoring the header guess."""
    return resp.content.decode(encoding, errors="replace")


@dataclass
class PoliteFetcher:
    user_agent: str
    delay_seconds: float = 1.0
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_seconds: float = 1.0
    force_encoding: str = "utf-8"
    session: requests.Session | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict, init=False)
    _last_request_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        self.session.headers["User-Agent"] = self.user_agent

    # ---- robots.txt ---------------------------------------------------
    def _robots_for(self, url: str) -> RobotFileParser | None:
        """Return the parser for this host, or None when the host has no rules (allow all)."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser(robots_url)
        try:
            resp = self._request(robots_url)
        except FetchError as exc:
            log.warning("robots.txt unreachable at %s (%s): treating host as disallowed", robots_url, exc)
            rp.disallow_all = True
        else:
            if resp.status_code >= 500:
                log.warning("robots.txt %s returned %s: treating host as disallowed", robots_url, resp.status_code)
                rp.disallow_all = True
            elif resp.status_code >= 400:
                log.info("robots.txt %s returned %s: no rules, all paths allowed", robots_url, resp.status_code)
                rp = None
            else:
                rp.parse(decode_html(resp, self.force_encoding).splitlines())
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # ---- requests ----------------------------------------------------
    def _wait_for_rate_limit(self) -> None:
        if self._last_request_at is not None:
            remaining = self.delay_seconds - (self.clock() - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.clock()

    def _retrying(self, url: str, attempt: Callable[[], tuple[object, int]],
                  retry_on: tuple[type[BaseException], ...] = (requests.Timeout, requests.ConnectionError)):
        """Run `attempt` (returns (result, status)) with rate limit, retries and exponential backoff.

        Retries on the given exceptions and on 429/5xx. Shared by the HTTP and browser fetchers.
        """
        last_error: str = ""
        for n in range(self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                result, status = attempt()
            except retry_on as exc:
                last_error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            else:
                if status not in RETRY_STATUSES:
                    return result, status
                last_error = f"HTTP {status}"
            if n < self.max_retries:
                wait = self.backoff_seconds * (2 ** n)
                log.warning("GET %s failed (%s); retry %d/%d in %.1fs",
                            url, last_error, n + 1, self.max_retries, wait)
                self.sleep(wait)
        raise FetchError(f"GET {url} failed after {self.max_retries + 1} attempts: {last_error}")

    def _request(self, url: str) -> requests.Response:
        """GET with rate limit and retries. Returns any non-retryable response (including 4xx)."""
        def attempt():
            resp = self.session.get(url, timeout=self.timeout_seconds)
            return resp, resp.status_code
        resp, _ = self._retrying(url, attempt)
        return resp

    def get_html(self, url: str) -> str:
        """Fetch a page politely. Raises RobotsDisallowed, or FetchError on failure or non-200."""
        if not self.allowed(url):
            log.warning("robots.txt disallows %s: skipped", url)
            raise RobotsDisallowed(url)
        resp = self._request(url)
        if resp.status_code != 200:
            raise FetchError(f"GET {url} returned HTTP {resp.status_code}")
        return decode_html(resp, self.force_encoding)
