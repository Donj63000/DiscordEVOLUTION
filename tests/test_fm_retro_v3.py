"""Régressions v3. Tous les corpus créés ici sont SYNTHÉTIQUES, jamais des preuves en jeu."""
from __future__ import annotations

import copy
from decimal import Decimal as D, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import random

import pytest

from utils.exo_advice import rune_details, session_advice
from utils.exo_data import parse_effects
from utils.exo_engine import Item, Rune, Rates, State, STATS, attempt, observe, rates_for, simulation_blocker
from utils.exo_session import Session, export_session, import_session
from utils.exo_workshop import reset_simulation
from utils.fm_retro_audit import collect, evaluate, monte_carlo, reference_cases
from utils.fm_retro_limits import HistoryBudget
from utils.fm_retro_observations import default_corpus, load_corpus, MIN_CONTEXT_SAMPLES, DEFAULT_PATH
from utils.fm_retro_reference import PROFILE, RETRO, REFERENCE_PATH, REFERENCE_SHA256
from utils.fm_retro_statistics import wilson, total_variation


def synthetic_row(index=0, outcome="SN", split="train"):
    """Imite uniquement le SCHÉMA des captures pour tester le chargeur."""
    return {
        "id": f"{split}-{index}", "session": f"synthetic-{split}", "split": split,
        "origin": "game_observation",
        "source": {"url": "https://synthetic.invalid/not-a-game-capture", "server": "TEST-ONLY",
                   "game_version": "1.29.1", "date": "2026-09-17", "profession_level": 100},
        "item": {"name": "Synthétique", "token": "test-only", "bounds": {"fo": [0, 50]}},
        "before": {"jets": {"fo": 20}, "sink": "10"}, "rune": {"stat": "fo", "tier": 0},
        "outcome": outcome,
        "after": {"jets": {"fo": 20 if outcome == "EC" else 21},
                  "sink": "10" if outcome == "SC" else "9"},
    }


def corpus_document(rows):
    return {"schema": 1, "reference": PROFILE, "id": "synthetic-tests-only",
            "description": "DONNÉES DE TEST INVENTÉES, PAS DE CAPTURES DU JEU.", "observations": rows}


def corpus_file(tmp_path, rows):
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(corpus_document(rows)), encoding="utf-8")
    return path


def dataset(tmp_path, rows):
    return load_corpus(corpus_file(tmp_path, rows))


@pytest.mark.parametrize("key", STATS)
def test_costs_and_gains_follow_pinned_reference(key):
    for tier, gain in enumerate(STATS[key].gains):
        rune = Rune(key, tier)
        assert rune.nominal_weight == D(gain) * STATS[key].weight
        assert rune.weight == rune.nominal_weight.to_integral_value(rounding=ROUND_CEILING)
        details = rune_details(rune)
        assert D(details["poids_total"]) == rune.weight
        assert details["niveau_preuve"] == RETRO.metadata["stats"][key]["evidence"]


def test_reference_is_pinned_immutable_and_never_certified():
    assert hashlib.sha256(REFERENCE_PATH.read_bytes()).hexdigest() == REFERENCE_SHA256 == RETRO.digest
    with pytest.raises(TypeError):
        RETRO.stats["fo"] = None
    with pytest.raises(TypeError):
        RETRO.metadata["rules"]["critical"]["value"] = "invented"
    assert RETRO.summary()["certifie_ankama"] is False
    assert not load_corpus(DEFAULT_PATH).observations
    assert load_corpus(DEFAULT_PATH).summary()["fidelite_serveur_demontree"] is False


@pytest.mark.parametrize("tier,cost,nominal", [(0, "1", ".75"), (1, "3", "2.5"), (2, "8", "7.5")])
def test_retro_vitality_cost_and_nominal_power_are_distinct(tier, cost, nominal):
    assert Rune("vi", tier).weight == D(cost)
    assert Rune("vi", tier).nominal_weight == D(nominal)


def test_conditional_reference_cases():
    assert all(case["passed"] for case in reference_cases())


@pytest.mark.parametrize("raw,key,bounds", [
    ("+1 à 3 dommages aux pièges", "pi", (1, 3)),
    ("+10 à 20 % de dommages aux pièges", "pi_per", (10, 20)),
    ("Renvoie 1 à 5 dommages", "ren", (1, 5)),
])
def test_new_retro_lines_are_parsed(raw, key, bounds):
    known, unknown, immutable = parse_effects([raw])
    assert known == {key: bounds}
    assert not unknown and not immutable


def test_nonexistent_ra_prospe_is_rejected():
    with pytest.raises(ValueError):
        Rune("pp", 2)


