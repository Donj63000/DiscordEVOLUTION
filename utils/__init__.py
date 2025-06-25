
import re
from datetime import timedelta

__all__ = ["parse_duration"]


def parse_duration(text: str) -> timedelta:
    """Parse a short duration expression into a ``timedelta``.

    Supported formats include ``"2h"`` or ``"01:30"`` for one hour
    and thirty minutes.
    """

    t = text.strip().lower()

    if ":" in t:
        try:
            hours, minutes = t.split(":", 1)
            return timedelta(hours=int(hours), minutes=int(minutes))
        except Exception as exc:
            raise ValueError(f"Invalid duration: {text}") from exc

    match = re.fullmatch(r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*(?:m|min)?)?", t)
    if not match:
        raise ValueError(f"Invalid duration: {text}")

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)

    if hours == 0 and minutes == 0:
        raise ValueError(f"Invalid duration: {text}")

    return timedelta(hours=hours, minutes=minutes)
