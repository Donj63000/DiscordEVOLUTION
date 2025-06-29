"""Date and time helper utilities."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

try:  # pragma: no cover - allow running without the dependency
    import dateparser  # type: ignore
except Exception:  # noqa: PIE786 - optional dependency may be missing
    dateparser = None


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


PARIS = ZoneInfo("Europe/Paris")


def parse_french_datetime(text: str) -> datetime | None:
    """Parse French date expressions and return an aware UTC ``datetime``.

    The helper relies on :mod:`dateparser` when available but also implements a
    minimal fallback to understand simple patterns such as ``"samedi 18h"`` when
    the dependency is missing. ``None`` is returned if no interpretation was
    possible.
    """

    dt = None
    if dateparser is not None:  # pragma: no cover - runtime dependency present
        dt = dateparser.parse(
            text,
            languages=["fr"],
            settings={
                "TIMEZONE": str(PARIS),
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=PARIS)

    if dt is None:
        base = datetime.now(PARIS)
        m = re.fullmatch(
            r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})h",
            text.strip(),
            re.IGNORECASE,
        )
        if m:
            weekday_fr = m.group(1).lower()
            hour = int(m.group(2))
            days = {
                "lundi": 0,
                "mardi": 1,
                "mercredi": 2,
                "jeudi": 3,
                "vendredi": 4,
                "samedi": 5,
                "dimanche": 6,
            }
            target = days[weekday_fr]
            diff = (target - base.weekday()) % 7
            if diff == 0 and base.hour >= hour:
                diff = 7
            date = (base + timedelta(days=diff)).date()
            dt = datetime.combine(date, time(hour, 0), tzinfo=PARIS)

    return dt.astimezone(timezone.utc) if dt else None


def parse_fr_datetime(text: str) -> datetime | None:
    """Backward compatibility wrapper for :func:`parse_french_datetime`."""

    return parse_french_datetime(text)

