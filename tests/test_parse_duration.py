import os
import sys
from datetime import timedelta
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import parse_duration


def test_parse_duration_hours():
    assert parse_duration("2h") == timedelta(hours=2)


def test_parse_duration_hh_mm():
    assert parse_duration("01:30") == timedelta(hours=1, minutes=30)


def test_parse_duration_compound_units():
    assert parse_duration("2h30") == timedelta(hours=2, minutes=30)
    assert parse_duration("1h 15m 30s") == timedelta(hours=1, minutes=15, seconds=30)


def test_parse_duration_seconds_only():
    assert parse_duration("45s") == timedelta(seconds=45)


def test_parse_duration_invalid():
    with pytest.raises(ValueError):
        parse_duration("abc")


@pytest.mark.parametrize("value", ["-1h", "-30m", "1h -30m", "1:60", "1:00:60", "0:00", "999999999999999999999h"])
def test_parse_duration_rejects_invalid_or_unrepresentable_values(value):
    with pytest.raises(ValueError):
        parse_duration(value)
