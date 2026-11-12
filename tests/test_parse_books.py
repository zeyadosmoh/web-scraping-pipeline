"""Parsers on saved books.toscrape.com HTML (no network)."""
from scraper.books import RawBook, parse_detail_page, parse_list_page

HOME = "https://books.toscrape.com/index.html"
LAST = "https://books.toscrape.com/catalogue/page-50.html"
SAPIENS = "https://books.toscrape.com/catalogue/sapiens-a-brief-history-of-humankind_996/index.html"


def test_list_page_detail_urls_and_next(fixture_html):
    urls, nxt = parse_list_page(fixture_html("books/index.html"), HOME)
    assert len(urls) == 20
    assert urls[0] == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert nxt == "https://books.toscrape.com/catalogue/page-2.html"


def test_last_list_page_has_no_next_and_resolves_relative_urls(fixture_html):
    urls, nxt = parse_list_page(fixture_html("books/page-50.html"), LAST)
    assert nxt is None
    assert len(urls) == 20
    assert all(u.startswith("https://books.toscrape.com/catalogue/") and u.endswith("/index.html") for u in urls)


def test_detail_page_fields(fixture_html):
    b = parse_detail_page(fixture_html("books/detail_sapiens.html"), SAPIENS)
    assert b == RawBook(
        url=SAPIENS,
        title="Sapiens: A Brief History of Humankind",
        price_text="£54.23",
        availability_text="In stock (20 available)",
        rating_word="Five",
        category="History",
        upc="4165285e1663650f",
    )


def test_other_detail_pages(fixture_html):
    a = parse_detail_page(fixture_html("books/detail_light_in_the_attic.html"), "u1")
    assert (a.title, a.price_text, a.rating_word, a.category, a.upc) == (
        "A Light in the Attic", "£51.77", "Three", "Poetry", "a897fe39b1053632")
    assert a.availability_text == "In stock (22 available)"
    m = parse_detail_page(fixture_html("books/detail_black_maria.html"), "u2")
    assert m.title == "The Black Maria" and m.price_text.startswith("£") and m.upc


def test_detail_page_with_missing_parts_returns_none_fields():
    b = parse_detail_page("<html><body><div class='product_main'><h1> X </h1></div></body></html>", "u")
    assert b.title == "X"
    assert b.price_text is None and b.upc is None and b.rating_word is None and b.category is None
