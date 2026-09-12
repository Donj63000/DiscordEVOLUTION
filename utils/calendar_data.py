"""Immutable snapshots and date arithmetic for the activity calendar."""

from __future__ import annotations

import calendar
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from utils.datetime_utils import PARIS

log = logging.getLogger(__name__)
MONTH_NAMES_FR = (
    "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)
DAY_NAMES_FR = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
MIN_DATE = date(1970, 1, 1)
MAX_DATE = date(2100, 12, 31)
PAGE_SIZE = 6
GROUP_CAPACITY = 8
MODES = ("semaine", "mois")
FILTERS = ("toutes", "inscrit", "disponibles")


def paris_time(value: datetime) -> datetime:
    """Interpret historical naive activity dates as Paris wall time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=PARIS)
    return value.astimezone(PARIS)


def plain_text(value: Any) -> str:
    """Remove control characters, bidi overrides and Discord mention syntax."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = re.sub(r"<a?:([A-Za-z0-9_]+):[0-9]+>", r"\1", text)
    text = re.sub(r"<@!?([0-9]+)>", r"membre \1", text)
    text = re.sub(r"<@&([0-9]+)>", r"rôle \1", text)
    text = re.sub(r"<#([0-9]+)>", r"salon \1", text)
    return "".join(
        char for char in text
        if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    ).strip()


def shorten(text: str, limit: int) -> str:
    """Bound UTF-16 units too, so emoji-heavy text stays within Discord limits."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= limit * 2:
        return text
    prefix = encoded[:max(0, limit - 1) * 2].decode("utf-16-le", errors="ignore")
    return prefix.rstrip() + "…"


def one_line(value: Any, limit: int = 200) -> str:
    return shorten(" ".join(plain_text(value).split()), limit)


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    starts_at: datetime
    description: str
    creator_id: int
    participants: tuple[int, ...]

    @property
    def timestamp(self) -> int:
        return int(self.starts_at.timestamp())

    @property
    def day(self) -> date:
        return self.starts_at.date()

    @property
    def places(self) -> int:
        return max(0, GROUP_CAPACITY - len(self.participants))

    def has_started(self, now: datetime) -> bool:
        return self.starts_at.astimezone(timezone.utc) <= paris_time(now).astimezone(timezone.utc)

    def status(self, user_id: int | None, now: datetime) -> str:
        if self.has_started(now):
            return "Début passé"
        if user_id is not None and user_id in self.participants:
            return "Inscrit"
        if not self.places:
            return "Complet"
        return f"{self.places} place{'s' if self.places > 1 else ''} libre{'s' if self.places > 1 else ''}"


@dataclass(frozen=True)
class CalendarSnapshot:
    events: tuple[CalendarEvent, ...]
    skipped: int = 0

    def get(self, event_id: str) -> CalendarEvent | None:
        return next((event for event in self.events if event.id == event_id), None)


def snapshot_events(records: Mapping[str, Any]) -> CalendarSnapshot:
    """Read without modifying storage; isolate malformed records instead of losing the view."""
    if not isinstance(records, Mapping):
        log.warning("Calendar: invalid events container")
        return CalendarSnapshot((), 1)
    events = []
    skipped = 0
    for key, record in list(records.items()):
        try:
            if isinstance(record, Mapping):
                if record.get("cancelled", False):
                    continue
                starts_at = datetime.fromisoformat(record["date_str"])
                title = record.get("titre", "")
                description = record.get("description", "")
                creator_id = int(record["creator_id"])
                participants = record.get("participants", ())
            else:
                if record.cancelled:
                    continue
                starts_at = record.date_obj
                title = record.titre
                description = record.description
                creator_id = int(record.creator_id)
                participants = record.participants
            event_id = str(key)
            if (not event_id or one_line(event_id, 100) != event_id
                    or not isinstance(starts_at, datetime)):
                raise ValueError("invalid event identifier or datetime")
            if not isinstance(participants, (list, tuple)):
                raise ValueError("invalid participants")
            participant_ids = tuple(dict.fromkeys(int(value) for value in participants))
            events.append(CalendarEvent(
                event_id, plain_text(title) or "Sans titre", paris_time(starts_at),
                plain_text(description), creator_id, participant_ids,
            ))
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            skipped += 1
            log.debug("Calendar: ignored malformed record id=%r", key, exc_info=True)
    events.sort(key=lambda event: (event.timestamp, event.id))
    return CalendarSnapshot(tuple(events), skipped)


def parse_anchor(value: str, today: date) -> date:
    if not value.strip():
        return today
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value.strip()):
        raise ValueError("Utilise le format JJ/MM/AAAA, par exemple 11/09/2026.")
    try:
        result = datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValueError("Cette date n’existe pas. Utilise le format JJ/MM/AAAA.") from exc
    if not MIN_DATE <= result <= MAX_DATE:
        raise ValueError("Choisis une date entre 1970 et 2100.")
    return result


@dataclass(frozen=True)
class CalendarState:
    anchor: date
    mode: str = "semaine"
    filter: str = "toutes"
    page: int = 0

    def __post_init__(self) -> None:
        if self.mode not in MODES or self.filter not in FILTERS:
            raise ValueError("Vue ou filtre de calendrier inconnu.")
        if not MIN_DATE <= self.anchor <= MAX_DATE:
            raise ValueError("Choisis une date entre 1970 et 2100.")

    @property
    def bounds(self) -> tuple[date, date]:
        if self.mode == "semaine":
            start = self.anchor - timedelta(days=self.anchor.weekday())
            return start, start + timedelta(days=7)
        start = self.anchor.replace(day=1)
        return start, start + timedelta(days=calendar.monthrange(start.year, start.month)[1])

    @property
    def title(self) -> str:
        start, end = self.bounds
        if self.mode == "mois":
            return f"{MONTH_NAMES_FR[start.month]} {start.year}"
        last = end - timedelta(days=1)
        if start.month == last.month:
            return f"{start.day}–{last.day} {MONTH_NAMES_FR[last.month].lower()} {last.year}"
        if start.year == last.year:
            return (
                f"{start.day} {MONTH_NAMES_FR[start.month].lower()} – "
                f"{last.day} {MONTH_NAMES_FR[last.month].lower()} {last.year}"
            )
        return f"{start:%d/%m/%Y} – {last:%d/%m/%Y}"

    def contains(self, value: date) -> bool:
        start, end = self.bounds
        return start <= value < end

    def step(self, delta: int) -> CalendarState:
        if self.mode == "semaine":
            anchor = self.anchor + timedelta(days=7 * delta)
        else:
            index = self.anchor.year * 12 + self.anchor.month - 1 + delta
            year, month = divmod(index, 12)
            day = min(self.anchor.day, calendar.monthrange(year, month + 1)[1])
            anchor = date(year, month + 1, day)
        return replace(self, anchor=min(MAX_DATE, max(MIN_DATE, anchor)), page=0)


def matches_filter(event: CalendarEvent, name: str, user_id: int, now: datetime) -> bool:
    if name == "inscrit":
        return user_id in event.participants
    if name == "disponibles":
        return bool(event.places) and not event.has_started(now)
    return True


@dataclass(frozen=True)
class CalendarPage:
    state: CalendarState
    events: tuple[CalendarEvent, ...]
    period_events: tuple[CalendarEvent, ...]
    unfiltered_count: int
    pages: int
    next_event: CalendarEvent | None


def make_page(
    snapshot: CalendarSnapshot, state: CalendarState, user_id: int, now: datetime,
) -> CalendarPage:
    candidates = tuple(
        event for event in snapshot.events if matches_filter(event, state.filter, user_id, now)
    )
    period = tuple(event for event in candidates if state.contains(event.day))
    pages = max(1, (len(period) + PAGE_SIZE - 1) // PAGE_SIZE)
    state = replace(state, page=min(max(0, state.page), pages - 1))
    start = state.page * PAGE_SIZE
    next_event = next((event for event in candidates if not event.has_started(now)
                       and MIN_DATE <= event.day <= MAX_DATE), None)
    return CalendarPage(
        state, period[start:start + PAGE_SIZE], period,
        sum(state.contains(event.day) for event in snapshot.events), pages, next_event,
    )
