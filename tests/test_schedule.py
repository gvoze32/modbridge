from datetime import datetime

import pytest

from modbridge.schedule import ScheduleWindow


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 9, hour, minute)


def test_simple_window() -> None:
    w = ScheduleWindow.parse("04:00-05:00")
    assert w.contains(at(4, 0))
    assert w.contains(at(4, 59))
    assert not w.contains(at(5, 0))
    assert not w.contains(at(10, 0))


def test_overnight_window() -> None:
    w = ScheduleWindow.parse("22:00-02:00")
    assert w.contains(at(23, 0))
    assert w.contains(at(1, 59))
    assert not w.contains(at(2, 0))
    assert not w.contains(at(12, 0))


@pytest.mark.parametrize("spec", ["4am-5am", "04:00", "25:00-26:00", "04:00-04:00", ""])
def test_invalid_windows(spec: str) -> None:
    with pytest.raises(ValueError):
        ScheduleWindow.parse(spec)


def test_str_roundtrip() -> None:
    assert str(ScheduleWindow.parse("04:00-05:00")) == "04:00-05:00"
