"""Build the two-round simulation catalogue from saved books.toscrape.com detail pages.

books.toscrape.com prices never change, so change tracking is demonstrated against
EDITED COPIES of real pages. Every generated file is marked as a simulation fixture.

  data/simulation/source/<slug>/index.html   real pages, saved once (politely)
  data/simulation/round1/...                 baseline catalogue (8 books)
  data/simulation/round2/...                 edited catalogue: price/stock changes,
                                             one removed, one new, one invalid row

Usage: .venv/bin/python scripts/build_simulation.py
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "simulation"
SRC = ROOT / "source"
MARK = "<!-- SIMULATION FIXTURE: edited copy of a books.toscrape.com page, not live data -->\n"

ROUND1 = [
    "a-light-in-the-attic_1000", "tipping-the-velvet_999", "soumission_998", "sharp-objects_997",
    "sapiens-a-brief-history-of-humankind_996", "the-requiem-red_995",
    "the-dirty-little-secrets-of-getting-your-dream-job_994",
    "the-coming-woman-a-novel-based-on-the-life-of-the-infamous-feminist-victoria-woodhull_993",
]
# round2 edits: slug -> (new price or None, new availability text or None)
EDITS = {
    "a-light-in-the-attic_1000": ("£45.99", None),                       # price drop
    "tipping-the-velvet_999": ("£58.00", None),                          # price rise
    "soumission_998": (None, "Out of stock"),                            # sold out
    "sharp-objects_997": (None, "In stock (4 available)"),               # stock down
    "sapiens-a-brief-history-of-humankind_996": ("£49.99", "In stock (7 available)"),  # both
}
REMOVED = "the-dirty-little-secrets-of-getting-your-dream-job_994"
NEW = "the-black-maria_991"
INVALID = "the-coming-woman-a-novel-based-on-the-life-of-the-infamous-feminist-victoria-woodhull_993"


def list_page(slugs: list[str], next_href: str | None) -> str:
    items = "\n".join(
        f'<li><article class="product_pod"><h3><a href="{s}/index.html" title="{s}">{s}</a></h3></article></li>'
        for s in slugs)
    pager = f'<ul class="pager"><li class="next"><a href="{next_href}">next</a></li></ul>' if next_href else ""
    return f"<!doctype html>\n{MARK}<html><body><ol class=\"row\">\n{items}\n</ol>{pager}</body></html>\n"


def edit_detail(html: str, price: str | None, availability: str | None) -> str:
    if price:
        old = re.search(r'<p class="price_color">(£[\d.]+)</p>', html).group(1)
        html = html.replace(old, price)
    if availability:
        html = re.sub(r"In stock \(\d+ available\)", availability, html)
    return html


def build_round(name: str, slugs: list[str], edits: dict, invalid: str | None = None) -> None:
    out = ROOT / name
    shutil.rmtree(out, ignore_errors=True)
    cat = out / "catalogue"
    half = (len(slugs) + 1) // 2
    cat.mkdir(parents=True)
    (cat / "page-1.html").write_text(list_page(slugs[:half], "page-2.html"), encoding="utf-8")
    (cat / "page-2.html").write_text(list_page(slugs[half:], None), encoding="utf-8")
    for s in slugs:
        html = (SRC / s / "index.html").read_text(encoding="utf-8")
        price, avail = edits.get(s, (None, None))
        html = edit_detail(html, price, avail)
        if s == invalid:  # a malformed price, to show a rejection with a reason
            html = re.sub(r'<p class="price_color">£[\d.]+</p>', '<p class="price_color">£--.--</p>', html, count=1)
        (cat / s).mkdir()
        (cat / s / "index.html").write_text(MARK + html, encoding="utf-8")


if __name__ == "__main__":
    build_round("round1", ROUND1, {})
    round2 = [s for s in ROUND1 if s != REMOVED] + [NEW]
    build_round("round2", round2, EDITS, invalid=INVALID)
    print("built", ROOT / "round1", ROOT / "round2")
