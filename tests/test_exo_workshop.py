"""Regressions du moteur et du parcours FM, sans SDK Discord ni acces reseau."""

import copy
import json
import random

import pytest

from utils.exo_data import demo_item, parse_effects
from utils.exo_engine import (
    D, LEGACY_PROFILE, PROFILE, STATS, Item, Rates, Rune, State, attempt,
    decimal_value, observe, rates_for, recommended_rune,
)
from utils.exo_feedback import batch_text, result_lines
from utils.exo_session import EXPORT_LIMIT, Session, export_session, import_session
from utils.exo_workshop import reset_simulation, set_goals, simulate_batch


def workshop():
    item = Item("Anneau de test", "test", {"fo": (10, 50), "pa": (1, 1)}, "fixture")
    session = Session.create(item)
    session.seed = 1
    session.rune = Rune("fo")
    session.sim = State({"fo": 10, "pa": 1})
    return session


@pytest.mark.parametrize("key", list(STATS))
def test_all_supported_runes_have_automatic_rates(key):
    session = workshop()
    for tier in range(len(STATS[key].gains)):
        rates = rates_for(session.item, session.sim, Rune(key, tier), None)
        assert 0 <= rates.sc <= 1
        assert 0 <= rates.sn <= 1
        assert 0 <= rates.ec <= 1
        assert rates.sc + rates.sn <= 1


def test_automatic_rates_react_to_jet_and_rune_size():
    item = Item("Force", "test", {"fo": (1, 100)}, "fixture")
    empty = rates_for(item, State({"fo": 0}), Rune("fo"), None)
    full = rates_for(item, State({"fo": 99}), Rune("fo"), None)
    larger = rates_for(item, State({"fo": 80}), Rune("fo", 2), None)
    smaller = rates_for(item, State({"fo": 80}), Rune("fo"), None)
    assert empty.sc + empty.sn > full.sc + full.sn
    assert larger.sc + larger.sn > smaller.sc + smaller.sn
    assert "non calibrée" in empty.source


@pytest.mark.parametrize("outcome,rates,final,gain", [
    ("EC", Rates(0, 0), 9, 0),
    ("SN", Rates(0, 1), 10, 1),
    ("SC", Rates(1, 0), 11, 1),
])
def test_worked_stat_can_fall_and_gross_gain_is_distinct(outcome, rates, final, gain):
    item = Item("Force", "test", {"fo": (1, 20)}, "fixture")
    state = State({"fo": 10})
    row = attempt(item, state, Rune("fo"), rates, 1)
    assert row["outcome"] == outcome
    assert state.jets["fo"] == final
    assert row["applied_gain"] == gain
    assert row["changes"]["fo"] == [10, final]
    assert row["losses"] == ({} if outcome == "SC" else {"fo": 1})


@pytest.mark.parametrize("rates", [Rates(1, 0), Rates(0, 1), Rates(0, 0)])
def test_weight_and_stat_accounting_over_random_scenarios(rates):
    item = Item("Fixture", "fixture", {
        "fo": (1, 100), "vi": (1, 400), "ini": (1, 1000), "pa": (1, 1),
    }, "fixture")
    rng = random.Random(42)
    for seed in range(500):
        state = State({
            "fo": rng.randrange(100), "vi": rng.randrange(400),
            "ini": rng.randrange(1000), "pa": rng.randrange(2),
        }, D(rng.randrange(1000)) / 100)
        rune = Rune(rng.choice(["fo", "vi", "ini"]), rng.randrange(3))
        state.jets[rune.stat] = min(state.jets[rune.stat], item.maximum(rune.stat) - rune.gain)
        before = copy.deepcopy(state)
        row = attempt(item, state, rune, rates, seed, 17)
        gained = row["gain"] if row["outcome"] != "EC" else 0
        for key in set(before.jets) | set(state.jets):
            assert state.jets.get(key, 0) == (
                before.jets.get(key, 0) + (gained if key == rune.stat else 0)
                - row["losses"].get(key, 0)
            )
            assert state.jets.get(key, 0) >= 0
        lost = sum((STATS[key].weight * value for key, value in row["losses"].items()), D(0))
        if row["outcome"] == "SC":
            assert state.sink == before.sink
            assert not row["losses"]
        else:
            assert state.sink - before.sink == lost - rune.weight + D(row["unexplained_weight"])
        assert state.spent == 17
        assert state.attempts == 1
        assert state.sequence == 1


