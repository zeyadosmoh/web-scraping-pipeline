"""Parsers for books.toscrape.com. They return raw strings; cleaning happens in clean.py."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://books.toscrape.com/"


@dataclass(frozen=True)
class RawBook:
    """Text exactly as found on the page (or None when missing)."""
    url: str
    title: str | None
    price_text: str | None
    availability_text: str | None
    rating_word: str | None
    category: str | None
    upc: str | None


def _text(node) -> str | None:
    return node.get_text(" ", strip=True) if node is not None else None


def parse_list_page(html: str, page_url: str) -> tuple[list[str], str | None]:
    """Return (absolute detail-page URLs, absolute next-page URL or None)."""
    soup = BeautifulSoup(html, "lxml")
    urls = [urljoin(page_url, a["href"])
            for a in soup.select("article.product_pod h3 a[href]")]
    nxt = soup.select_one("ul.pager li.next a[href]")
    return urls, (urljoin(page_url, nxt["href"]) if nxt else None)


def parse_detail_page(html: str, url: str) -> RawBook:
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("div.product_main")

    table = {(_text(tr.th) or ""): _text(tr.td) for tr in soup.select("table.table-striped tr")}

    rating = None
    if main is not None and (stars := main.select_one("p.star-rating")) is not None:
        rating = next((c for c in stars.get("class", []) if c != "star-rating"), None)

    # Breadcrumb: Home > Books > <Category> > <Title>
    crumbs = soup.select("ul.breadcrumb li")
    category = _text(crumbs[2]) if len(crumbs) >= 4 else None

    return RawBook(
        url=url,
        title=_text(main.h1) if main is not None else None,
        price_text=_text(main.select_one("p.price_color")) if main is not None else table.get("Price (incl. tax)"),
        availability_text=table.get("Availability") or (
            _text(main.select_one("p.availability")) if main is not None else None),
        rating_word=rating,
        category=category,
        upc=table.get("UPC"),
    )
