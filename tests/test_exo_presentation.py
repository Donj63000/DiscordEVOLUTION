"""Validation des vrais payloads d'affichage, sans doublure du SDK Discord."""

import copy

import pytest

from utils.exo_data import demo_item
from utils.exo_engine import D, STATS, Item, Rates, Rune, State, attempt, observe
from utils.exo_feedback import batch_text
from utils.exo_presentation import build_payload, guidance, utf16_length
from utils.exo_session import Session
from utils.exo_workshop import simulate_batch


def text(payload):
    return "\n".join([payload["title"], payload["description"], payload["footer"]["text"]] + [
        part for entry in payload["fields"] for part in (entry["name"], entry["value"])
    ])


def validate(payload):
    assert len(payload["fields"]) <= 25
    assert utf16_length(payload["title"]) <= 256
    assert utf16_length(payload["description"]) <= 4096
    total = sum(utf16_length(payload[key]) for key in ("title", "description"))
    total += utf16_length(payload["footer"]["text"])
    for entry in payload["fields"]:
        assert utf16_length(entry["name"]) <= 256
        assert 1 <= utf16_length(entry["value"]) <= 1024
        total += utf16_length(entry["name"]) + utf16_length(entry["value"])
    assert total <= 6000


@pytest.mark.parametrize("tab", ["atelier", "maths", "settings", "journal", "aide"])
@pytest.mark.parametrize("dense", [False, True])
def test_panels_respect_discord_utf16_limits(tab, dense):
    item = demo_item() if not dense else Item(
        "😀" * 100, "dense", {key: (100, 9999) for key in STATS}, "source " * 60,
        tuple(["🤖" * 150] * 100), tuple(["✨" * 150] * 100),
    )
    session = Session.create(item)
    session.notice = "😀" * 3000
    session.tab = tab
    before = copy.deepcopy(session)
    payload = build_payload(session)
    validate(payload)
    assert session == before


def test_every_stat_is_visible_even_on_large_item():
    session = Session.create(Item("Test", "test", {key: (0, 99) for key in STATS}, "fixture"))
    contents = text(build_payload(session))
    for stat in STATS.values():
        assert stat.name in contents
    assert "runes utilisées" in contents
    assert "runes passées" not in contents


def test_incomplete_gelano_has_actionable_guidance():
    session = Session.create(demo_item())
    session.sim = State({"pa": 0, "pm": 1}, D(10))
    assert session.rune.stat == "pm"
    assert "objet inachevé" in guidance(session)
    assert "PA ≥ 1" in guidance(session)


def test_panel_does_not_claim_automatic_rates_are_official():
    session = Session.create(demo_item())
    session.sim = State({"pa": 0})
    session.rune = Rune("pa")
    contents = text(build_payload(session))
    assert "non calibrée" in contents
    assert "non certifiés Ankama" in contents


def test_after_batch_each_delta_matches_whole_lot():
    item = Item("Force", "test", {"fo": (1, 20)}, "fixture")
    session = Session.create(item)
    session.sim = State({"fo": 10})
    session.rune = Rune("fo")
    session.custom["fo:0"] = Rates(0, 0)
    result = simulate_batch(session, 10)
    session.notice = batch_text(result)
    contents = text(build_payload(session))
    assert "Force : 0" in contents
    assert "(−10)" in contents
    assert "−10 Force" in contents


def test_all_one_hundred_history_entries_are_accessible():
    session = Session.create(demo_item())
    session.sim = State({"pa": 0}, D(10000))
    for _ in range(100):
        attempt(session.item, session.sim, Rune("fo"), Rates(0, 0), 1)
    session.tab = "journal"
    for page in range(100):
        session.journal_page = page
        payload = build_payload(session)
        validate(payload)
        assert f"Essai #{100 - page} ·" in text(payload)


def test_dense_observation_retains_all_losses_and_transitions():
    item = Item("Test", "test", {key: (1, 50) for key in STATS}, "fixture")
    session = Session.create(item)
    session.mode = "observation"
    session.observed = State({key: 50 for key in STATS}, None)
    observe(item, session.observed, Rune("fo"), "EC", {key: 1 for key in STATS})
    session.tab = "journal"
    payload = build_payload(session)
    validate(payload)
    contents = text(payload)
    for stat in STATS.values():
        assert f"−1 {stat.name}" in contents
        assert f"{stat.name} : **50 → 49**" in contents
    assert "None" not in contents


def test_imported_text_cannot_create_discord_mentions():
    item = Item("@everyone [click](https://example.invalid)", "test", {"pa": (1, 1)}, "@here")
    session = Session.create(item)
    contents = text(build_payload(session))
    assert "@everyone" not in contents
    assert "@here" not in contents
    assert "@\u200beveryone" in contents
    assert "\\[click\\]" in contents


def test_extremely_small_math_probability_still_fits():
    session = Session.create(demo_item())
    session.tab = "maths"
    session.p = 1e-320
    session.notice = "X" * 3000
    validate(build_payload(session))