def test_order_of_json_keys_does_not_change_next_draw():
    session = workshop()
    session.sim = State({"fo": 30, "pa": 1, "ini": 5})
    other = copy.deepcopy(session.sim)
    other.jets = dict(reversed(list(other.jets.items())))
    a = attempt(session.item, session.sim, Rune("fo"), Rates(0, 0), 19)
    b = attempt(session.item, other, Rune("fo"), Rates(0, 0), 19)
    assert a == b


def test_sink_overflow_does_not_commit_partial_state():
    item = Item("Force", "test", {"fo": (1, 20)}, "fixture")
    state = State({"fo": 10, "pa": 1}, D(100000))
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        attempt(item, state, Rune("fo"), Rates(0, 0), 1)
    assert state == before


def test_inconsistent_observed_payment_invalidates_known_sink():
    state = State({"pa": 1})
    row = observe(demo_item(), state, Rune("pm"), "EC", {})
    assert row["unexplained_weight"] == "90"
    assert state.sink is None
    assert "puits désormais inconnu" in "\n".join(result_lines(row))
    observe(demo_item(), state, Rune("pm"), "EC", {"pa": 1})
    assert state.sink is None


def test_malus_can_be_recorded_manually_without_guessing_weight():
    item = Item("Malus", "malus", {"fo": (-20, -1), "pa": (1, 1)}, "fixture")
    state = State({"fo": -3, "pa": 1}, D(50))
    row = observe(item, state, Rune("pm"), "EC", {"fo": 2})
    assert state.jets["fo"] == -5
    assert state.sink is None
    assert row["changes"]["fo"] == [-3, -5]


def test_unknown_sink_is_never_displayed_as_none_or_zero():
    state = State({"pa": 1}, None)
    row = observe(demo_item(), state, Rune("pm"), "EC", {"pa": 1})
    text = "\n".join(result_lines(row))
    assert "inconnu → inconnu" in text
    assert "None" not in text


def test_gelano_pm_without_pa_is_not_finished_and_pa_can_be_restored():
    session = Session.create(demo_item())
    session.sim = State({"pa": 0, "pm": 1}, D(10))
    assert session.goal_met
    assert not session.reached
    session.rune = Rune("pa")
    session.custom["pa:0"] = Rates(1, 0)
    session.prices["pa:0"] = 150000
    result = simulate_batch(session, 100)
    assert len(result.rows) == 1
    assert session.reached
    assert session.sim.jets == {"pa": 1, "pm": 1}
    assert session.sim.sink == 10
    assert session.sim.spent == 150000
    assert "Tous les objectifs" in result.stop_reason


def test_native_heavy_objective_is_rejected_instead_of_creating_an_impossible_session():
    with pytest.raises(ValueError, match="Objectif PA=2.*plafond"):
        Session.create(demo_item(), "pa")


def test_batch_stops_on_selected_natural_line_without_free_rebuild():
    session = workshop()
    session.sim.jets["fo"] = 47
    session.rune = Rune("fo", 1)
    session.custom["fo:1"] = Rates(1, 0)
    session.prices["fo:1"] = 123
    result = simulate_batch(session, 100)
    assert len(result.rows) == 1
    assert session.sim.jets["fo"] == 50
    assert session.sim.spent == 123
    assert session.sim.jets.get("pm", 0) == 0
    assert not session.reached


def test_batch_wont_overshoot_but_single_rune_allows_deliberate_over():
    session = workshop()
    session.sim.jets["fo"] = 49
    session.rune = Rune("fo", 1)
    session.custom["fo:1"] = Rates(1, 0)
    before = copy.deepcopy(session.sim)
    with pytest.raises(ValueError, match="plus petite"):
        simulate_batch(session, 10)
    assert session.sim == before
    simulate_batch(session, 1)
    assert session.sim.jets["fo"] == 52


def test_batch_reports_why_it_stopped_after_a_valid_attempt():
    session = workshop()
    session.sim.sequence = 10**9 - 1
    session.custom["fo:0"] = Rates(1, 0)
    result = simulate_batch(session, 10)
    assert len(result.rows) == 1
    assert "Limite de session" in result.stop_reason


