"""Excel and CSV output shape (in-memory data, no DB or network)."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from scraper.changes import Change
from scraper.export import BANNER, SHEETS, write_csv, write_excel
from scraper.report import LogRow, ReportData, ReportProduct, RunInfo, summarize

D = Decimal
T0 = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)   # 08:00 in Cairo (EET, UTC+2)
T1 = T0 + timedelta(minutes=20)


def data(source="live"):
    prods = [
        ReportProduct("u1", "Alpha", "Poetry", D("10.00"), 5, 3, "https://x/1", source, T0, T1),
        ReportProduct("u2", "Beta", "Poetry", D("20.01"), 0, 5, "https://x/2", source, T0, T1),
        ReportProduct("u3", "Gamma", "History", D("33.33"), 2, 1, "https://x/3", source, T0, T1),
        ReportProduct("u4", "Gone", "History", D("99.00"), 1, 2, "https://x/4", source, T0, T0, removed_at=T1),
    ]
    changes = [
        Change("u1", "Alpha", "Poetry", "price", D("12.00"), D("10.00"), 5, 5, source, T1),
        Change("u4", "Gone", "History", "removed", D("99.00"), None, 1, None, source, T1),
    ]
    log = [LogRow(T1, "https://x/9", "u9", "price", "£--.--", "price not in £0.00 format", source)]
    run = RunInfo(f"{source}-r1", source, T0, T1, None, True, False, 5, 4, 1, 0)
    return ReportData(source, run, prods, changes, log, generated_at=T1)


@pytest.fixture
def live_wb(tmp_path):
    return load_workbook(write_excel(data("live"), tmp_path / "live.xlsx"))


@pytest.fixture
def sim_wb(tmp_path):
    return load_workbook(write_excel(data("simulation"), tmp_path / "sim.xlsx"))


def header(ws, row):
    return [c.value for c in ws[row] if c.value is not None]


def test_sheet_names_and_order(live_wb, sim_wb):
    assert live_wb.sheetnames == SHEETS == ["All products", "Changes since last run", "Summary", "Import_Log"]
    assert sim_wb.sheetnames == SHEETS


def test_headers(live_wb):
    assert header(live_wb["All products"], 1) == [
        "UPC", "Title", "Category", "Price (GBP)", "Stock", "Rating (1-5)", "Status",
        "First seen (Africa/Cairo)", "Last seen (Africa/Cairo)", "Removed at (Africa/Cairo)", "URL"]
    assert header(live_wb["Changes since last run"], 1) == [
        "Change", "UPC", "Title", "Category", "Old price (GBP)", "New price (GBP)", "Price change (GBP)",
        "Old stock", "New stock", "Detected at (Africa/Cairo)"]
    assert header(live_wb["Summary"], 1)[:7] == [
        "Category", "Products", "Avg price (GBP)", "Min price (GBP)", "Max price (GBP)", "Out of stock",
        "Units in stock"]
    assert header(live_wb["Import_Log"], 1) == [
        "Logged at (Africa/Cairo)", "URL", "UPC", "Field", "Value", "Reason"]


def test_frozen_header_and_autofilter(live_wb, sim_wb):
    for ws in live_wb.worksheets:
        assert ws.freeze_panes == "A2", ws.title
        assert ws.auto_filter.ref and ws.auto_filter.ref.startswith("A1:"), ws.title
        assert ws["A1"].font.bold
    for ws in sim_wb.worksheets:  # banner on row 1 pushes the header to row 2
        assert ws.freeze_panes == "A3", ws.title
        assert ws.auto_filter.ref.startswith("A2:"), ws.title
    assert live_wb["All products"].auto_filter.ref == "A1:K5"


def test_gbp_number_format_and_values(live_wb):
    ws = live_wb["All products"]
    assert ws["D2"].number_format == '"£"#,##0.00' and ws["D2"].value == 10
    ch = live_wb["Changes since last run"]
    assert "£" in ch["E2"].number_format and "£" in ch["F2"].number_format and "£" in ch["G2"].number_format
    assert ch["G2"].value == -2
    assert "£" in live_wb["Summary"]["C2"].number_format


def test_sheets_are_ltr(live_wb, sim_wb):
    for wb in (live_wb, sim_wb):
        for ws in wb.worksheets:
            assert not ws.sheet_view.rightToLeft, ws.title
            assert ws["A2"].alignment.readingOrder == 1, ws.title


def test_cairo_times_shown_in_local_time(live_wb):
    ws = live_wb["All products"]
    assert ws["H2"].value == datetime(2026, 11, 12, 8, 0)          # 06:00 UTC -> 08:00 Cairo
    assert ws["H2"].number_format == "yyyy-mm-dd hh:mm:ss"
    assert live_wb["Changes since last run"]["J2"].value == datetime(2026, 11, 12, 8, 20)


def test_simulation_banner_only_on_simulated_output(live_wb, sim_wb):
    for ws in sim_wb.worksheets:
        assert ws["A1"].value == BANNER, ws.title
    for ws in live_wb.worksheets:
        assert all("SIMULATION" not in str(c.value or "") for row in ws.iter_rows() for c in row), ws.title


def test_removed_rows_listed_and_summary_counts_active_only(live_wb):
    ch = live_wb["Changes since last run"]
    assert [ch.cell(r, 1).value for r in (2, 3)] == ["price", "removed"]
    assert ch["F3"].value is None
    sm = summarize(data("live"))
    assert (sm.active_products, sm.removed_products, sm.out_of_stock, sm.units_in_stock) == (3, 1, 1, 7)
    assert sm.avg_price == (D("10.00") + D("20.01") + D("33.33")) / 3   # exact, not rounded
    ws = live_wb["Summary"]
    metrics = {ws.cell(r, 9).value: ws.cell(r, 10).value for r in range(2, ws.max_row + 1)}
    assert metrics["Active products"] == 3 and metrics["Out of stock"] == 1
    assert metrics["Report source"] == "live"


def test_report_data_rejects_mixed_sources():
    d = data("live")
    with pytest.raises(ValueError, match="other sources"):
        ReportData("live", d.run, d.products + data("simulation").products[:1], [], [], T1)


def test_csv_outputs(tmp_path):
    paths = write_csv(data("live"), tmp_path)
    assert [p.name for p in paths] == ["products_live.csv", "changes_live.csv", "import_log_live.csv"]
    raw = paths[0].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    rows = list(csv.DictReader(paths[0].open(encoding="utf-8-sig")))
    assert len(rows) == 4 and rows[0]["price_gbp"] == "10.00" and rows[0]["first_seen_utc"].endswith("Z")
    assert rows[3]["status"] == "removed"
    changes = list(csv.DictReader(paths[1].open(encoding="utf-8-sig")))
    assert changes[1]["change_type"] == "removed" and changes[1]["new_price_gbp"] == ""
    log = list(csv.DictReader(paths[2].open(encoding="utf-8-sig")))
    assert log[0]["value"] == "£--.--"


# ---- exact cell positions per change type; header widths ---------------------------
from scraper.export import header_width  # noqa: E402


def test_change_rows_exact_cell_positions(tmp_path):
    chg = [
        Change("n1", "New book", "Poetry", "new", None, D("52.15"), None, 19, "live", T1),
        Change("r1", "Gone", "History", "removed", D("33.34"), None, 19, None, "live", T1),
        Change("p1", "Pricey", "Fiction", "price", D("53.74"), D("58.00"), 20, 20, "live", T1),
        Change("s1", "Stocky", "Fiction", "stock", D("50.10"), D("50.10"), 20, 0, "live", T1),
        Change("b1", "Both", "History", "price+stock", D("54.23"), D("49.99"), 20, 7, "live", T1),
    ]
    d = data("live")
    d.changes = chg
    ws = load_workbook(write_excel(d, tmp_path / "c.xlsx"))["Changes since last run"]
    # Columns: A Change, B UPC, ..., E Old price, F New price, G Price change, H Old stock, I New stock
    assert [ws.cell(1, c).value for c in (5, 6, 7, 8, 9)] == [
        "Old price (GBP)", "New price (GBP)", "Price change (GBP)", "Old stock", "New stock"]
    rows = {ws.cell(r, 1).value: [ws.cell(r, c).value for c in (5, 6, 7, 8, 9)] for r in range(2, 7)}
    assert rows["new"] == [None, 52.15, None, None, 19]          # old side blank, new side filled
    assert rows["removed"] == [33.34, None, None, 19, None]      # new side blank
    assert rows["price"] == [53.74, 58, 4.26, 20, 20]
    assert rows["stock"] == [50.1, 50.1, 0, 20, 0]
    assert rows["price+stock"] == [54.23, 49.99, -4.24, 20, 7]
    assert ws["H2"].coordinate == "H2" and ws["I2"].value == 19   # "new" stock lands under New stock


def test_every_column_is_at_least_as_wide_as_its_header(live_wb, sim_wb):
    from openpyxl.utils import get_column_letter
    for wb, header_row in ((live_wb, 1), (sim_wb, 2)):
        for ws in wb.worksheets:
            for c in range(1, ws.max_column + 1):
                text = ws.cell(header_row, c).value
                if not text:
                    continue
                width = ws.column_dimensions[get_column_letter(c)].width
                assert width >= header_width(str(text)) > len(str(text)), (ws.title, text, width)
