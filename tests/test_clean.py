"""Cleaning and validation rules (no network)."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from scraper.books import RawBook, parse_detail_page
from scraper.clean import (CleanError, clean_book, clean_books, clean_text, parse_price,
                           parse_rating, parse_stock)
from scraper.http import decode_html
from tests.conftest import make_response

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
GOOD = RawBook(url="u1", title="  A  Light\xa0in the Attic ", price_text="£51.77",
               availability_text="In stock (22 available)", rating_word="Three",
               category=" Poetry ", upc="a897fe39b1053632")


# ---- price --------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("£51.77", Decimal("51.77")), (" £ 10.5 ", Decimal("10.50")), ("20", Decimal("20.00")),
])
def test_price_to_decimal_gbp(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text,reason", [
    (None, "missing"), ("", "missing"), ("Â£51.77", "mojibake"), ("$51.77", "format"),
    ("£51.777", "format"), ("£0.00", "greater than 0"), ("abc", "format"),
])
def test_bad_prices_are_rejected_with_reason(text, reason):
    with pytest.raises(CleanError, match=reason):
        parse_price(text)


def test_price_from_fixture_bytes_end_to_end(fixture_bytes):
    """Server bytes -> forced UTF-8 decode -> parse -> Decimal. Proves the £ encoding fix."""
    resp = make_response(200, fixture_bytes("books/detail_sapiens.html"))
    raw = parse_detail_page(decode_html(resp), "u")
    assert parse_price(raw.price_text) == Decimal("54.23")
    # The naive requests decode would have been rejected, not silently misparsed:
    naive = parse_detail_page(resp.text, "u")
    with pytest.raises(CleanError, match="mojibake"):
        parse_price(naive.price_text)


# ---- stock / rating / text ------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("In stock (22 available)", 22), ("  in stock (1 available) ", 1), ("Out of stock", 0),
])
def test_stock_text_to_int(text, expected):
    assert parse_stock(text) == expected


@pytest.mark.parametrize("text", [None, "", "In stock", "Available soon"])
def test_bad_stock_rejected(text):
    with pytest.raises(CleanError):
        parse_stock(text)


@pytest.mark.parametrize("word,n", [("One", 1), ("two", 2), (" Three ", 3), ("Four", 4), ("FIVE", 5)])
def test_rating_words(word, n):
    assert parse_rating(word) == n


@pytest.mark.parametrize("word", [None, "", "Zero", "Six", "3"])
def test_bad_rating_rejected(word):
    with pytest.raises(CleanError, match="rating"):
        parse_rating(word)


def test_clean_text_trims_and_collapses():
    assert clean_text("  a \n\t b\xa0 c ") == "a b c"
    assert clean_text(None) == ""


# ---- whole rows -----------------------------------------------------------------
def test_clean_book_valid_row():
    p = clean_book(GOOD, "live", NOW)
    assert (p.title, p.category, p.price_gbp, p.stock, p.rating, p.source) == (
        "A Light in the Attic", "Poetry", Decimal("51.77"), 22, 3, "live")
    assert p.scraped_at.tzinfo is not None


def test_clean_book_requires_aware_utc():
    with pytest.raises(ValueError):
        clean_book(GOOD, "live", datetime(2026, 11, 12))


def test_clean_books_dedupes_by_upc_and_logs_rejections():
    rows = [
        GOOD,
        replace(GOOD, url="u2"),                                   # duplicate UPC
        replace(GOOD, url="u3", upc="b1", price_text="£abc"),      # bad UPC (too short) -> upc first
        replace(GOOD, url="u4", upc="c0ffee1234567890", price_text="free"),
        replace(GOOD, url="u5", upc="d0ffee1234567890", rating_word="Six"),
        replace(GOOD, url="u6", upc="e0ffee1234567890", title="   "),
        replace(GOOD, url="u7", upc="f0ffee1234567890", availability_text="Out of stock"),
    ]
    products, rejections = clean_books(rows, "live", NOW)
    assert [p.url for p in products] == ["u1", "u7"]
    assert products[1].stock == 0
    by_url = {r.url: r for r in rejections}
    assert set(by_url) == {"u2", "u3", "u4", "u5", "u6"}
    assert "duplicate UPC" in by_url["u2"].reason and "u1" in by_url["u2"].reason
    assert by_url["u3"].field == "upc"
    assert (by_url["u4"].field, by_url["u4"].value) == ("price", "free")
    assert by_url["u5"].field == "rating"
    assert by_url["u6"].field == "title"


def test_clean_fixture_pages(fixture_html):
    raws = [parse_detail_page(fixture_html(f"books/{n}.html"), n)
            for n in ("detail_sapiens", "detail_light_in_the_attic", "detail_black_maria")]
    products, rejections = clean_books(raws, "live", NOW)
    assert rejections == [] and len(products) == 3
    assert all(isinstance(p.price_gbp, Decimal) and 1 <= p.rating <= 5 and p.stock > 0 for p in products)
