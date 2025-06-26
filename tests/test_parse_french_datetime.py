import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from utils import parse_french_datetime


def test_parse_french_weekday_hour():
    dt = parse_french_datetime("samedi 21h", tz="Europe/Paris")
    assert dt is not None
    assert dt.hour == 21

