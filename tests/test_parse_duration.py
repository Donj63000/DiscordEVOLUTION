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


def test_parse_duration_invalid():
    with pytest.raises(ValueError):
        parse_duration("abc")
