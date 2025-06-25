"""Date and time helper utilities."""

from __future__ import annotations

import re
from datetime import timedelta


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

