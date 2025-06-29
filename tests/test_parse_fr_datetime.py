import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import timezone
from zoneinfo import ZoneInfo
from utils import parse_fr_datetime, parse_french_datetime


def test_parse_french_weekday_hour():
    dt = parse_fr_datetime("samedi 21h")
    assert dt is not None
    assert dt.tzinfo is timezone.utc
    paris = ZoneInfo("Europe/Paris")
    assert dt.astimezone(paris).hour == 21


def test_parse_french_datetime_alias():
    dt_alias = parse_french_datetime("samedi 21h")
    dt_ref = parse_fr_datetime("samedi 21h")
    assert dt_alias == dt_ref

