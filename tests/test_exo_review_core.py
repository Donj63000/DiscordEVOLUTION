"""Regressions de la review FM : frontieres, invariants et contrat IA sans Discord."""
import copy
import json

import pytest

from utils.exo_advice import (
    REFERENCE_VERSION, RUNE_NAMES, fm_guide, model_reference, requested_rune,
    rune_details, session_advice,
)
from utils.exo_data import demo_item
from utils.exo_engine import (
    D, LEGACY_PROFILE, PROFILE, STATS, Item, Rates, Rune, State, attempt,
    eligibility, observe, simulation_blocker, validate_item_jets,
)
from utils.exo_presentation import build_payload
from utils.exo_session import Session, export_session, import_session
from utils.exo_workshop import reset_simulation, set_goals, simulate_batch


def payload(session=None):
    return json.loads(export_session(session or Session.create(demo_item())))


def load(data):
    return import_session(json.dumps(data, ensure_ascii=True).encode("utf-8"))


def recorded_session(count=1):
    session = Session.create(Item("Anneau test", "fixture", {"fo": (1, 50)}, "fixture"))
    session.sim = State({"fo": 30}, D(1000))
    session.rune = Rune("fo")
    session.prices["fo:0"] = 25
    session.custom["fo:0"] = Rates(0, 0)
    for _ in range(count):
        attempt(session.item, session.sim, session.rune, session.rates, 1, session.price)
    return session


@pytest.mark.parametrize("jets", [
    {"pa": 2}, {"pm": 2}, {"pa": 1, "sa": 34}, {"pa": 1, "pm": 1, "po": 1},
])
def test_unrelated_invalid_line_is_blocked_before_any_mutation(jets):
    state = State(jets)
    before = copy.deepcopy(state)
    assert not eligibility(demo_item(), state, Rune("fo"))[0]
    assert "101" in simulation_blocker(demo_item(), state, Rune("fo"))
    with pytest.raises(ValueError, match="101"):
        attempt(demo_item(), state, Rune("fo"), Rates(1, 0), 1)
    assert state == before


@pytest.mark.parametrize("jets", [{"pa": 2}, {"pm": 2}, {"pa": 1, "pm": 1, "po": 1}])
def test_import_rejects_invalid_simulated_jet_even_if_observation_mode_is_selected(jets):
    data = payload()
    data["simulation"]["jets"] = jets
    data["mode"] = "observation"
    with pytest.raises(ValueError, match="101"):
        load(data)


@pytest.mark.parametrize("goals", ["pm=2", "pa=2", "pm=1;po=1", "pm=1;fo=12"])
def test_import_and_manual_goals_share_the_same_caps(goals):
    session = Session.create(demo_item())
    before = copy.deepcopy(session)
    with pytest.raises(ValueError, match="101"):
        set_goals(session, goals)
    assert session == before
    parsed = dict(part.split("=") for part in goals.split(";"))
    key = next(iter(parsed))
    data = payload(session)
    data["goal"] = {"stat": key, "value": int(parsed[key])}
    data["quality"] = {k: int(v) for k, v in parsed.items() if k != key}
    with pytest.raises(ValueError, match="101"):
        load(data)


def test_zero_quality_threshold_remains_compatible_with_imports():
    data = payload()
    data["quality"]["fo"] = 0
    session = load(data)
    assert session.quality["fo"] == 0
    assert import_session(export_session(session)).requirements == session.requirements


def test_invalid_goals_cannot_be_used_by_batch_through_direct_python_mutation():
    session = Session.create(demo_item())
    session.goal_value = 2
    before = copy.deepcopy(session)
    with pytest.raises(ValueError, match="Objectif"):
        simulate_batch(session, 1)
    assert session == before


@pytest.mark.parametrize("bounds,jets,rune", [
    ({"vi": (400, 600)}, {"vi": 597}, Rune("vi")),
    ({"pa": (1, 2)}, {"pa": 1}, Rune("pa")),
    ({"ini": (1000, 2000)}, {"ini": 1990}, Rune("ini")),
])
def test_large_natural_maxima_can_still_be_restored_and_exported(bounds, jets, rune):
    item = Item("Maximum naturel", "fixture", bounds, "fixture")
    state = State(jets)
    validate_item_jets(item, state.jets)
    attempt(item, state, rune, Rates(1, 0), 1)
    session = Session.create(item)
    session.sim = state
    assert import_session(export_session(session)).sim == state


def test_observed_out_of_profile_jet_is_retained_and_labelled_not_simulated():
    data = payload()
    data["observations"]["jets"] = {"pa": 2}
    data["mode"] = "observation"
    session = load(data)
    assert session.observed.jets == {"pa": 2}
    assert session.sim.jets == {"pa": 1}
    assert "hors profil" in session.notice
    advice = session_advice(session)
    assert not advice["admissibilite"]["jet_conforme_profil"]
    assert not advice["admissibilite"]["simulation_possible"]
    assert advice["taux_prochaine_pose"] is None
    assert import_session(export_session(session)).observed == session.observed


