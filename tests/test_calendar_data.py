import calendar
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from utils.calendar_data import (
    CalendarState, MAX_DATE, MIN_DATE, PAGE_SIZE, make_page, one_line,
    paris_time, parse_anchor, plain_text, shorten, snapshot_events,
)
from utils.datetime_utils import PARIS


def record(start="2026-09-11 23:00:00", **overrides):
    data = {
        "id": "1", "titre": "Donjon Blop", "date_str": start, "description": "Sortie de guilde",
        "creator_id": 7, "role_id": 42, "participants": [7], "cancelled": False,
    }
    return {**data, **overrides}


@pytest.mark.parametrize("year", [2024, 2026, 2100])
@pytest.mark.parametrize("month", range(1, 13))
def test_month_boundaries_cover_every_day(year, month):
    state = CalendarState(date(year, month, 17), "mois")
    start, end = state.bounds
    assert start == date(year, month, 1)
    assert (end - start).days == calendar.monthrange(year, month)[1]
    assert state.contains(start)
    assert not state.contains(end)


@pytest.mark.parametrize("anchor,start,end", [
    (date(2026, 9, 11), date(2026, 9, 7), date(2026, 9, 14)),
    (date(2026, 1, 1), date(2025, 12, 29), date(2026, 1, 5)),
    (date(2024, 2, 29), date(2024, 2, 26), date(2024, 3, 4)),
])
def test_monday_first_weeks_cross_months_and_years(anchor, start, end):
    state = CalendarState(anchor)
    assert state.bounds == (start, end)
    assert state.title


def test_month_navigation_clamps_day_and_preserves_filter():
    state = CalendarState(date(2024, 1, 31), "mois", "inscrit", 2).step(1)
    assert state == CalendarState(date(2024, 2, 29), "mois", "inscrit", 0)
    assert CalendarState(date(2026, 12, 15), "mois").step(1).anchor == date(2027, 1, 15)


@pytest.mark.parametrize("mode", ["semaine", "mois"])
def test_navigation_stays_in_supported_range(mode):
    assert CalendarState(MIN_DATE, mode).step(-1).anchor == MIN_DATE
    assert CalendarState(MAX_DATE, mode).step(1).anchor == MAX_DATE


@pytest.mark.parametrize("value", [
    "31/02/2026", "2026-09-11", "1/9/2026", "01/01/1969", "01/01/2101", "pas une date",
])
def test_invalid_dates_have_actionable_errors(value):
    with pytest.raises(ValueError):
        parse_anchor(value, date(2026, 9, 11))


def test_parse_date_and_default():
    today = date(2026, 9, 11)
    assert parse_anchor("", today) == today
    assert parse_anchor(" 29/02/2024 ", today) == date(2024, 2, 29)


def test_snapshot_is_detached_sorted_and_tolerates_bad_records():
    records = {
        "3": record("2026-09-12 01:00:00"),
        "2": record(participants=[7, "7", 8]),
        "gone": record(cancelled=True),
        "bad": record(date_str="not a date"),
        "missing": None,
    }
    snapshot = snapshot_events(records)
    assert [e.id for e in snapshot.events] == ["2", "3"]
    assert snapshot.skipped == 2
    assert snapshot.get("2").participants == (7, 8)
    records["2"]["participants"].append(9)
    assert snapshot.get("2").participants == (7, 8)
    assert records["2"]["date_str"] == "2026-09-11 23:00:00"


@pytest.mark.parametrize("start,utc_hour", [
    ("2026-01-11 23:00:00", 22),
    ("2026-09-11 23:00:00", 21),
    ("2026-03-29 03:15:00", 1),
    ("2026-10-25 03:15:00", 2),
])
def test_naive_dates_mean_paris_not_server_timezone(start, utc_hour):
    event = snapshot_events({"1": record(start)}).events[0]
    assert event.starts_at.astimezone(timezone.utc).hour == utc_hour


def test_aware_utc_dates_are_grouped_in_the_correct_paris_day():
    event = snapshot_events({"1": record("2026-09-30T22:30:00+00:00")}).events[0]
    assert event.day == date(2026, 10, 1)
    assert event.starts_at.hour == 0


def test_filters_statuses_and_capacity_are_consistent():
    now = datetime(2026, 9, 11, 21, tzinfo=PARIS)
    records = {
        "past": record("2026-09-11 20:00:00"),
        "mine": record(),
        "free": record(participants=[9]),
        "full": record(participants=list(range(10, 18))),
    }
    snapshot = snapshot_events(records)
    state = CalendarState(now.date())
    assert len(make_page(snapshot, state, 7, now).events) == 4
    assert {e.id for e in make_page(snapshot, replace(state, filter="inscrit"), 7, now).events} == {
        "past", "mine",
    }
    assert {e.id for e in make_page(snapshot, replace(state, filter="disponibles"), 7, now).events} == {
        "free", "mine",
    }
    assert snapshot.get("mine").status(7, now) == "Inscrit"
    assert snapshot.get("full").status(7, now) == "Complet"
    assert snapshot.get("past").status(7, now) == "Début passé"


def test_pagination_reaches_all_events_and_clamps_after_deletion():
    now = datetime(2026, 9, 11, 21, tzinfo=PARIS)
    snapshot = snapshot_events({str(i): record() for i in range(80)})
    state = CalendarState(now.date())
    page = make_page(snapshot, state, 7, now)
    found = []
    for index in range(page.pages):
        current = make_page(snapshot, replace(state, page=index), 7, now)
        assert len(current.events) <= PAGE_SIZE
        found.extend(event.id for event in current.events)
    assert len(set(found)) == 80
    empty = make_page(snapshot_events({}), replace(state, page=13), 7, now)
    assert empty.state.page == 0
    assert empty.pages == 1


def test_controls_mentions_and_long_emoji_text_are_safe():
    assert plain_text("T\u202eitre\x00 <@123> <a:blop:42>") == "Titre membre 123 blop"
    assert one_line("A\nB\tC") == "A B C"
    value = shorten("😀" * 200, 100)
    assert len(value.encode("utf-16-le")) // 2 <= 100
    assert value.endswith("…")


def test_unknown_modes_and_filters_are_rejected():
    with pytest.raises(ValueError):
        CalendarState(date(2026, 1, 1), "jour")
    with pytest.raises(ValueError):
        CalendarState(date(2026, 1, 1), filter="unknown")


def test_has_started_compares_elapsed_time_during_autumn_fold():
    before = datetime(2026, 10, 25, 2, 45, tzinfo=PARIS, fold=0)
    after = datetime(2026, 10, 25, 2, 15, tzinfo=PARIS, fold=1)
    event = snapshot_events({"1": record(after.isoformat())}).events[0]
    assert event.has_started(before) is False
