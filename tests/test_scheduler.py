from datetime import datetime, timedelta, timezone

from worker.scheduler import TZ_CN, upcoming_schedule_events

TZ = TZ_CN


def _cn(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_sunday_start_runs_monday_pipeline_before_friday_reminder():
    """周日启动时，应先等到周一流水线，而不是先等到周五提醒。"""
    ref = _cn(2026, 6, 28, 19, 47)  # 周日
    events = upcoming_schedule_events(ref=ref, reminder_day=4)
    assert [name for name, _ in events] == ["pipeline", "reminder"]
    assert events[0][1] == _cn(2026, 6, 29, 8, 0)
    assert events[1][1] == _cn(2026, 7, 3, 9, 0)


def test_wednesday_start_reminder_before_pipeline():
    """周三启动、周四提醒时，应先提醒再跑下周一流水线。"""
    ref = _cn(2026, 7, 1, 10, 0)  # 周三
    events = upcoming_schedule_events(ref=ref, reminder_day=3, reminder_hour=9)
    assert [name for name, _ in events] == ["reminder", "pipeline"]
    assert events[0][1] == _cn(2026, 7, 2, 9, 0)  # 周四
    assert events[1][1] == _cn(2026, 7, 6, 8, 0)  # 下周一


def test_monday_before_pipeline_time_runs_today():
    """周一流水线时间之前启动，当天流水线应排在首位。"""
    ref = _cn(2026, 6, 29, 7, 0)
    events = upcoming_schedule_events(ref=ref, reminder_day=4)
    assert events[0] == ("pipeline", _cn(2026, 6, 29, 8, 0))


def test_monday_after_pipeline_time_skips_to_next_week():
    """周一已过流水线时间，下次流水线应为下周一。"""
    ref = _cn(2026, 6, 29, 10, 0)
    events = upcoming_schedule_events(ref=ref, reminder_day=4)
    pipeline = next(dt for name, dt in events if name == "pipeline")
    assert pipeline == _cn(2026, 7, 6, 8, 0)