def test_malus_remains_trackable_but_not_simulatable():
    session = Session.create(Item("Malus", "fixture", {"fo": (-20, -1)}, "fixture"))
    observe(session.item, session.observed, Rune("fo"), "EC", {"fo": 1})
    other = import_session(export_session(session))
    assert other.observed == session.observed
    assert "malus" in simulation_blocker(other.item, other.sim, other.rune).lower()


@pytest.mark.parametrize("field", ["weight", "sink_before", "sink_after", "unexplained_weight"])
@pytest.mark.parametrize("mode", ["simulation", "observations"])
def test_comma_decimals_are_canonical_and_renderable_after_import(field, mode):
    session = recorded_session()
    if mode == "observations":
        session.observed = State({"fo": 30}, D(1000))
        observe(session.item, session.observed, Rune("fo"), "EC", {}, 25)
    data = payload(session)
    row = data[mode]["journal"][-1]
    row[field] = f"{D(row[field]):.2f}".replace(".", ",")
    imported = load(data)
    state = imported.sim if mode == "simulation" else imported.observed
    assert isinstance(state.journal[-1][field], str)
    assert "," not in state.journal[-1][field]
    for current_mode in ("simulation", "observation"):
        imported.mode = current_mode
        for tab in ("atelier", "maths", "journal", "settings", "aide"):
            imported.tab = tab
            assert build_payload(imported)
    assert import_session(export_session(imported)).sim == imported.sim
    assert import_session(export_session(imported)).observed == imported.observed


@pytest.mark.parametrize("path", [
    ("item", "name"), ("item", "token"), ("item", "source"), ("warning",),
    ("item", "unsupported"), ("item", "immutable"),
])
def test_invalid_unicode_is_rejected_at_import_even_in_ignored_metadata(path):
    data = payload()
    parent = data
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = ["\ud800"] if path[-1] in {"unsupported", "immutable"} else "\ud800"
    with pytest.raises(ValueError, match="Unicode"):
        load(data)


def test_invalid_unicode_event_source_is_rejected():
    data = payload(recorded_session())
    data["simulation"]["journal"][0]["rates"]["source"] = "\udfff"
    with pytest.raises(ValueError, match="Unicode"):
        load(data)


def test_valid_accents_and_surrogate_pairs_round_trip():
    data = payload()
    data["item"]["name"] = "Épée 🔨"
    imported = load(data)
    assert import_session(export_session(imported)).item.name == "Épée 🔨"


def test_export_reports_invalid_local_text_as_a_user_value_error():
    session = Session.create(Item("\ud800", "fixture", {"pa": (1, 1)}, "fixture"))
    with pytest.raises(ValueError, match="Unicode"):
        export_session(session)


def test_snapshot_cannot_contradict_last_event():
    session = Session.create(demo_item())
    attempt(session.item, session.sim, session.rune, None, 1)
    data = payload(session)
    data["simulation"]["jets"] = {"pa": 1, "pm": 1}
    with pytest.raises(ValueError, match="Jets du journal"):
        load(data)


def test_snapshot_detects_a_changed_line_not_touched_by_the_last_event():
    session = Session.create(Item("Deux lignes", "fixture", {"fo": (1, 50), "vi": (1, 100)}, "fixture"))
    session.sim = State({"fo": 10, "vi": 10}, D(100))
    attempt(session.item, session.sim, Rune("fo"), Rates(1, 0), 1)
    attempt(session.item, session.sim, Rune("vi"), Rates(1, 0), 1)
    data = payload(session)
    data["simulation"]["jets"]["fo"] += 1
    with pytest.raises(ValueError, match="Jets du journal"):
        load(data)


@pytest.mark.parametrize("change", ["snapshot_sink", "intermediate_sink", "tail_number", "gap", "changes"])
def test_journal_tail_requires_a_coherent_chain(change):
    data = payload(recorded_session(3))
    state = data["simulation"]
    if change == "snapshot_sink":
        state["sink"] = "500"
    elif change == "intermediate_sink":
        state["journal"][1]["sink_before"] = "500"
    elif change == "tail_number":
        state["sequence"] += 1
    elif change == "gap":
        state["journal"].pop(1)
    else:
        # Delta still correct in isolation, but the adjacent rows disagree.
        pair = state["journal"][1]["changes"]["fo"]
        state["journal"][1]["changes"]["fo"] = [value + 1 for value in pair]
    with pytest.raises(ValueError):
        load(data)


def test_complete_long_session_round_trip():
    session = recorded_session(150)
    imported = import_session(export_session(session))
    assert imported.sim == session.sim
    assert len(imported.sim.journal) == 150
    assert imported.sim.journal[0]["n"] == 1