def test_batch_feedback_reports_all_losses_and_cost_not_only_last_rune():
    session = workshop()
    session.item = Item("Force", "test", {"fo": (1, 20)}, "fixture")
    # Changer la fiche exige des objectifs compatibles : PA+PM seraient deux exos.
    set_goals(session, "pm=1;fo=10")
    session.sim = State({"fo": 10})
    session.custom["fo:0"] = Rates(0, 0)
    session.prices["fo:0"] = 25
    result = simulate_batch(session, 10)
    text = batch_text(result)
    assert result.losses == {"fo": 10}
    assert result.changes == {"fo": [10, 0]}
    assert "−10 Force" in text
    assert "10 EC" in text
    assert "250 kamas" in text
    assert session.sim.attempts == 10


@pytest.mark.parametrize("count", [0, -1, 101, True, "10"])
def test_invalid_batch_size_is_atomic(count):
    session = workshop()
    before = copy.deepcopy(session)
    with pytest.raises(ValueError):
        simulate_batch(session, count)
    assert session == before


def test_observation_mode_cannot_run_batches():
    session = workshop()
    session.mode = "observation"
    before = copy.deepcopy(session)
    with pytest.raises(ValueError, match="suivi"):
        simulate_batch(session, 10)
    assert session == before


def test_multi_line_goals_do_not_reset_existing_progress():
    session = workshop()
    session.sim.attempts = 50
    session.sim.spent = 999
    before = copy.deepcopy(session.sim)
    set_goals(session, "pm=1 ; pa=1 ; force=45")
    assert session.requirements == {"pm": 1, "pa": 1, "fo": 45}
    assert session.sim == before


@pytest.mark.parametrize("text", ["", "pa=2", "fo=102", "pm=1; po=1", "pm=0", "fo=1;force=2"])
def test_invalid_goals_are_rejected_atomically(text):
    session = workshop()
    before = copy.deepcopy(session)
    with pytest.raises(ValueError):
        set_goals(session, text)
    assert session == before


@pytest.mark.parametrize("kind", ["minimum", "random", "perfect"])
def test_new_jet_is_reproducible_and_preserves_other_mode_and_settings(kind):
    session = workshop()
    session.prices["fo:0"] = 12
    session.observed = State({"fo": 23, "pa": 1}, None)
    session.custom["fo:0"] = Rates(.2, .5)
    before = copy.deepcopy(session)
    other = copy.deepcopy(session)
    reset_simulation(session, kind, 123)
    reset_simulation(other, kind, 123)
    assert session == other
    assert session.observed == before.observed
    assert session.requirements == before.requirements
    assert session.prices == before.prices
    assert session.custom == before.custom
    assert session.sim.sink == 0
    assert session.sim.attempts == 0
    for key, (low, high) in session.item.bounds.items():
        assert low <= session.sim.jets[key] <= high


@pytest.mark.parametrize("current,target,expected", [(5, 50, 0), (25, 50, 1), (49, 50, 0), (70, 100, 2)])
def test_recommended_tier_avoids_crossing_remaining_target(current, target, expected):
    item = Item("Force", "test", {"fo": (1, 100)}, "fixture")
    assert recommended_rune(item, State({"fo": current}), "fo", target).tier == expected


@pytest.mark.parametrize("value", [True, "NaN", "Infinity", "-1", "0.001", "1" * 100, "1E999999999"])
def test_decimal_inputs_are_bounded(value):
    with pytest.raises(ValueError):
        decimal_value(value)


def test_zero_with_huge_exponent_cannot_poison_arithmetic():
    assert decimal_value("0E+999999") == 0


def test_legacy_snapshot_is_preserved_without_reinterpreting_its_sink():
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures/fm_retro/legacy-v2.json"
    migrated = import_session(fixture.read_bytes())
    assert migrated.sim.jets == {"fo": 30}
    assert migrated.sim.sink == D(997)
    assert len(migrated.sim.journal) == 3
    assert "lecture seule" in migrated.notice
    assert json.loads(export_session(migrated))["schema"] == 3
    with pytest.raises(ValueError, match="Ancien modèle"):
        simulate_batch(migrated, 1)


