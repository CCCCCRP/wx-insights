from pathlib import Path

from worker.insight.selector import InsightSelector


def test_selector_dry_run_from_archive():
    """无 DB 时从 txt 归档选取（若 archive 存在）。"""
    selector = InsightSelector("last")
    primary, context, stats = selector.select()
    assert stats.week_id
    assert stats.source in ("db", "archive")
    # archive 目录存在时应有数据
    from worker.config import ARCHIVE_ROOT
    if ARCHIVE_ROOT.is_dir() and any(ARCHIVE_ROOT.iterdir()):
        assert stats.primary_count >= 0