def test_resistance_conflict_is_visible_instead_of_claiming_a_correction():
    assert STATS["r_fe"].weight == 2
    assert STATS["rp_fe"].weight == 6
    assert "non tranchée" in rune_details(Rune("r_fe"))["limites"]


def test_zero_observations_never_become_calibration():
    report = evaluate(load_corpus(DEFAULT_PATH), 100)
    assert report["status"] == "insufficient_data"
    assert report["coverage"] == 0 and report["brier"] is None
    assert not report["server_fidelity_demonstrated"]


def test_threshold_and_holdout_are_not_used_as_training(tmp_path):
    rows = [synthetic_row(i) for i in range(MIN_CONTEXT_SAMPLES - 1)]
    rows += [synthetic_row(i, "SC", "validation") for i in range(150)]
    corpus = dataset(tmp_path, rows)
    assert not corpus.groups
    assert corpus.summary()["poses_validation"] == 150


def test_joint_replay_preserves_correlation_and_declared_sink_residual(tmp_path):
    rows = [synthetic_row(i, "SC" if i < 30 else "SN" if i < 80 else "EC") for i in range(100)]
    # Contre-exemple volontaire au modèle de puits : ne pas réécrire la capture.
    rows[-1]["after"]["sink"] = "10"
    corpus = dataset(tmp_path, rows)
    first = corpus.observations[0]
    sample = corpus.match(first.item(), first.state_before(), first.rune())
    assert sample.counts == (30, 50, 20)
    rates = rates_for(first.item(), first.state_before(), first.rune(), None, corpus=corpus)
    assert (rates.sc, rates.sn) == (.3, .5)
    seen = set()
    for seed in range(1000):
        state = first.state_before()
        event = attempt(first.item(), state, first.rune(), None, seed, corpus=corpus)
        selected = next(row for row in corpus.observations if row.id == event["evidence"]["observation"])
        assert event["outcome"] == selected.outcome
        assert state.jets == dict(selected.after) and state.sink == selected.sink_after
        seen.add(event["ledger"]["residual"])
    assert seen == {"0", "1"}


def test_exact_context_required_and_custom_rates_do_not_use_corpus(tmp_path):
    corpus = dataset(tmp_path, [synthetic_row(i, "SC") for i in range(100)])
    row = corpus.observations[0]
    changed = row.state_before()
    changed.jets["fo"] = 19
    assert corpus.match(row.item(), changed, row.rune()) is None
    changed = row.state_before()
    changed.sink += 1
    assert corpus.match(row.item(), changed, row.rune()) is None
    state = row.state_before()
    event = attempt(row.item(), state, row.rune(), Rates(0, 0), 1, corpus=corpus)
    assert event["outcome"] == "EC" and event["evidence"]["kind"] == "custom_scenario"


def test_malus_requires_exact_joint_observations(tmp_path):
    rows = [synthetic_row(i, "SC") for i in range(100)]
    for row in rows:
        row["item"]["bounds"] = {"fo": [-20, -1]}
        row["before"]["jets"] = {"fo": -10}
        row["after"]["jets"] = {"fo": -9}
    corpus = dataset(tmp_path, rows)
    row = corpus.observations[0]
    assert simulation_blocker(row.item(), row.state_before(), row.rune())
    state = row.state_before()
    event = attempt(row.item(), state, row.rune(), None, 1, corpus=corpus)
    assert event["outcome"] == "SC" and state.jets["fo"] == -9
    # Dès que l'état sort du contexte connu, aucun taux de malus n'est inventé.
    assert simulation_blocker(row.item(), state, row.rune(), corpus=corpus)


def test_manual_malus_critical_success_preserves_known_sink():
    item = Item("Malus", "malus", {"fo": (-20, -1)}, "test")
    state = State({"fo": -10}, D(10))
    observe(item, state, Rune("fo"), "SC", {})
    assert state.sink == 10


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(origin="simulation"),
    lambda r: r.update(split="test"),
    lambda r: r.update(id=""),
    lambda r: r.update(outcome="critical"),
    lambda r: r["source"].update(url="http://capture.invalid"),
    lambda r: r["source"].update(url="https://user:pass@capture.invalid"),
    lambda r: r["source"].update(game_version="2.68"),
    lambda r: r["source"].update(profession_level=99),
    lambda r: r["source"].update(date="2026-02-30"),
    lambda r: r["before"].update(sink="NaN"),
    lambda r: r["before"].update(sink="-1"),
    lambda r: r["before"]["jets"].update(fo=True),
    lambda r: r["after"]["jets"].update(fo=99),
    lambda r: r["after"]["jets"].update(pa=2),
    lambda r: r["item"]["bounds"].update(fo=[0, 10001]),
    lambda r: r["rune"].update(tier=3),
    lambda r: r.update(extra_field=1),
])
def test_invalid_observations_fail_closed(tmp_path, mutate):
    row = synthetic_row()
    mutate(row)
    with pytest.raises(ValueError):
        dataset(tmp_path, [row])