def test_v3_round_trip_preserves_goals_settings_and_next_event():
    session = workshop()
    session.custom["fo:0"] = Rates(.3, .4)
    session.prices["fo:0"] = 23
    set_goals(session, "pm=1;pa=1;fo=45")
    simulate_batch(session, 10)
    other = import_session(export_session(session))
    assert session.requirements == other.requirements
    assert session.sim == other.sim
    a = simulate_batch(session, 1)
    b = simulate_batch(other, 1)
    assert a.rows == b.rows


@pytest.mark.parametrize("mutation", [
    lambda data: data["quality"].update(mystere=1),
    lambda data: data["quality"].update(fo=True),
    lambda data: data["simulation"]["journal"][0]["changes"].update(fo=[10, 999]),
    lambda data: data["simulation"]["journal"][0].update(applied_gain=0),
    lambda data: data["simulation"]["journal"][0].update(n=500),
    lambda data: data["simulation"].update(spent=0),
    lambda data: data["simulation"]["journal"][0]["rates"].update(sc=2),
    lambda data: data["simulation"].update(attempts=0),
    lambda data: data.update(profile="unknown"),
])
def test_import_rejects_inconsistent_event_or_goals(mutation):
    session = workshop()
    session.custom["fo:0"] = Rates(1, 0)
    session.prices["fo:0"] = 20
    simulate_batch(session, 1)
    data = json.loads(export_session(session))
    mutation(data)
    with pytest.raises(ValueError):
        import_session(json.dumps(data).encode())


def test_import_rejects_duplicate_json_keys():
    raw = export_session(workshop()).replace(b'"schema": 3', b'"schema": 3, "schema": 3', 1)
    with pytest.raises(ValueError, match="répété"):
        import_session(raw)


def test_large_two_mode_history_still_exports_without_silent_trimming():
    session = workshop()
    session.sim = State({"fo": 10, "pa": 1}, D(1000))
    for _ in range(100):
        attempt(session.item, session.sim, Rune("fo"), Rates(0, 0), 5, 15)
        observe(session.item, session.observed, Rune("fo"), "EC", {}, 8)
    raw = export_session(session)
    assert len(raw) <= EXPORT_LIMIT
    other = import_session(raw)
    assert len(other.sim.journal) == len(other.observed.journal) == 100


@pytest.mark.parametrize("raw,key,bounds", [
    ("+1 000–1 500 en initiative", "ini", (1000, 1500)),
    ("+5 % de résistances à l'air", "rp_ai", (5, 5)),
    ("+2 résistance à la terre", "r_te", (2, 2)),
    ("−10 à −5 en force", "fo", (-10, -5)),
])
def test_catalogue_typographic_variants(raw, key, bounds):
    stats, unknown, _ = parse_effects([raw])
    assert stats == {key: bounds}
    assert not unknown


def test_unknown_damage_effect_is_not_silently_marked_immutable():
    stats, unknown, immutable = parse_effects([
        "Dommages renvoyés : 5", "Dommages : 5 à 8 (neutre)", "+5 dommages",
    ])
    assert stats == {"do": (5, 5)}
    assert unknown == ("Dommages renvoyés : 5",)
    assert immutable == ("Dommages : 5 à 8 (neutre)",)


def test_default_objective_on_native_pm_boots_is_an_exo_pa():
    item = Item("Bottes", "boots", {"pm": (1, 1), "fo": (10, 30)}, "fixture")
    session = Session.create(item)
    assert session.goal_stat == "pa"
    assert session.goal_value == 1
    assert session.requirements["pm"] == 1
    assert session.rune == Rune("pa")


@pytest.mark.parametrize("objective", ["pm", "po", "fo"])
def test_explicit_admissible_objective_is_never_silently_changed(objective):
    session = Session.create(demo_item(), objective)
    assert session.goal_stat == objective
    assert session.goal_value == 1


def test_import_rejects_ambiguous_duplicate_primary_goal():
    session = Session.create(demo_item())
    payload = json.loads(export_session(session))
    payload["quality"][session.goal_stat] = session.goal_value + 1
    with pytest.raises(ValueError, match="répété"):
        import_session(json.dumps(payload).encode())
