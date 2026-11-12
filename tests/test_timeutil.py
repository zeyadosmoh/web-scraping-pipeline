from datetime import datetime, timezone

import pytest

from scraper.timeutil import excel_local, to_report_tz, utc_now


def test_utc_now_is_aware_utc():
    assert utc_now().utcoffset().total_seconds() == 0


@pytest.mark.parametrize("utc,local", [
    (datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc), datetime(2026, 1, 15, 12, 0)),  # EET +2
    (datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc), datetime(2026, 7, 15, 13, 0)),  # EEST +3 (Egypt DST)
])
def test_cairo_display(utc, local):
    assert excel_local(utc) == local
    assert to_report_tz(utc).tzinfo is not None


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        to_report_tz(datetime(2026, 1, 1))