def test_duplicate_ids_and_session_leakage_and_mixed_versions_rejected(tmp_path):
    row = synthetic_row()
    for other in (
        copy.deepcopy(row),
        {**synthetic_row(1, split="validation"), "session": row["session"]},
        {**synthetic_row(1), "source": {**row["source"], "game_version": "1.30"}},
        {**synthetic_row(1), "source": {**row["source"], "server": "OTHER"}},
    ):
        with pytest.raises(ValueError):
            dataset(tmp_path, [row, other])


def test_critical_observation_cannot_hide_losses_or_sink_consumption(tmp_path):
    row = synthetic_row(outcome="SC")
    row["after"]["sink"] = "9"
    with pytest.raises(ValueError):
        dataset(tmp_path, [row])
    row = synthetic_row(outcome="SC")
    row["after"]["jets"]["fo"] = 20
    with pytest.raises(ValueError):
        dataset(tmp_path, [row])


def test_unknown_sink_kept_for_audit_not_joint_learning(tmp_path):
    rows = [synthetic_row(i) for i in range(100)]
    for row in rows:
        row["before"]["sink"] = None
    corpus = dataset(tmp_path, rows)
    assert len(corpus.observations) == 100 and not corpus.groups


def test_holdout_score_uses_engine_with_injected_corpus(tmp_path):
    rows = [synthetic_row(i, "SC" if i < 40 else "EC") for i in range(100)]
    rows += [synthetic_row(i, "SC" if i < 40 else "EC", "validation") for i in range(100)]
    report = evaluate(dataset(tmp_path, rows), 5000)
    assert report["status"] == "measured_on_declared_holdout"
    assert report["scored_n"] == 100 and report["coverage"] == 1
    assert report["brier"] == pytest.approx(.48)
    assert report["contexts"][0]["joint_total_variation"] < .04
    assert not report["server_fidelity_demonstrated"]


def test_unpredicted_holdout_event_does_not_disappear(tmp_path):
    rows = [synthetic_row(i, "SC") for i in range(100)]
    rows += [synthetic_row(0, "EC", "validation")]
    report = evaluate(dataset(tmp_path, rows), 100)
    assert report["zero_probability_observations"] == 1
    assert report["log_loss"] is None and report["brier"] == 2
    assert report["contexts"][0]["joint_total_variation"] == 1


def recorded(n=3):
    item = Item("Test", "test", {"fo": (1, 50)}, "test")
    session = Session.create(item)
    session.rune = Rune("fo")
    session.sim = State({"fo": 20}, D(10000))
    for _ in range(n):
        attempt(item, session.sim, session.rune, Rates(0, 0), 129)
    return session


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(tier=1),
    lambda r: r.update(nominal_weight="3"),
    lambda r: r.update(reference="0" * 64),
    lambda r: r["after"].update(fo=25),
    lambda r: r["before"].update(fo=25),
    lambda r: r["ledger"].update(lost="1"),
    lambda r: r["ledger"].update(charged="0"),
    lambda r: r["ledger"].update(residual="NaN"),
    lambda r: r["evidence"].update(kind="server_verified"),
    lambda r: r["evidence"].update(corpus="wrong"),
    lambda r: r["evidence"].update(loss_model="uniform"),
])
def test_v3_tampered_event_is_rejected(mutate):
    payload = json.loads(export_session(recorded()))
    mutate(payload["simulation"]["journal"][-1])
    with pytest.raises(ValueError):
        import_session(json.dumps(payload).encode())


def test_history_complete_roundtrip_and_next_event():
    session = recorded(2000)
    imported = import_session(export_session(session))
    assert len(imported.sim.journal) == 2000
    assert imported.sim.journal[0]["n"] == 1
    assert imported.sim.journal_bytes == session.sim.journal_bytes
    assert attempt(imported.item, imported.sim, imported.rune, None, 2) == attempt(
        session.item, session.sim, session.rune, None, 2)


@pytest.mark.parametrize("bound", ["MAX_HISTORY", "MAX_HISTORY_BYTES"])
def test_capacity_refusal_is_atomic(monkeypatch, bound):
    import utils.exo_engine as engine
    session = recorded(3)
    before = copy.deepcopy(session.sim)
    monkeypatch.setattr(engine, bound, 3 if bound == "MAX_HISTORY" else session.sim.journal_bytes)
    with pytest.raises(ValueError, match="Historique plein"):
        attempt(session.item, session.sim, session.rune, Rates(1, 0), 1, 500)
    assert session.sim == before and session.sim.journal_bytes == before.journal_bytes


