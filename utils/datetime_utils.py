"""Date and time helper utilities."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import dateparser


def parse_duration(s: str) -> timedelta:
    """Convert short duration expressions into a :class:`timedelta`.

    Supported formats include ``"2h"``, ``"1:30"`` or ``"90m"``.
    """

    s = s.strip().lower()

    if match := re.fullmatch(r"(\d+):(\d\d)", s):
        hours, minutes = map(int, match.groups())
        return timedelta(hours=hours, minutes=minutes)

    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))

    if s.endswith("m"):
        return timedelta(minutes=int(s[:-1]))

    raise ValueError("Format de durée inconnu")


def parse_french_datetime(text: str, tz: str = "Europe/Paris") -> datetime | None:
    """Parse French date expressions using ``dateparser``.

    Examples of supported inputs include ``"samedi 21h"``, ``"demain 20:30"``
    or ``"12/07 19h"``. The returned datetime is timezone-aware.
    """

    base = datetime.now(ZoneInfo(tz))
    dt = dateparser.parse(
        text,
        languages=["fr"],
        settings={
            "RELATIVE_BASE": base,
            "TIMEZONE": tz,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    return dt

