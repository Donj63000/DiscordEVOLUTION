"""Grades publics de Crocabulia figés ; transports et identifiants de test synthétiques."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests_evo.helpers import context
from utils.dofus_wiki import WikiDetail, WikiEntry, search_key
from utils.evo_monsters import monster_statistics
from utils.evo_tools import EvoTools


CROCABULIA_GRADES = [
    {"grade": 1, "level": 400, "hp": 0, "ap": 0, "mp": 0,
     "resist": {"neutral": 50, "earth": -20, "fire": 20, "water": 20, "air": 20}},
    {"grade": 2, "level": 420, "hp": 0, "ap": 0, "mp": 0,
     "resist": {"neutral": 52, "earth": -18, "fire": 22, "water": 22, "air": 22}},
    {"grade": 3, "level": 440, "hp": 0, "ap": 0, "mp": 0,
     "resist": {"neutral": 54, "earth": -16, "fire": 24, "water": 24, "air": 24}},
    {"grade": 4, "level": 460, "hp": 0, "ap": 0, "mp": 0,
     "resist": {"neutral": 56, "earth": -14, "fire": 26, "water": 26, "air": 26}},
    {"grade": 5, "level": 480, "hp": 0, "ap": 0, "mp": 0,
     "resist": {"neutral": 58, "earth": -12, "fire": 28, "water": 28, "air": 28}},
]


def test_crocabulia_level_and_resistance_ranges_are_computed_without_fabricating_stats():
    result = monster_statistics({"grades": copy.deepcopy(CROCABULIA_GRADES)})
    assert result["plage_niveau"] == [400, 480]
    assert result["niveau_affiche"] == "Niveau 400 à 480 selon le grade."
    assert result["nombre_grades_renseignes"] == 5
    assert result["plages_pv_pa_pm"] == {"pv": None, "pa": None, "pm": None}
    assert result["plages_resistances_pourcent"] == {
        "neutre": [50, 58], "terre": [-20, -12], "feu": [20, 28],
        "eau": [20, 28], "air": [20, 28],
    }
    assert result["donnees_non_renseignees"] == ["PV", "PA", "PM"]
    assert result["donnees_partielles"] == []
    assert result["grades_incomplets"] is False
    assert [row["grade"] for row in result["grades"]] == [1, 2, 3, 4, 5]
    assert [row["niveau"] for row in result["grades"]] == [400, 420, 440, 460, 480]
    assert result["grades"][2]["resistances_pourcent"]["terre"] == -16
    assert all(row["pv"] is None and row["pa"] is None and row["pm"] is None for row in result["grades"])


def test_actual_hp_ap_mp_keys_are_mapped_and_isolated_zero_stats_remain_known():
    result = monster_statistics({"grades": [
        {"grade": 1, "level": 10, "hp": 1200, "ap": 0, "mp": 3},
        {"grade": 2, "level": 20, "hp": 1500, "ap": 6, "mp": 4},
    ]})
    assert result["plages_pv_pa_pm"] == {"pv": [1200, 1500], "pa": [0, 6], "pm": [3, 4]}
    assert result["grades"][0]["pa"] == 0
    assert result["grades"][1]["pv"] == 1500
    assert "PA" not in result["donnees_non_renseignees"]


def test_partial_and_absent_fields_are_not_filled_from_another_grade():
    result = monster_statistics({"grades": [
        {"grade": 1, "level": 10, "hp": 1200, "ap": 6, "mp": 3,
         "resist": {"neutral": 0, "earth": -20}},
        {"grade": 2, "level": 20, "ap": 6, "mp": 3, "resist": {"neutral": 10}},
    ]})
    assert result["grades"][1]["pv"] is None
    assert result["grades"][1]["resistances_pourcent"]["terre"] is None
    assert result["plages_pv_pa_pm"]["pv"] == [1200, 1200]
    assert result["plages_resistances_pourcent"]["neutre"] == [0, 10]
    assert result["plages_resistances_pourcent"]["feu"] is None
    assert result["donnees_partielles"] == ["PV", "résistance terre"]


@pytest.mark.parametrize("grades", [None, {}, "inconnu", []])
def test_missing_grades_remain_unknown(grades):
    result = monster_statistics({"grades": grades})
    assert result["grades"] == []
    assert result["plage_niveau"] is None
    assert result["plages_pv_pa_pm"]["pv"] is None
    assert all(value is None for value in result["plages_resistances_pourcent"].values())


def test_invalid_numeric_types_do_not_become_stats_and_missing_grade_id_is_not_invented():
    result = monster_statistics({"grades": [
        {"level": True, "hp": "1500", "ap": False, "mp": {}, "resist": {"neutral": True}},
    ]})
    assert result["grades"][0]["grade"] is None
    assert result["grades"][0]["niveau"] is None
    assert result["plages_pv_pa_pm"] == {"pv": None, "pa": None, "pm": None}
    assert result["plages_resistances_pourcent"]["neutre"] is None
    assert result["grades_incomplets"] is True


def test_grade_limit_is_explicit_instead_of_claiming_full_coverage():
    result = monster_statistics({"grades": CROCABULIA_GRADES * 2})
    assert len(result["grades"]) == 8
    assert result["grades_incomplets"] is True


@pytest.mark.asyncio
async def test_monster_tool_exposes_level_phrase_stats_and_grade_percentages_without_xixou():
    ctx = context()
    entry = WikiEntry("monster", "crocabulia", "Crocabulia", "Monstre", "400 à 480",
                      "/monstres/crocabulia/", search_key("Crocabulia"))
    detail = WikiDetail(entry, {"id": 77, "grades": copy.deepcopy(CROCABULIA_GRADES)}, None, False)
    ctx.bot.cogs["DofusWikiCog"] = SimpleNamespace(enrichment_client=None)
    tools = EvoTools()
    tools.resolve = AsyncMock(return_value=(entry, None))
    tools.detail = AsyncMock(return_value=(detail, None))
    result = await tools.execute("monstre", json.dumps({"nom": "Crocabulia", "page": 1}), ctx, {"monstre"})
    assert result["niveau_affiche"] == "Niveau 400 à 480 selon le grade."
    assert result["source"] == "https://wiki.moon-bot.io/monstres/crocabulia/"
    assert len(result["grades"]) == 5
    assert result["plages_resistances_pourcent"]["terre"] == [-20, -12]
    assert result["plages_pv_pa_pm"]["pv"] is None
    assert "PV" in result["donnees_non_renseignees"]
