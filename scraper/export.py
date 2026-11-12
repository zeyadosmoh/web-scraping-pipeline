"""Excel (openpyxl) and CSV outputs from a ReportData.

Excel layout, in order: All products | Changes since last run | Summary | Import_Log
- header row bold and filled, frozen, with an autofilter on every sheet
- prices use a £ number format and keep full precision (averages are not rounded in the cell)
- sheets are explicitly left-to-right and text cells use LTR reading order, so the file
  opens correctly in Arabic/RTL Excel installs
- times are stored in UTC and shown in the report timezone (Africa/Cairo), named in the header
- a simulation report has a red SIMULATION banner on row 1 of every sheet; a live report has none
CSV is UTF-8 with BOM (so Excel opens it correctly), with ISO-8601 UTC times.
"""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from scraper.report import ReportData, summarize
from scraper.timeutil import excel_local

SHEETS = ["All products", "Changes since last run", "Summary", "Import_Log"]
GBP = '"£"#,##0.00'
GBP_DELTA = '+"£"#,##0.00;-"£"#,##0.00;"£"0.00'
DATETIME = "yyyy-mm-dd hh:mm:ss"
BANNER = "SIMULATION: built from edited copies of saved pages. Not live data; never mixed into live reports."

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
BANNER_FONT = Font(bold=True, color="FFFFFF", size=12)
BANNER_FILL = PatternFill("solid", fgColor="C00000")
LTR = Alignment(horizontal="left", vertical="top", readingOrder=1)
LTR_NUM = Alignment(horizontal="right", vertical="top", readingOrder=1)
CHANGE_FILLS = {
    "new": PatternFill("solid", fgColor="E2EFDA"),
    "removed": PatternFill("solid", fgColor="F8CBAD"),
    "price": PatternFill("solid", fgColor="FFF2CC"),
    "stock": PatternFill("solid", fgColor="DDEBF7"),
    "price+stock": PatternFill("solid", fgColor="FCE4D6"),
}


def tz_label(tz: str) -> str:
    return f"({tz})"


class _Sheet:
    """Writes a table (with an optional banner) and applies the shared formatting."""

    def __init__(self, ws: Worksheet, simulation: bool):
        self.ws = ws
        ws.sheet_view.rightToLeft = False
        self.row = 1
        if simulation:
            ws.cell(1, 1, BANNER).font = BANNER_FONT
            ws.cell(1, 1).fill = BANNER_FILL
            ws.cell(1, 1).alignment = LTR
            ws.row_dimensions[1].height = 22
            self.row = 2
        self.header_row = self.row
        self.ncols = 0

    def header(self, names: list[str]) -> None:
        self.ncols = len(names)
        for c, name in enumerate(names, 1):
            cell = self.ws.cell(self.row, c, name)
            cell.font, cell.fill, cell.alignment = HEADER_FONT, HEADER_FILL, LTR
        if self.header_row == 2:  # stretch the banner across the table
            self.ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(self.ncols, 1))
        self.ws.freeze_panes = self.ws.cell(self.header_row + 1, 1)
        self.row += 1

    def add(self, values: list, formats: dict[int, str] | None = None, fill: PatternFill | None = None) -> None:
        formats = formats or {}
        for c, v in enumerate(values, 1):
            cell = self.ws.cell(self.row, c, v)
            if c in formats:
                cell.number_format = formats[c]
            cell.alignment = LTR_NUM if isinstance(v, (int, float, Decimal, datetime)) else LTR
            if fill is not None:
                cell.fill = fill
        self.row += 1

    def finish(self, autofilter: bool = True, widths: dict[int, int] | None = None) -> None:
        last = max(self.row - 1, self.header_row)
        if autofilter and self.ncols:
            self.ws.auto_filter.ref = f"A{self.header_row}:{get_column_letter(self.ncols)}{last}"
        widths = widths or {}
        for c in range(1, self.ncols + 1):
            if c in widths:
                w = widths[c]
            else:
                lengths = [len(str(self.ws.cell(r, c).value or "")) for r in range(self.header_row, last + 1)]
                w = min(max(lengths, default=8) + 2, 60)
            self.ws.column_dimensions[get_column_letter(c)].width = max(w, 10)


def _local(ts: datetime | None, tz: str) -> datetime | None:
    return excel_local(ts, tz) if ts else None


