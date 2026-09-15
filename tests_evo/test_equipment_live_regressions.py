"""Régressions des filtres sur le Voile d'encre, avec une fiche wiki publique figée."""
from dataclasses import replace

import pytest

from utils.dofus_wiki import parse_entries
from utils.evo_config import EvoError
from utils.evo_equipment import build_index, category_name, equipment_search, exo_candidates


VOILE = {
    "id": 8876, "name": "Voile d\\'encre", "type": "Cape", "level": 191,
    "url": "https://wiki.moon-bot.io/items/voile-d-encre/",
}
EFFECTS = [
    "+251 à 350 en vitalité", "+51 à 70 en force", "+51 à 70 en intelligence",
    "+26 à 40 en sagesse", "+6 à 10 de dommages",
    "+6 à 10 % de résistance à l\\'eau", "+6 à 10 % de résistance à l\\'air",
    "+6 à 10 % de résistance au feu", "+1 à la portée", "+2 à 3 aux coups critiques",
]


@pytest.fixture
def voile_rows():
    entries = parse_entries([VOILE], "item")
    catalog = {"data": {"capes": [{
        "id": VOILE["id"], "name": VOILE["name"], "level": VOILE["level"], "effets": EFFECTS,
    }]}}
    rows, info = build_index(entries, catalog)
    assert info["objets_indexes"] == 1
    return rows


def search(rows, **changes):
    params = {
        "category": "cape", "min_level": 1, "max_level": 200,
        "priorities": ["force", "vitalite"], "no_malus": [], "query": "",
    }
    return equipment_search(rows, **(params | changes))


@pytest.mark.parametrize("category", ["cape", "Cape", "CAPES", " capes "])
def test_cape_for_level_200_character_includes_verified_level_191_item(voile_rows, category):
    result = search(voile_rows, category=category)
    assert result["correspondances"] == 1
    item = result["resultats"][0]
    assert item["objet"] == "Voile d'encre"
    assert item["reference"] == "item:8876"
    assert item["niveau"] == 191
    assert item["jets_naturels"]["Force"] == [51, 70]
    assert item["jets_naturels"]["Vitalité"] == [251, 350]
    assert item["source"] == VOILE["url"]
    assert result["filtres_appliques"]["niveau_min"] == 1
    assert result["filtres_appliques"]["niveau_max"] == 200
    assert result["premier_filtre_sans_resultat"] is None


def test_normalized_index_category_is_also_used_by_equipment_and_exo_filters(voile_rows):
    indexed = replace(voile_rows[0], entry=replace(voile_rows[0].entry, category="Capes"))
    assert search([indexed])["correspondances"] == 1
    result = exo_candidates([indexed], target="pa", category="cape", min_level=1, max_level=200,
                            references=["item:8876"])
    assert result["resultats"][0]["reference"] == "item:8876"
    assert result["exclus"] == []


def test_explicit_exact_item_level_stays_strict_and_explains_the_filter(voile_rows):
    result = search(voile_rows, min_level=200)
    assert result["resultats"] == []
    assert result["diagnostic_filtres"]["type"] == 1
    assert result["diagnostic_filtres"]["niveau"] == 0
    assert result["premier_filtre_sans_resultat"] == "niveau"
    assert "niveau d'objet exact" in result["precision_niveau"]
    assert "plafond" in result["precision_niveau"]


def test_character_below_item_level_never_receives_the_item(voile_rows):
    result = search(voile_rows, max_level=190)
    assert result["resultats"] == []
    assert result["premier_filtre_sans_resultat"] == "niveau"


def test_explicit_name_words_match_like_native_equipment_search(voile_rows):
    result = search(voile_rows, query="encre voile")
    assert result["resultats"][0]["reference"] == "item:8876"


def test_mistaken_class_name_filter_is_visible_in_diagnostics_not_silently_ignored(voile_rows):
    result = search(voile_rows, query="cra")
    assert result["resultats"] == []
    assert result["filtres_appliques"]["nom_contient"] == "cra"
    assert result["diagnostic_filtres"]["niveau"] == 1
    assert result["premier_filtre_sans_resultat"] == "nom"


def test_total_build_pa_pm_constraints_cannot_be_faked_as_item_stats(voile_rows):
    result = search(voile_rows, priorities=["force", "pa", "pm"])
    assert result["resultats"] == []
    assert result["diagnostic_filtres"]["nom"] == 1
    assert result["premier_filtre_sans_resultat"] == "priorites"


def test_wrong_category_and_invalid_ranges_are_not_relaxed(voile_rows):
    assert search(voile_rows, category="coiffe")["premier_filtre_sans_resultat"] == "type"
    with pytest.raises(EvoError):
        search(voile_rows, min_level=200, max_level=191)
    with pytest.raises(EvoError):
        category_name("type inconnu")


def test_same_public_name_with_conflicting_identifier_is_not_matched():
    entries = parse_entries([VOILE], "item")
    catalog = {"data": {"capes": [{
        "id": 999999, "name": VOILE["name"], "level": 191, "effets": EFFECTS,
    }]}}
    rows, info = build_index(entries, catalog)
    assert rows == ()
    assert info["non_rapproches_ou_sans_stats"] == 1