@pytest.mark.parametrize("schema", [1, 2])
def test_real_legacy_fixture_is_preserved_readonly_then_reset(schema):
    path = Path(__file__).parent / f"fixtures/fm_retro/legacy-v{schema}.json"
    session = import_session(path.read_bytes())
    assert session.sim.sink == 997
    assert "Ancien modèle" in simulation_blocker(session.item, session.sim, session.rune)
    assert import_session(export_session(session)).sim == session.sim
    reset_simulation(session, "minimum", 1)
    assert session.sim.profile == PROFILE and not session.sim.journal


def test_reference_or_corpus_mismatch_cannot_resume_silently():
    data = json.loads(export_session(recorded()))
    data["model_signature"] = data["model_signature"][:-1] + "0"
    with pytest.raises(ValueError, match="corpus différent"):
        import_session(json.dumps(data).encode())


def test_extract_only_manual_journal(tmp_path):
    session = recorded()
    source = synthetic_row()["source"]
    with pytest.raises(ValueError, match="suivi v3"):
        collect(session, session_id="s", split="train", source=source, corpus_id="test")
    session.observed = State({"fo": 20}, D(10))
    observe(session.item, session.observed, Rune("fo"), "SC", {})
    document = collect(session, session_id="s", split="train", source=source, corpus_id="test")
    path = tmp_path / "collected.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    corpus = load_corpus(path)
    assert len(corpus.observations) == 1
    assert corpus.observations[0].outcome == "SC"


def test_memory_budget_failure_and_release_are_atomic():
    budget = HistoryBudget(100)
    budget.reserve(1, 30)
    budget.reserve(2, 50)
    with pytest.raises(ValueError):
        budget.reserve(1, 51)
    assert budget.used == 80 and budget.allocations == {1: 30, 2: 50}
    budget.reserve(1, 10)
    assert budget.used == 60
    budget.release(1)
    budget.release(1)
    assert budget.used == 50


@pytest.mark.parametrize("successes,total", [(0, 100), (1, 100), (50, 100), (100, 100)])
def test_wilson_intervals_do_not_claim_certainty(successes, total):
    low, high = wilson(successes, total)
    assert 0 <= low <= successes / total <= high <= 1
    assert high > 0 and low < 1


@pytest.mark.parametrize("args", [(0, 0), (-1, 100), (101, 100), (True, 100), (1, 100, float("nan"))])
def test_invalid_intervals(args):
    with pytest.raises(ValueError):
        wilson(*args)


def test_total_variation_basics():
    assert total_variation({"SC": 1}, {"EC": 1}) == 1
    assert total_variation({"SC": .4, "EC": .6}, {"SC": .5, "EC": .5}) == pytest.approx(.1)
    with pytest.raises(ValueError):
        total_variation({}, {"SC": 1})


def test_monte_carlo_exercises_engine_not_server_calibration():
    report = monte_carlo(12000)
    assert report["draws"] == 12000 and report["passed"]
    assert report["ledger_errors"] == 0 and not report["server_fidelity_demonstrated"]
    assert len(report["cases"]) >= 100

def test_missing_middle_event_is_not_a_complete_v3_session():
    data = json.loads(export_session(recorded(5)))
    del data["simulation"]["journal"][2]
    with pytest.raises(ValueError, match="Historique v3 incomplet"):
        import_session(json.dumps(data).encode())


def test_collector_never_trains_on_its_own_sink_calculation():
    session = recorded(1)
    session.observed = State({"fo": 20}, D(10))
    observe(session.item, session.observed, Rune("fo"), "SN", {})
    result = collect(session, session_id="capture", split="train",
                     source=synthetic_row()["source"], corpus_id="test")
    assert session.observed.sink == 9
    assert result["observations"][0]["before"]["sink"] is None
    assert result["observations"][0]["after"]["sink"] is None


def test_ethereal_catalogue_is_not_treated_as_a_fixed_natural_jet():
    from types import SimpleNamespace
    from utils.exo_data import from_detail
    detail = SimpleNamespace(
        entry=SimpleNamespace(name="Épée éthérée de test", category="Épée", kind="item", token="test"),
        data={"stats": ["+10 en force"]}, stale=False,
    )
    item = from_detail(detail)
    assert not item.automatic
    assert "éthérée" in item.unsupported[0]

def test_wilson_near_one_confidence_stays_finite():
    low, high = wilson(1, 100, .9999999999999999)
    assert 0 <= low < .01 < high <= 1
