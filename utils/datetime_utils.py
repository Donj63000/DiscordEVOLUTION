"""Date and time helper utilities."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, time
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


def parse_french_datetime(text: str, tz: str = "Europe/Paris") -> datetime | None:
    """Parse French date expressions.

    Tries to use :mod:`dateparser` when available, otherwise falls back to a
    minimal built-in parser that understands simple patterns like
    ``"samedi 21h"``. The returned datetime is timezone-aware.
    """

    base = datetime.now(ZoneInfo(tz))

    if dateparser is not None:  # pragma: no cover - runtime dependency present
        return dateparser.parse(
            text,
            languages=["fr"],
            settings={
                "RELATIVE_BASE": base,
                "TIMEZONE": tz,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )

    # -------------- Fallback minimal parser --------------
    m = re.fullmatch(r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})h", text.strip(), re.IGNORECASE)
    if m:
        weekday_fr = m.group(1).lower()
        hour = int(m.group(2))
        days = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}
        target = days[weekday_fr]
        diff = (target - base.weekday()) % 7
        if diff == 0 and base.hour >= hour:
            diff = 7
        date = (base + timedelta(days=diff)).date()
        return datetime.combine(date, time(hour, 0), tzinfo=ZoneInfo(tz))

    return None

