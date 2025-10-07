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

    Supported formats include ``"2h"``, ``"1:30"``, ``"1:30:45"``,
    ``"2h30"``, ``"1h 45m"`` or ``"90m"``. Seconds can be provided via the
    suffix ``s``/``sec`` and French words such as ``minutes`` or
    ``secondes`` are also accepted.
    """

    s = s.strip().lower()
    if not s:
        raise ValueError("Format de durée inconnu")

    if match := re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", s):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3) or 0)
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)

    normalized = s
    replacements = (
        ("heures", "h"),
        ("heure", "h"),
        ("hrs", "h"),
        ("hr", "h"),
        ("minutes", "m"),
        ("minute", "m"),
        ("mins", "m"),
        ("min", "m"),
        ("mn", "m"),
        ("secondes", "s"),
        ("seconde", "s"),
        ("seconds", "s"),
        ("second", "s"),
        ("secs", "s"),
        ("sec", "s"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

    normalized = re.sub(r"\bet\b", " ", normalized)
    normalized = re.sub(r"[+,/;\-]", " ", normalized)

    pattern = re.compile(r"(\d+)\s*([hms])")
    hours = minutes = seconds = 0
    matched_units: set[str] = set()
    for match in pattern.finditer(normalized):
        value = int(match.group(1))
        unit = match.group(2)
        matched_units.add(unit)
        if unit == "h":
            hours += value
        elif unit == "m":
            minutes += value
        else:
            seconds += value

    remaining = pattern.sub(" ", normalized)
    extras = re.findall(r"\d+", remaining)
    if extras:
        if matched_units.intersection({"h", "m"}) or not matched_units:
            for extra in extras:
                minutes += int(extra)
            remaining = re.sub(r"\d+", " ", remaining)
        else:
            raise ValueError("Format de durée inconnu")
    else:
        remaining = re.sub(r"\d+", " ", remaining)
    if remaining.strip():
        raise ValueError("Format de durée inconnu")

    if hours == minutes == seconds == 0:
        raise ValueError("Format de durée inconnu")

    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


PARIS = ZoneInfo("Europe/Paris")


def parse_french_datetime(text: str) -> datetime | None:
    """Parse French date expressions and return an aware UTC ``datetime``.

    The helper relies on :mod:`dateparser` when available but also implements a
    minimal fallback to understand simple patterns such as ``"samedi 18h"`` when
    the dependency is missing. ``None`` is returned if no interpretation was
    possible.
    """

    text = text.strip()

    # Manual pattern first to avoid dateparser quirks (ex: returning midnight for "samedi 21h")
    base = datetime.now(PARIS)
    match = re.fullmatch(
        r"(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})h",
        text,
        re.IGNORECASE,
    )
    if match:
        weekday_fr = match.group(1).lower()
        hour = int(match.group(2))
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
        manual_dt = datetime.combine(date, time(hour, 0), tzinfo=PARIS)
        return manual_dt.astimezone(timezone.utc)

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

    return dt.astimezone(timezone.utc) if dt else None


def parse_fr_datetime(text: str) -> datetime | None:
    """Backward compatibility wrapper for :func:`parse_french_datetime`."""

    return parse_french_datetime(text)

