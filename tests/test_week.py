from datetime import datetime, timezone, timedelta

from worker.crawl.week import week_id_from_ts, year_range, week_range, TZ_CN


def test_week_id_from_ts():
    # 2026-06-15 属于 ISO 周 2026-W25
    ts = int(datetime(2026, 6, 15, 12, 0, 0, tzinfo=TZ_CN).timestamp())
    assert week_id_from_ts(ts) == "2026-W25"


def test_history_range_default_six_months():
    start_ts, end_ts, period_id = year_range()
    assert end_ts > start_ts
    assert (end_ts - start_ts) >= 179 * 86400
    assert period_id.startswith("6month-")


def test_week_range_last():
    start_ts, end_ts, week_id = week_range("last")
    assert end_ts > start_ts
    assert "-W" in week_id