def test_legacy_history_is_readable_and_requires_an_explicit_new_start():
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures/fm_retro/legacy-v1.json"
    imported = import_session(fixture.read_bytes())
    with pytest.raises(ValueError, match="Ancien modèle"):
        attempt(imported.item, imported.sim, imported.rune, Rates(0, 0), 1)
    assert import_session(export_session(imported)).sim == imported.sim
    assert "lecture seule" in imported.notice
    reset_simulation(imported, "minimum", 1)
    attempt(imported.item, imported.sim, imported.rune, Rates(0, 0), 1)
    assert imported.sim.sequence == 1


def test_reset_starts_a_new_journal_chain_without_touching_observations():
    session = recorded_session(3)
    session.observed = State({"fo": 23}, None)
    reset_simulation(session, "minimum", 1)
    assert not session.sim.journal
    assert import_session(export_session(session)).observed.jets == {"fo": 23}


@pytest.mark.parametrize("alias,rune", list(RUNE_NAMES.items()))
def test_every_authorized_alias_keeps_its_exact_rune(alias, rune):
    assert requested_rune(f"Peux-tu poser une {alias} sur mon objet stp ?") == rune


@pytest.mark.parametrize("text", [
    "Ne pose pas une Fo", "Dois-je poser une Fo ?", "Si utile pose une Fo",
    '"pose une Fo"', "« pose une Fo »", "Il a dit pose une Fo",
    "Pose deux Fo", "Pose une Fo puis une Vi", "pose une Fo; recommence",
    "pose une Fo\npose une Vi", "pose une Fo sur l'objet d'Alex",
])
def test_routing_parser_does_not_expand_action_authorization(text):
    assert requested_rune(text) is None


@pytest.mark.parametrize("key", list(STATS))
def test_guide_distinguishes_unit_weight_from_full_weight_for_every_tier(key):
    for tier in range(len(STATS[key].gains)):
        rune = Rune(key, tier)
        guide = fm_guide(f"Quel poids pour une {rune.name} ?")
        assert guide["runes"] == [rune_details(rune)]
        details = guide["runes"][0]
        assert D(details["poids_total"]) == rune.weight
        assert D(details["poids_nominal"]) == rune.gain * STATS[key].weight
        assert D(details["poids_par_point"]) == STATS[key].weight


def test_pa_prefix_never_selects_an_ap_rune_by_accident():
    guide = fm_guide("Compare Pa Fo et Ra Fo")
    assert [rune["nom"] for rune in guide["runes"]] == ["Pa Fo", "Ra Fo"]


def test_reference_does_not_claim_server_calibration_and_versions_its_seed_profile():
    assert model_reference()["version"] == REFERENCE_VERSION
    assert model_reference()["statut"] == "reference_documentee_modele_non_calibre"
    assert not model_reference()["certifie_ankama"]
    assert PROFILE == "retro-documented-v3"
    assert STATS["vi"].weight == D(".25")
    assert STATS["so"].weight == D(20)


@pytest.mark.parametrize("reason", ["unknown_sink", "uninterpreted", "line_cap", "observation"])
def test_no_usable_probability_is_exposed_for_a_blocked_simulation(reason):
    session = Session.create(demo_item())
    if reason == "unknown_sink":
        session.sim.sink = None
    elif reason == "uninterpreted":
        session.item = Item("Inconnu", "fixture", {"pa": (1, 1)}, "fixture", ("effet inconnu",))
    elif reason == "line_cap":
        session.sim.jets["pm"] = 1
    else:
        session.mode = "observation"
    result = session_advice(session)
    assert not result["admissibilite"]["simulation_possible"]
    assert result["taux_prochaine_pose"] is None


def test_snapshot_uses_effective_heavy_exo_rates_not_ignored_custom_rates():
    session = Session.create(demo_item())
    session.custom["pm:0"] = Rates(1, 0)
    result = session_advice(session)
    assert result["taux_prochaine_pose"]["sc"] == .01
    assert result["taux_prochaine_pose"]["sn"] == 0
    assert result["rune"]["poids_total"] == "90"
    assert {"seed", "prices", "budget", "journal"}.isdisjoint(result)


def test_observed_out_of_profile_warning_survives_clearing_last_action_notice():
    session = Session.create(demo_item())
    session.mode = "observation"
    session.observed.jets["pa"] = 2
    session.notice = ""
    displayed = json.dumps(build_payload(session), ensure_ascii=False)
    assert "hors profil local" in displayed


def test_export_still_rejects_circular_python_containers_without_looping():
    session = Session.create(demo_item())
    row = {}
    row["self"] = row
    session.sim.journal.append(row)
    with pytest.raises(ValueError, match="Circular"):
        export_session(session)