def write_excel(data: ReportData, path: Path, tz: str = "Africa/Cairo") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sim = data.is_simulation
    t = tz_label(tz)
    wb = Workbook()
    wb.remove(wb.active)

    # --- All products ---
    s = _Sheet(wb.create_sheet(SHEETS[0]), sim)
    s.header(["UPC", "Title", "Category", "Price (GBP)", "Stock", "Rating (1-5)", "Status",
              f"First seen {t}", f"Last seen {t}", f"Removed at {t}", "URL"])
    for p in data.products:
        s.add([p.upc, p.title, p.category, p.price_gbp, p.stock, p.rating,
               "Active" if p.active else "Removed", _local(p.first_seen_at, tz), _local(p.last_seen_at, tz),
               _local(p.removed_at, tz), p.url],
              {4: GBP, 8: DATETIME, 9: DATETIME, 10: DATETIME},
              fill=None if p.active else CHANGE_FILLS["removed"])
    s.finish(widths={2: 50, 8: 20, 9: 20, 10: 20, 11: 60})

    # --- Changes since last run ---
    s = _Sheet(wb.create_sheet(SHEETS[1]), sim)
    s.header(["Change", "UPC", "Title", "Category", "Old price (GBP)", "New price (GBP)", "Price change (GBP)",
              "Old stock", "New stock", f"Detected at {t}"])
    for c in data.changes:
        s.add([c.change_type, c.upc, c.title, c.category, c.old_price, c.new_price, c.price_delta,
               c.old_stock, c.new_stock, _local(c.detected_at, tz)],
              {5: GBP, 6: GBP, 7: GBP_DELTA, 10: DATETIME}, fill=CHANGE_FILLS.get(c.change_type))
    if not data.changes:
        s.add(["(no changes since the previous run)"])
    s.finish(widths={3: 50, 10: 20})

    # --- Summary ---
    sm = summarize(data)
    s = _Sheet(wb.create_sheet(SHEETS[2]), sim)
    s.header(["Category", "Products", "Avg price (GBP)", "Min price (GBP)", "Max price (GBP)",
              "Out of stock", "Units in stock"])
    money = {3: GBP, 4: GBP, 5: GBP}
    for cs in sm.by_category:
        s.add([cs.category, cs.products, cs.avg_price, cs.min_price, cs.max_price, cs.out_of_stock,
               cs.units_in_stock], money)
    s.finish(widths={1: 30, 2: 12, 3: 16, 4: 16, 5: 16, 6: 14, 7: 14})
    # Key figures go to the right of the category table, so the table keeps a clean autofilter range.
    ws, col = s.ws, 9
    run = data.run
    facts: list[tuple[str, object, str | None]] = [
        ("Report source", data.source, None),
        (f"Generated at {t}", _local(data.generated_at, tz), DATETIME),
        ("Run id", run.run_id if run else "-", None),
        (f"Run started {t}", _local(run.started_at, tz) if run else None, DATETIME),
        (f"Run finished {t}", _local(run.finished_at, tz) if run else None, DATETIME),
        ("Run duration (s)", round(run.duration_seconds, 1) if run else None, "0.0"),
        ("Complete catalogue run", ("yes" if run.complete else "no") if run else "-", None),
        ("Limit", (run.limit if run.limit is not None else "none") if run else "-", None),
        ("Rows scraped (this run)", run.rows_scraped if run else None, None),
        ("Rows valid (this run)", run.rows_valid if run else None, None),
        ("Rows rejected (this run)", run.rows_rejected if run else None, None),
        ("Fetch failures (this run)", run.fetch_failures if run else None, None),
        ("Active products", sm.active_products, None),
        ("Removed products", sm.removed_products, None),
        ("Categories", sm.categories, None),
        ("In stock", sm.in_stock, None),
        ("Out of stock", sm.out_of_stock, None),
        ("Units in stock", sm.units_in_stock, None),
        ("Average price (GBP)", sm.avg_price, GBP),
        ("Min price (GBP)", sm.min_price, GBP),
        ("Max price (GBP)", sm.max_price, GBP),
        *[(f"Rated {r} star{'s' if r > 1 else ''}", n, None) for r, n in sm.rating_counts.items()],
        *[(f"Changes: {k}", v, None) for k, v in sorted(sm.changes_by_type.items())],
    ]
    hr = s.header_row
    for c, name in ((col, "Metric"), (col + 1, "Value")):
        cell = ws.cell(hr, c, name)
        cell.font, cell.fill, cell.alignment = HEADER_FONT, HEADER_FILL, LTR
    for i, (label, value, fmt) in enumerate(facts, start=hr + 1):
        ws.cell(i, col, label).alignment = LTR
        v = ws.cell(i, col + 1, value)
        v.alignment = LTR_NUM if isinstance(value, (int, float, Decimal, datetime)) else LTR
        if fmt:
            v.number_format = fmt
    ws.column_dimensions[get_column_letter(col)].width = 30
    ws.column_dimensions[get_column_letter(col + 1)].width = 34

    # --- Import_Log ---
    s = _Sheet(wb.create_sheet(SHEETS[3]), sim)
    s.header([f"Logged at {t}", "URL", "UPC", "Field", "Value", "Reason"])
    for r in data.log:
        s.add([_local(r.logged_at, tz), r.url, r.upc, r.field, r.value, r.reason], {1: DATETIME})
    if not data.log:
        s.add(["(no rejected rows or failed URLs in this run)"])
    s.finish(widths={1: 20, 2: 60, 6: 50})

    wb.active = 0
    wb.save(path)
    return path


def _iso(ts: datetime | None) -> str:
    return ts.isoformat().replace("+00:00", "Z") if ts else ""


def write_csv(data: ReportData, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = data.source
    files = {
        f"products_{tag}.csv": (
            ["upc", "title", "category", "price_gbp", "stock", "rating", "status", "first_seen_utc",
             "last_seen_utc", "removed_at_utc", "url", "source"],
            [[p.upc, p.title, p.category, p.price_gbp, p.stock, p.rating, "active" if p.active else "removed",
              _iso(p.first_seen_at), _iso(p.last_seen_at), _iso(p.removed_at), p.url, p.source]
             for p in data.products]),
        f"changes_{tag}.csv": (
            ["change_type", "upc", "title", "category", "old_price_gbp", "new_price_gbp", "price_change_gbp",
             "old_stock", "new_stock", "detected_at_utc", "source"],
            [[c.change_type, c.upc, c.title, c.category, c.old_price, c.new_price, c.price_delta,
              c.old_stock, c.new_stock, _iso(c.detected_at), c.source] for c in data.changes]),
        f"import_log_{tag}.csv": (
            ["logged_at_utc", "url", "upc", "field", "value", "reason", "source"],
            [[_iso(r.logged_at), r.url, r.upc, r.field, r.value, r.reason, r.source] for r in data.log]),
    }
    paths = []
    for name, (header, rows) in files.items():
        p = out_dir / name
        with p.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows([["" if v is None else v for v in row] for row in rows])
        paths.append(p)
    return paths
