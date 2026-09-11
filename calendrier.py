"""Opaque, bounded Pillow rendering for the optional monthly overview."""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import importlib.util
import io
import logging
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from utils.calendar_data import CalendarEvent, MONTH_NAMES_FR, one_line, snapshot_events

log = logging.getLogger(__name__)
WIDTH = 1120
MARGIN = 28
GAP = 8
CELL_WIDTH = 145
CELL_HEIGHT = 132
GRID_TOP = 168
BACKGROUND = "#141923"
SURFACE = "#202735"
WEEKEND = "#252C3B"
TEXT = "#F4F6FC"
MUTED = "#BAC5DA"
ACCENT = "#99ABFF"
ACTIVITY = "#32436C"


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Use installed fonts only; never download assets on a command path."""
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = [filename]
    spec = importlib.util.find_spec("matplotlib")
    if spec is not None and spec.origin:
        candidates.append(str(Path(spec.origin).parent / "mpl-data" / "fonts" / "ttf" / filename))
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    log.debug("Calendar: using Pillow fallback font")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def fit_text(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    """Ellipsize using measured pixels, including unbroken user-provided titles."""
    text = one_line(text, 1000)
    if draw.textlength(text, font=font) <= width:
        return text
    suffix = "…"
    left, right = 0, len(text)
    while left < right:
        mid = (left + right + 1) // 2
        if draw.textlength(text[:mid] + suffix, font=font) <= width:
            left = mid
        else:
            right = mid - 1
    return text[:left].rstrip() + suffix


def render_month(
    events: Iterable[CalendarEvent], year: int, month: int, today: date | None = None,
) -> bytes:
    """Render at most two summaries per day; the paginated text remains authoritative."""
    weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    height = GRID_TOP + len(weeks) * (CELL_HEIGHT + GAP) + 66
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    small, body, bold = _font(16), _font(18), _font(18, True)
    time_font = _font(13, True)
    day_font, title_font = _font(28, True), _font(36, True)
    grouped: dict[int, list[CalendarEvent]] = {}
    ordered = sorted(events, key=lambda event: (event.timestamp, event.id))
    for event in ordered:
        if (event.day.year, event.day.month) == (year, month):
            grouped.setdefault(event.day.day, []).append(event)

    draw.rounded_rectangle((28, 25, 66, 63), radius=11, fill=ACCENT)
    draw.text((39, 30), "E", font=_font(25, True), fill=BACKGROUND)
    draw.text((80, 29), "EVOLUTION  /  ACTIVITÉS", font=bold, fill=MUTED)
    draw.text((28, 75), f"{MONTH_NAMES_FR[month]} {year}", font=title_font, fill=TEXT)
    count = sum(len(day_events) for day_events in grouped.values())
    count_text = f"{count} activité{'s' if count != 1 else ''}"
    draw.text((WIDTH - 28 - draw.textlength(count_text, font=bold), 90),
              count_text, font=bold, fill=ACCENT)

    for col, label in enumerate(("LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM")):
        x = MARGIN + col * (CELL_WIDTH + GAP)
        draw.text((x + 12, 140), label, font=bold, fill=MUTED)

    for row, week in enumerate(weeks):
        for col, day in enumerate(week):
            x = MARGIN + col * (CELL_WIDTH + GAP)
            y = GRID_TOP + row * (CELL_HEIGHT + GAP)
            if not day:
                continue
            is_today = today == date(year, month, day)
            draw.rounded_rectangle(
                (x, y, x + CELL_WIDTH, y + CELL_HEIGHT), radius=12,
                fill=WEEKEND if col >= 5 else SURFACE,
                outline=ACCENT if is_today else None, width=2,
            )
            if is_today:
                draw.rounded_rectangle((x + 9, y + 8, x + 53, y + 47), radius=10, fill=ACCENT)
            draw.text((x + 13, y + 8), str(day), font=day_font,
                      fill=BACKGROUND if is_today else TEXT)
            items = grouped.get(day, [])
            if len(items) > 2:
                more = f"+{len(items) - 2}"
                count_width = draw.textlength(more, font=time_font)
                draw.text((x + CELL_WIDTH - 12 - count_width, y + 19), more,
                          font=time_font, fill=ACCENT)
            elif items:
                draw.ellipse((x + CELL_WIDTH - 21, y + 20, x + CELL_WIDTH - 13, y + 28),
                             fill=ACCENT)
            for index, event in enumerate(items[:2]):
                line_y = y + 50 + index * 40
                draw.rounded_rectangle(
                    (x + 8, line_y, x + CELL_WIDTH - 8, line_y + 36), radius=5, fill=ACTIVITY,
                )
                draw.text((x + 12, line_y + 1), f"{event.starts_at:%H:%M}",
                          font=time_font, fill=MUTED)
                draw.text((x + 12, line_y + 16),
                          fit_text(draw, event.title, small, CELL_WIDTH - 24),
                          font=small, fill=TEXT)

    legend_y = height - 45
    draw.rounded_rectangle((28, legend_y, 45, legend_y + 17), radius=4, outline=ACCENT, width=2)
    draw.text((54, legend_y - 2), "Aujourd’hui", font=body, fill=TEXT)
    draw.ellipse((208, legend_y + 5, 216, legend_y + 13), fill=ACCENT)
    draw.text((229, legend_y - 2), "Activité", font=body, fill=TEXT)
    draw.text((370, legend_y - 2), "Heure de Paris · Détails complets dans la liste Discord",
              font=small, fill=MUTED)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    image.close()
    return output.getvalue()


def gen_cal(data_events, bg, annee: int, mois: int, highlight_date: date | None = None):
    """Compatibility entry point; the decorative background is deliberately ignored."""
    snapshot = snapshot_events(data_events)
    return io.BytesIO(render_month(snapshot.events, annee, mois, highlight_date))


class MonthlyRenderer:
    """Bound CPU concurrency and memory; cached bytes never share open file handles."""

    def __init__(self, max_entries: int = 24, max_bytes: int = 8 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._size = 0
        self._semaphore = asyncio.Semaphore(2)

    async def render(
        self, events: tuple[CalendarEvent, ...], year: int, month: int, today: date,
    ) -> bytes:
        visible = tuple(event for event in events
                        if (event.day.year, event.day.month) == (year, month))
        signature = hashlib.sha256()
        for event in visible:
            fields = (event.id, one_line(event.title, 1000), event.starts_at.isoformat())
            signature.update(repr(fields).encode("utf-8"))
        key = (year, month, today, signature.digest())
        async with self._semaphore:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                log.debug("Calendar: monthly render cache hit year=%s month=%s", year, month)
                return cached
            result = await asyncio.to_thread(render_month, visible, year, month, today)
            if self.max_entries > 0 and len(result) <= self.max_bytes:
                previous = self._cache.pop(key, None)
                if previous is not None:
                    self._size -= len(previous)
                self._cache[key] = result
                self._size += len(result)
                while len(self._cache) > self.max_entries or self._size > self.max_bytes:
                    _, evicted = self._cache.popitem(last=False)
                    self._size -= len(evicted)
            log.debug("Calendar: rendered year=%s month=%s bytes=%s", year, month, len(result))
            return result
