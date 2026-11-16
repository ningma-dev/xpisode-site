import datetime as dt

from xirra import stats


def ep(day: dt.date, hour: int, seconds: float, keep: bool | None, operator: str = "ana", task: str = "pick") -> dict:
    start = dt.datetime.combine(day, dt.time(hour), stats.TZ)
    return {
        "start_unix_ns": int(start.timestamp() * 1e9),
        "duration_ns": int(seconds * 1e9),
        "keep": keep,
        "operator": operator,
        "task": task,
        "station": "yam_1",
    }


def test_a_day_is_a_beijing_day_and_tallies_split_by_judgment() -> None:
    d = dt.date(2026, 11, 14)
    eps = [ep(d, 0, 60, True), ep(d, 23, 120, False), ep(d, 12, 30, None, "bo"), ep(d + dt.timedelta(days=1), 0, 99, True)]
    start, end = stats.day_bounds(d)
    day = stats.within(eps, start, end)
    assert len(day) == 3
    people = stats.by(day, "operator")
    assert people["ana"].count == 2 and people["ana"].kept == 1 and people["ana"].dropped == 1
    assert people["ana"].seconds == 180 and people["ana"].kept_seconds == 60
    assert people["bo"].unjudged == 1


def test_old_rows_whose_operator_is_plan_are_not_counted_as_a_person() -> None:
    d = dt.date(2026, 11, 10)
    people = stats.by([ep(d, 9, 10, True, "plan")], "operator")
    assert "plan" not in people


def test_hours_reads_well() -> None:
    assert stats.hours(1800) == "30 分钟"
    assert stats.hours(5400) == "1.5 小时"
