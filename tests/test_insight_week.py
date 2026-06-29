from worker.crawl.week import context_range, week_range


def test_week_range_explicit():
    start_ts, end_ts, week_id = week_range("2026-W25")
    assert week_id == "2026-W25"
    assert end_ts > start_ts


def test_context_range_before_week():
    start_ts, _, week_id = week_range("2026-W25")
    ctx_start, ctx_end = context_range(start_ts)
    assert ctx_end == start_ts - 1
    assert ctx_start < ctx_end
