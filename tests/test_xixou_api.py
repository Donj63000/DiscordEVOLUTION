"""Je vérifie les catalogues Xixou sans clé réelle ni connexion extérieure."""

import asyncio
from dataclasses import replace
from email.utils import formatdate
import json
import threading
from unittest.mock import AsyncMock

import aiohttp
from aioresponses import CallbackResult, aioresponses
import pytest

from utils.dofus_wiki import WikiDetail, WikiEntry
from utils.xixou_api import (
    CatalogCache, FAMILIES, ItemEnrichment, MAX_STALE_SECONDS, XIXOU_ORIGIN, XixouClient, XixouError,
    build_enrichment, identity_key, percent, validate_catalog,
)


def item(name="Gelano", identifier=2469, category="Anneau", level=60):
    entry = WikiEntry("item", str(identifier), name, category, level, "/items/gelano/", name)
    return WikiDetail(entry, {"id": identifier, "name": name, "level": level}, None, False)


def envelope(family, data):
    return {"famille": family, "genere_le": "2026-09-01T10:30:07+00:00", "data": data}


def catalogs(equipment=None, resources=None, monsters=None, carte=None):
    return {
        "equipements": envelope("equipements", equipment or {}),
        "ressources": envelope("ressources", resources or {}),
        "monstres": envelope("monstres", monsters or []),
        "carte": envelope("carte", carte or {"zones": [], "resources": {}}),
    }


def gelano_catalogs():
    rows = catalogs(equipment={"anneaux": [{
        "name": "Gelano", "level": "60", "pods": "5", "description": "Un anneau.",
        "effets": ["+1 PA"], "conditions": ["Niveau > 59"], "panoplie": "Panoplie Gelax",
        "drops": [{"name": "Gelée Fraise", "rate": "0.07%", "pp": 1000},
                  {"name": "Gelaviv le Glaçon", "rate": "0.14%", "pp": 1000}],
    }]}, monsters=[
        {"id": 57, "name": "Gelée Fraise - Gelaviv le Glaçon",
         "stats": {"niveau": {"ranks": {f"rank_{i}": str(level)
                                            for i, level in enumerate([24, 26, 28, 29, 30], 1)}}},
         "zones": ["La péninsule des gelées"],
         "drops": [{"name": "Gelano", "taux": "0.07%", "pp": 1000, "max": "1",
                    "taux_ranks": [0.03, 0.04, 0.05, 0.06, 0.07]}]},
        {"id": 2376, "name": "Gelaviv le Glaçon - Gelée Fraise",
         "stats": {"niveau": {"ranks": {f"rank_{i}": str(level)
                                            for i, level in enumerate([24, 26, 28, 29, 30], 1)}}},
         "zones": ["La péninsule des gelées"],
         "drops": [{"name": "Gelano", "taux": "0.14%", "pp": 1000, "max": "1",
                    "taux_ranks": [0.06, 0.08, 0.10, 0.12, 0.14]}]},
    ], carte={"zones": [{"name": "La péninsule des gelées", "cells": [[11, 28], [11, 29]],
                          "polygon": [[5.5, 27.5], [11.5, 27.5], [11.5, 30.5]]}],
              "resources": {}})
    return rows


def api_url(family):
    return f"{XIXOU_ORIGIN}/api/v1/{family}.json"


def valid_payload(family):
    if family == "monstres":
        return envelope(family, [{"id": 1, "name": "Monstre"}])
    if family == "carte":
        return envelope(family, {"zones": [], "resources": {}})
    if family == "ressources":
        return envelope(family, {"bois": [{"name": "Bois de Frêne", "level": "1"}]})
    return envelope(family, {"anneaux": [{"name": "Gelano", "level": "60"}]})


@pytest.mark.parametrize("value,expected", [
    (0, "0%"), (0.0, "0%"), (-0.0, "0%"), (0.001, "0.001%"),
    ("0,030 %", "0.03%"), ("1E-7", "0.0000001%"), ("100.0", "100%"),
    ("", ""), (None, ""), (True, ""), ("NaN", ""), ("Infinity", ""),
    (-1, ""), (101, ""), ("1e-999999", ""), ("1" * 100, ""),
])
def test_percent_preserves_small_values_and_does_not_invent_missing_rates(value, expected):
    assert percent(value) == expected


def test_identity_normalizes_typography_but_keeps_meat_stars_and_qualifiers():
    assert identity_key(" VIANDE   d’OISEAU ** ") == "viande d'oiseau **"
    assert identity_key("Viande d'Oiseau **") != identity_key("Viande d'Oiseau ****")
    assert identity_key("Petite Épée") != identity_key("Épée")


def test_gelano_shows_rank_ranges_archmonster_and_exact_zone_geometry():
    result = build_enrichment(item(), gelano_catalogs())
    assert result.description == "Un anneau."
    assert result.effects == ("+1 PA",)
    assert result.weight == 5 and result.level == 60
    assert result.details == (("Conditions", ("Niveau > 59",)), ("Panoplie", ("Panoplie Gelax",)))
    assert [drop.name for drop in result.drops] == ["Gelée Fraise", "Gelaviv le Glaçon"]
    assert result.drops[0].rate == "0.03% – 0.07%"
    assert result.drops[0].level_rates == (
        ("24", "0.03%"), ("26", "0.04%"), ("28", "0.05%"), ("29", "0.06%"), ("30", "0.07%"),
    )
    assert result.drops[1].rate == "0.06% – 0.14%"
    assert result.drops[0].pp == "1000" and result.drops[0].maximum == "1"
    assert result.zones[0].cells == ((11.0, 28.0), (11.0, 29.0))
    assert result.dates == ("2026-09-01T10:30:07+00:00",)
    assert result.stale is False


def test_inactive_ranks_are_excluded_and_suspect_levels_are_identified():
    data = gelano_catalogs()
    monster = data["monstres"]["data"][0]
    monster["ranks_inactifs"] = [5]
    monster["ranks_suspects"] = [2]
    result = build_enrichment(item(), data)
    assert result.drops[0].rate == "0.03% – 0.06%"
    assert result.drops[0].level_rates[1] == ("26 (à vérifier)", "0.04%")
    assert len(result.drops[0].level_rates) == 4


def test_rank_rates_without_levels_still_show_the_range_instead_of_only_maximum():
    data = gelano_catalogs()
    data["monstres"]["data"][0]["stats"] = {}
    result = build_enrichment(item(), data)
    assert result.drops[0].rate == "0.03% – 0.07%"
    assert result.drops[0].level_rates == ()


def test_all_inactive_ranks_do_not_fall_back_to_an_inactive_maximum_rate():
    data = gelano_catalogs()
    data["monstres"]["data"][0]["ranks_inactifs"] = [1, 2, 3, 4, 5]
    result = build_enrichment(item(), data)
    assert result.drops[0].rate == "" and result.drops[0].level_rates == ()


def test_bouftou_identifier_selects_real_monster_instead_of_summoned_namesake():
    data = catalogs(resources={"laine": [{
        "name": "Laine de Bouftou", "level": "9",
        "drops": [{"monster_name": "Bouftou", "monster_id": 101,
                   "drop_rate_percent": 80, "pp": 100, "drop_max": "∞"}],
    }]}, monsters=[
        {"id": 36, "name": "Bouftou", "race": "Invocations de classe", "drops": [],
         "zones": ["Zone erronée"]},
        {"id": 101, "name": "Bouftou - Boufdégou le Refoulant", "zones": ["Coin des Bouftous"],
         "stats": {"niveau": {"ranks": {"rank_1": "3", "rank_2": "15"}}},
         "drops": [{"name": "Laine de Bouftou", "categorie": "Laine", "taux": "80%",
                    "taux_ranks": [60, 80], "pp": 100, "max": "∞"}]},
    ])
    result = build_enrichment(item("Laine de Bouftou", 384, "Laine", 9), data)
    assert result.drops[0].rate == "60% – 80%"
    assert result.drops[0].zones == ("Coin des Bouftous",)
    assert result.drops[0].maximum == "∞"
    assert result.zones[0].cells == ()


def test_vulbis_keeps_tiny_drop_and_unmapped_zone_text():
    data = catalogs(equipment={"dofus": [{"name": "Dofus Vulbis", "level": 6, "drops": []}]},
                    monsters=[{"id": 854, "name": "Crocabulia",
                               "zones": ["Sanctuaire des Dragoeufs"],
                               "drops": [{"name": "Dofus Vulbis", "taux": "0.001%",
                                          "pp": 1000, "max": 1}]}])
    result = build_enrichment(item("Dofus Vulbis", 6980, "Dofus", 6), data)
    assert result.drops[0].rate == "0.001%"
    assert result.zones[0].name == "Sanctuaire des Dragoeufs"
    assert not result.zones[0].cells


@pytest.mark.parametrize("rate_field", ["drop_rate_percent", "rate_percent", "dropRate", "rate"])
def test_resource_drop_formats_support_zero_and_unlinked_monsters(rate_field):
    data = catalogs(resources={"minerai": [{"name": "Cuivre", "level": "10", "drops": [
        {"monster": "Coffre", rate_field: 0, "pp": 0, "drop_max": 0},
        {"monster_name": "Autre"},
    ]}]})
    result = build_enrichment(item("Cuivre", 441, "Minerai", 10), data)
    assert result.drops[0].rate == "0%" and result.drops[0].pp == "0"
    assert result.drops[0].maximum == "0"
    assert result.drops[1].rate == "" and result.drops[1].pp == ""


@pytest.mark.parametrize("value", [True, False, {}, [], -1, "-100", "invalid", "Infinity"])
def test_malformed_prospecting_and_maximum_are_not_displayed_as_real_numbers(value):
    data = catalogs(resources={"minerai": [{"name": "Cuivre", "level": "10", "drops": [
        {"monster": "Coffre", "rate": "51%", "pp": value, "drop_max": value},
    ]}]})
    result = build_enrichment(item("Cuivre", 441, "Minerai", 10), data)
    assert result.drops[0].pp == result.drops[0].maximum == ""


def test_malformed_monster_prospecting_does_not_override_item_data_with_raw_structures():
    data = gelano_catalogs()
    data["monstres"]["data"][0]["drops"][0].update({"pp": {}, "max": True})
    result = build_enrichment(item(), data)
    assert result.drops[0].pp == result.drops[0].maximum == ""


def test_resource_can_have_drops_harvest_and_multiple_craft_uses():
    data = catalogs(resources={"minerai": [{
        "name": "Cuivre", "level": "10", "drops": [{"monster": "Coffre", "dropRate": 51}],
        "crafts": [{"item_name": "Petite Amulette du Hibou"}, {"item_name": "Arc"},
                   {"item_name": "Arc"}],
    }]}, carte={"zones": [], "resources": {
        "mineur": [{"name": "Cuivre", "level": 20, "positions": [[2, -3, 4], [1, 0], [1, 0]]}],
    }})
    result = build_enrichment(item("Cuivre", 441, "Minerai", 10), data)
    assert len(result.drops) == 1
    assert result.harvests[0].job == "Mineur" and result.harvests[0].level == "20"
    assert result.harvests[0].positions == ((2.0, -3.0, 4), (1.0, 0.0, None))
    assert result.uses == ("Petite Amulette du Hibou", "Arc")


@pytest.mark.parametrize("name,tree", [("Bois de Frêne", "Frêne"), ("Bois d'Orme", "Orme")])
def test_wood_maps_only_to_exact_tree_and_harvest_category(name, tree):
    data = catalogs(resources={"bois": [{"name": name, "level": "1", "drops": []}]},
                    carte={"zones": [], "resources": {"bucheron": [
                        {"name": tree, "level": 1, "positions": [[1, 2, 3]]},
                        {"name": tree + " sombre", "level": 50, "positions": [[99, 99, 1]]},
                    ]}})
    result = build_enrichment(item(name, 1, "Bois", 1), data)
    assert result.harvests[0].name == tree
    assert result.harvests[0].positions == ((1.0, 2.0, 3),)
    assert result.drops == ()


@pytest.mark.parametrize("mutation", [
    {"id": 9999}, {"name": "Gelano Royal"}, {"level": "61"},
])
def test_incompatible_item_identifiers_names_and_levels_are_rejected(mutation):
    data = catalogs(equipment={"anneaux": [{"name": "Gelano", "level": "60", **mutation}]})
    assert build_enrichment(item(), data) is None


def test_category_disambiguates_homonyms_and_different_type_is_rejected():
    data = catalogs(equipment={"arcs": [{"name": "Nomoon", "level": 28}],
                               "familiers": [{"name": "Nomoon", "level": 1}]})
    assert build_enrichment(item("Nomoon", 1, "Familier", 1), data).level == 1
    assert build_enrichment(item("Nomoon", 1, "Botte", 1), data) is None


@pytest.mark.parametrize("moon_category,xixou_category", [
    ("Marteau", "marteaux"), ("Cadeau", "cadeaux"), ("Sac à dos", "sacs"),
    ("Objet de Quête", "objet-de-mission"), ("Carte commune", "carte-ttg"),
    ("Carte rare", "carte-ttg"), ("Carte épique", "carte-ttg"), ("Carte unique", "carte-ttg"),
    ("Potion d'oubli de sort", "potion-oubli-sort"), ("Objet d'élevage", "objet-elevage"),
    ("Certificat de mise en chanil", "certificat-chenil"), ("Fée d'artifice", "fee-artifice"),
    ("Fantôme de Familier", "fantome-de-familier"), ("Pierre d'âme pleine", "pierre-ame-pleine"),
    ("Parchemin de caractéristique", "parchemin-caracteristique"),
    ("Fragment d'âme de Shushu", "fragment-ame-de-shushu"),
    ("Pierre d'âme de gardien de donjon", "pierre-ame-pleine"),
])
def test_verified_category_aliases_match_real_catalog_taxonomies(moon_category, xixou_category):
    data = catalogs(resources={xixou_category: [{"name": "Objet de test", "level": 1}]})
    assert build_enrichment(item("Objet de test", 1, moon_category, 1), data) is not None


def test_exact_item_id_and_name_allow_documented_source_category_disagreement():
    data = catalogs(resources={"objet-de-mission": [{"id": 1, "name": "Suiveur", "level": 1}]})
    assert build_enrichment(item("Suiveur", 1, "Personnage suiveur", 1), data) is not None


def test_ambiguous_rows_are_not_merged_but_identical_duplicates_are_safe():
    row = {"name": "Gelano", "level": 60, "pods": 5}
    assert build_enrichment(item(), catalogs(equipment={"anneaux": [row, dict(row)]})).weight == 5
    assert build_enrichment(item(), catalogs(equipment={"anneaux": [row, {**row, "pods": 6}]})) is None


def test_confirmed_id_resolves_other_candidate_without_id():
    data = catalogs(equipment={"anneaux": [{"name": "Gelano", "level": 60, "pods": 99},
                                           {"name": "Gelano", "level": 60, "id": 2469, "pods": 5}]})
    assert build_enrichment(item(), data).weight == 5


def test_starred_meat_cannot_receive_other_rank_drops():
    data = catalogs(resources={"viande": [{"name": "Viande d'Oiseau ****", "level": 1}]},
                    monsters=[{"id": 1, "name": "Tofu", "drops": [
                        {"name": "Viande d'Oiseau **", "taux": "100%"},
                    ]}])
    assert build_enrichment(item("Viande d'Oiseau **", 1, "Viande", 1), data) is None
    assert build_enrichment(item("Viande d'Oiseau ****", 2, "Viande", 1), data).drops == ()


def test_reverse_monster_drops_do_not_cross_known_duplicate_item_identities():
    data = catalogs(resources={"cadeaux": [
        {"name": "Cadeau", "id": 1, "level": 1}, {"name": "Cadeau", "id": 2, "level": 1},
    ]}, monsters=[{"id": 1, "name": "Tofu", "drops": [{"name": "Cadeau", "taux": "100%"}]}])
    assert build_enrichment(item("Cadeau", 1, "Cadeau", 1), data).drops == ()


def test_reverse_monster_drops_do_not_cross_same_level_namesakes_in_other_categories():
    data = catalogs(resources={"fruit": [{"name": "Pomme", "level": 1}],
                               "friandise": [{"name": "Pomme", "level": 1}]},
                    monsters=[{"id": 1, "name": "Tofu", "drops": [
                        {"name": "Pomme", "taux": "100%"},
                    ]}])
    assert build_enrichment(item("Pomme", 1, "Fruit", 1), data).drops == ()


def test_equipment_never_uses_explicit_resource_monster_drop_with_identical_name():
    data = catalogs(equipment={"anneaux": [{"name": "Homonyme", "level": 1,
                                           "drops": [{"name": "Tofu", "rate": "5%"}]}]},
                    resources={"friandise": [{"name": "Homonyme", "level": 1}]},
                    monsters=[{"id": 1, "name": "Tofu", "zones": ["Zone incorrecte"],
                               "drops": [{"name": "Homonyme", "type": "ressource",
                                          "taux": "100%"}]}])
    result = build_enrichment(item("Homonyme", 1, "Anneau", 1), data)
    assert result.drops[0].rate == "5%" and result.drops[0].zones == ()


def test_reverse_equipment_drop_rejects_explicit_resource_type_even_without_other_catalog():
    data = catalogs(equipment={"anneaux": [{"name": "Homonyme", "level": 1}]},
                    monsters=[{"id": 1, "name": "Tofu", "drops": [
                        {"name": "Homonyme", "type": "ressource", "taux": "100%"},
                    ]}])
    assert build_enrichment(item("Homonyme", 1, "Anneau", 1), data).drops == ()


def test_wrong_monster_id_does_not_attach_another_monsters_zones_or_ranks():
    data = gelano_catalogs()
    data["equipements"]["data"]["anneaux"][0]["drops"][0]["monster_id"] = 999
    result = build_enrichment(item(), data)
    assert result.drops[0].rate == "0.07%"
    assert result.drops[0].level_rates == () and result.drops[0].zones == ()


@pytest.mark.parametrize("bad_id", [[], {}, "invalid", False])
def test_malformed_item_identifier_is_not_treated_as_an_absent_identifier(bad_id):
    data = catalogs(equipment={"anneaux": [{"name": "Gelano", "level": 60, "id": bad_id}]})
    assert build_enrichment(item(), data) is None


@pytest.mark.parametrize("bad_id", [[], {}, "invalid", False])
def test_malformed_monster_identifier_keeps_only_documented_item_drop(bad_id):
    data = gelano_catalogs()
    data["equipements"]["data"]["anneaux"][0]["drops"][0]["monster_id"] = bad_id
    result = build_enrichment(item(), data)
    assert result.drops[0].level_rates == ()


def test_malformed_optional_monster_fields_do_not_break_valid_item_data():
    data = gelano_catalogs()
    data["monstres"]["data"].append({"id": [], "name": "Monstre", "drops": []})
    result = build_enrichment(item(), data)
    assert result.effects == ("+1 PA",)


def test_ambiguous_monsters_keep_documented_item_rate_without_guessing():
    data = gelano_catalogs()
    monster = data["monstres"]["data"][0]
    data["monstres"]["data"].append({**monster, "id": 999})
    result = build_enrichment(item(), data)
    assert result.drops[0].rate == "0.07%" and result.drops[0].zones == ()


def test_unknown_geometry_does_not_use_similar_zone_name():
    data = gelano_catalogs()
    data["carte"]["data"]["zones"][0]["name"] = "La péninsule des gelées royales"
    assert build_enrichment(item(), data).zones[0].cells == ()


def test_invalid_coordinates_and_duplicate_points_are_removed():
    data = gelano_catalogs()
    data["carte"]["data"]["zones"][0]["cells"] = [
        [1, 2], [1, 2], [False, 2], [float("nan"), 3], [1001, 0], ["1", 0], [1], None,
    ]
    assert build_enrichment(item(), data).zones[0].cells == ((1.0, 2.0),)


def test_weapon_fields_and_alternate_effects_format_are_supported():
    data = catalogs(equipment={"arcs": [{"name": "Arc", "level": 1,
                                       "effects": ["+5 Force"], "arme_stats": ["PA : 4"],
                                       "craft_de": [{"name": "Grand Arc"}]}]})
    result = build_enrichment(item("Arc", 1, "Arc", 1), data)
    assert result.effects == ("+5 Force",)
    assert result.details == (("Arme", ("PA : 4",)),)
    assert result.uses == ("Grand Arc",)


def test_missing_optional_data_keeps_enrichment_and_marked_staleness():
    data = {"equipements": envelope("equipements", {"anneaux": [{"name": "Gelano"}]})}
    result = build_enrichment(item(), data, stale=True)
    assert result.stale and result.level is None
    assert result.drops == result.zones == result.harvests == result.effects == ()


def test_item_image_comes_from_the_matched_catalog_category_not_display_category():
    data = catalogs(resources={"bois": [{"id": 1, "name": "Bois de Frêne", "level": 1,
                                        "category": "Champ non utilisé", "image": "17.svg"}]})
    result = build_enrichment(item("Bois de Frêne", 1, "Ressource", 1), data)
    assert result.image_candidates == (
        "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/images_bois/17.png",
    )


@pytest.mark.parametrize("identifier,rarity,image", [
    (14050, "communes", "communes-v2.png"),
    (14051, "rares", "rares-v2.png"),
    (14052, "épiques", "epiques-v2.png"),
])
def test_verified_packet_ids_match_exact_documented_names_despite_source_colon(
    identifier, rarity, image,
):
    data = catalogs(resources={"paquet-de-cartes": [{
        "name": f"Paquet de cartes {rarity}", "level": "1", "image": image,
    }]})
    result = build_enrichment(item(f"Paquet de cartes : {rarity}", identifier, "Paquet de cartes", 1),
                              data)
    assert result.image_candidates[0] == (
        "https://xixou.io/wp-content/uploads/xixou-ressources/tcg-images/paquets/" + image
    )


@pytest.mark.parametrize("identifier,name,category,level", [
    (999, "Paquet de cartes : communes", "Paquet de cartes", 1),
    (14051, "Paquet de cartes : communes", "Paquet de cartes", 1),
    (14050, "Paquet de cartes : rares", "Paquet de cartes", 1),
    (14050, "Paquet de cartes : communes", "Ressource", 1),
    (14050, "Paquet de cartes : communes", "Paquet de cartes", 2),
])
def test_packet_alias_does_not_apply_outside_its_verified_id_name_type_and_level(
    identifier, name, category, level,
):
    data = catalogs(resources={"paquet-de-cartes": [{
        "name": "Paquet de cartes communes", "level": 1, "image": "communes-v2.png",
    }]})
    assert build_enrichment(item(name, identifier, category, level), data) is None


@pytest.mark.parametrize("source_category,source_fields", [
    ("paquets", {}), ("paquet-de-cartes", {"level": 2}),
    ("paquet-de-cartes", {"id": 999}), ("paquet-de-cartes", {"level": None}),
])
def test_packet_alias_rejects_unverified_source_category_level_and_contradicting_id(
    source_category, source_fields,
):
    row = {"name": "Paquet de cartes communes", "level": 1, "image": "communes-v2.png",
           **source_fields}
    data = catalogs(resources={source_category: [row]})
    assert build_enrichment(item("Paquet de cartes : communes", 14050, "Paquet de cartes", 1),
                            data) is None


def test_packet_name_alias_does_not_broaden_normalization_of_other_items():
    data = catalogs(equipment={"anneaux": [{"name": "Anneau Gelano", "level": 60}]})
    assert build_enrichment(item("Anneau : Gelano", 2469, "Anneau", 60), data) is None


def test_item_image_is_preserved_with_a_verified_id_despite_source_type_disagreement():
    data = catalogs(resources={"objet-de-mission": [{"id": 1, "name": "Suiveur", "level": 1,
                                                    "image": "2.svg"}]})
    result = build_enrichment(item("Suiveur", 1, "Personnage suiveur", 1), data)
    assert result.image_candidates == (
        "https://xixou.io/wp-content/uploads/xixou-og/xixou-ressources/images_objet-de-mission/2.png",
    )


def test_distinct_items_with_same_image_basename_use_their_own_image_folder():
    data = catalogs(equipment={"anneaux": [{"name": "Gelano", "level": 60, "image": "47.svg"}]},
                    resources={"pierre-brute": [{"name": "Silex Sanglant", "level": 1,
                                                "image": "47.svg"}]})
    ring = build_enrichment(item(), data)
    stone = build_enrichment(item("Silex Sanglant", 2, "Pierre brute", 1), data)
    assert ring.image_candidates != stone.image_candidates
    assert "/xixou-anneaux/" in ring.image_candidates[0]
    assert "/images_pierre-brute/" in stone.image_candidates[0]


def test_equipment_namesakes_in_different_categories_keep_the_selected_image():
    data = catalogs(equipment={"arcs": [{"name": "Nomoon", "level": 28, "image": "1.svg"}],
                               "familiers": [{"name": "Nomoon", "level": 1, "image": "2.svg"}]})
    result = build_enrichment(item("Nomoon", 1, "Familier", 1), data)
    assert result.image_candidates == (
        "https://xixou.io/wp-content/uploads/xixou-og/xixou-familiers/2.png",
    )


@pytest.mark.parametrize("rows", [
    [{"name": "Gelano", "level": 60, "image": "47.svg", "id": 999}],
    [{"name": "Gelano", "level": 60, "image": "47.svg"},
     {"name": "Gelano", "level": 60, "image": "48.svg"}],
])
def test_item_identity_must_be_verified_before_its_image_can_be_returned(rows):
    assert build_enrichment(item(), catalogs(equipment={"anneaux": rows})) is None


def test_identical_item_duplicates_with_different_image_folders_keep_data_without_guessing_image():
    row = {"id": 1, "name": "Homonyme", "level": 1, "image": "17.svg", "pods": 5}
    data = catalogs(resources={"fruit": [row], "friandise": [dict(row)]})
    result = build_enrichment(item("Homonyme", 1, "Ressource", 1), data)
    assert result.weight == 5
    assert result.image_candidates == ()


def test_duplicate_categories_with_same_verified_image_folder_keep_the_image():
    row = {"id": 1, "name": "Paquet", "level": 1, "image": "communes-v2.png"}
    data = catalogs(resources={"paquet-de-cartes": [row], "paquets": [dict(row)]})
    result = build_enrichment(item("Paquet", 1, "Ressource", 1), data)
    assert result.image_candidates[0] == (
        "https://xixou.io/wp-content/uploads/xixou-ressources/tcg-images/paquets/communes-v2.png"
    )


@pytest.mark.parametrize("image", [None, "", [], "../47.svg", "https://untrusted.test/47.svg"])
def test_unavailable_or_invalid_image_does_not_remove_valid_item_information(image):
    data = catalogs(equipment={"anneaux": [{"name": "Gelano", "image": image, "pods": 5}]})
    result = build_enrichment(item(), data)
    assert result.weight == 5 and result.image_candidates == ()


def test_monster_details_are_outside_item_enrichment():
    detail = item()
    detail = replace(detail, entry=replace(detail.entry, kind="monster"))
    assert build_enrichment(detail, gelano_catalogs()) is None


@pytest.mark.parametrize("family,data", [
    ("equipements", []), ("equipements", {}), ("equipements", {"anneaux": []}),
    ("ressources", {"bois": [None]}), ("monstres", []), ("monstres", {}),
    ("carte", {"zones": []}), ("carte", {"zones": "broken", "resources": {}}),
])
def test_bad_catalog_shapes_cannot_replace_known_good_data(family, data):
    with pytest.raises(XixouError):
        validate_catalog(family, envelope(family, data))


@pytest.mark.parametrize("value", [None, [], {}, {"data": {}, "famille": "carte"},
                                    {"data": {"anneaux": [{"name": "Gelano"}]}, "famille": []},
                                    {"data": {"anneaux": [{"name": "Gelano"}]}, "famille": {}}])
def test_invalid_envelopes_are_rejected(value):
    with pytest.raises(XixouError):
        validate_catalog("equipements", value)


@pytest.mark.asyncio
async def test_disabled_client_never_creates_session_or_requests():
    client = XixouClient()
    assert not client.enabled
    await client.warmup()
    assert await client.enrich(item()) is None
    assert client._session is None
    await client.close()


@pytest.mark.asyncio
async def test_fixed_url_key_header_no_redirects_and_fresh_cache(caplog):
    client = XixouClient("private-test-key")
    caplog.set_level("DEBUG", logger="utils.xixou_api")
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), payload=valid_payload("equipements"),
                       headers={"ETag": '"version-1"'})
            first = await client.catalog("equipements")
            assert await client.catalog("equipements") is first
            request = next(iter(mocked.requests.values()))[0]
            assert request.kwargs["headers"]["X-Api-Key"] == "private-test-key"
            assert request.kwargs["allow_redirects"] is False
            assert len(mocked.requests) == 1
            assert client._cache["equipements"].etag == '"version-1"'
        assert "private-test-key" not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_etag_304_renews_existing_data_without_reparsing():
    clock = [100.0]
    client = XixouClient("test", ttl=60, clock=lambda: clock[0])
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), payload=valid_payload("equipements"),
                       headers={"ETag": '"version-1"'})
            first = await client.catalog("equipements")
            clock[0] += 61
            mocked.get(api_url("equipements"), status=304)
            assert await client.catalog("equipements") is first
            request = next(iter(mocked.requests.values()))[-1]
            assert request.kwargs["headers"]["If-None-Match"] == '"version-1"'
            assert client._cache["equipements"].fetched_at == clock[0]
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 401, 403, 404, 429, 500])
async def test_http_errors_keep_cache_and_do_not_follow_redirects(status):
    clock = [100.0]
    client = XixouClient("test", ttl=60, clock=lambda: clock[0])
    payload = valid_payload("equipements")
    client._cache["equipements"] = CatalogCache(payload, 0)
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), status=status,
                       headers={"Location": "https://untrusted.test", "Retry-After": "120"})
            assert await client.catalog("equipements") is payload
            assert client._cache["equipements"].fetched_at == 0
            assert await client.catalog("equipements") is payload
            assert sum(len(requests) for requests in mocked.requests.values()) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"body": "invalid json", "content_type": "application/json"},
    {"body": "{}", "content_type": "text/html"},
    {"payload": {"data": []}}, {"exception": aiohttp.ClientConnectionError("secret")},
    {"exception": asyncio.TimeoutError()},
    {"body": "[" * 1200 + "]" * 1200, "content_type": "application/json"},
])
async def test_malformed_network_responses_fall_back_without_leaking_exceptions(response, caplog):
    client = XixouClient("test")
    caplog.set_level("DEBUG", logger="utils.xixou_api")
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), **response)
            assert await client.catalog("equipements") is None
        assert "secret" not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_expired_cache_is_not_returned_after_one_day():
    client = XixouClient("test", clock=lambda: MAX_STALE_SECONDS + 1)
    client._cache["equipements"] = CatalogCache(valid_payload("equipements"), 0)
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), status=500)
            assert await client.catalog("equipements") is None
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["120", formatdate(usegmt=True, timeval=4_000_000_000)])
async def test_retry_after_prevents_requests_to_other_catalogs(retry_after):
    client = XixouClient("test", clock=lambda: 100.0)
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), status=429, headers={"Retry-After": retry_after})
            assert await client.catalog("equipements") is None
            assert await client.catalog("ressources") is None
            assert client._backoff_until >= 220
            assert len(mocked.requests) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_backoff_expires_and_success_replaces_old_cache():
    clock = [100.0]
    client = XixouClient("test", clock=lambda: clock[0])
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), status=429, headers={"Retry-After": "120"})
            await client.catalog("equipements")
            clock[0] += 121
            mocked.get(api_url("equipements"), payload=valid_payload("equipements"))
            assert await client.catalog("equipements") == valid_payload("equipements")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_fetches_share_download_and_waiter_cancellation_isolated():
    client = XixouClient("test")
    started, release = asyncio.Event(), asyncio.Event()

    async def callback(url, **kwargs):
        started.set()
        await release.wait()
        return CallbackResult(payload=valid_payload("equipements"))

    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), callback=callback)
            first = asyncio.create_task(client.catalog("equipements"))
            await started.wait()
            second = asyncio.create_task(client.catalog("equipements"))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            assert await second == valid_payload("equipements")
            assert sum(len(requests) for requests in mocked.requests.values()) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_warmup_limits_downloads_to_two_concurrent_requests():
    client = XixouClient("test")
    active = 0
    peak = 0

    async def callback(url, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        family = str(url).rsplit("/", 1)[1].removesuffix(".json")
        return CallbackResult(payload=valid_payload(family))

    try:
        with aioresponses() as mocked:
            for family in FAMILIES:
                mocked.get(api_url(family), callback=callback)
            await client.warmup()
        assert peak == 2
        assert set(client._cache) == set(FAMILIES)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_monster_catalog_still_returns_item_information():
    client = XixouClient("test")
    payloads = gelano_catalogs()
    try:
        with aioresponses() as mocked:
            for family in FAMILIES:
                if family == "monstres":
                    mocked.get(api_url(family), status=500)
                elif family == "ressources":
                    mocked.get(api_url(family), payload=valid_payload(family))
                else:
                    mocked.get(api_url(family), payload=payloads[family])
            result = await client.enrich(item())
        assert result.effects == ("+1 PA",)
        assert result.drops[0].rate == "0.07%" and result.drops[0].level_rates == ()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cached_enrichment_reports_stale_catalog_after_failed_refresh():
    client = XixouClient("test", ttl=60, clock=lambda: 100)
    for family, payload in gelano_catalogs().items():
        client._cache[family] = CatalogCache(payload, 0)
        client._retry_at[family] = 200
    try:
        result = await client.enrich(item())
        assert result.stale and result.effects == ("+1 PA",)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_oversized_body_and_declared_content_length_are_rejected(monkeypatch):
    monkeypatch.setattr("utils.xixou_api.MAX_CATALOG_BYTES", 16)
    client = XixouClient("test")
    try:
        with aioresponses() as mocked:
            mocked.get(api_url("equipements"), body=" " * 32, content_type="application/json")
            mocked.get(api_url("ressources"), body="{}", content_type="application/json",
                       headers={"Content-Length": "32"})
            assert await client.catalog("equipements") is None
            assert await client.catalog("ressources") is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_cancels_inflight_requests_and_closes_owned_session():
    client = XixouClient("test")
    started = asyncio.Event()

    async def callback(url, **kwargs):
        started.set()
        await asyncio.Event().wait()

    with aioresponses() as mocked:
        mocked.get(api_url("equipements"), callback=callback)
        task = asyncio.create_task(client.catalog("equipements"))
        await started.wait()
        await client.close()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert client._session.closed
    assert not client.enabled and not client._inflight
    assert await client.catalog("equipements") is None


@pytest.mark.asyncio
async def test_injected_session_stays_owned_by_caller():
    session = AsyncMock()
    client = XixouClient("test", session=session)
    await client.close()
    session.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_family_is_rejected_before_network():
    client = XixouClient("test")
    with pytest.raises(ValueError):
        await client.catalog("../evil")
    assert client._session is None
    await client.close()


@pytest.mark.asyncio
async def test_close_during_enrichment_stops_before_reading_cleared_cache(monkeypatch):
    client = XixouClient("test")

    async def closed_catalog(family):
        await client.close()
        return valid_payload(family)

    monkeypatch.setattr(client, "catalog", closed_catalog)
    assert await client.enrich(item()) is None


def prime_catalogs(client, fetched_at=100):
    for family, payload in gelano_catalogs().items():
        client._cache[family] = CatalogCache(payload, fetched_at)


@pytest.mark.asyncio
async def test_repeated_and_concurrent_enrichment_uses_one_normalization(monkeypatch):
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    calls = []

    def tracked_build(detail, data):
        calls.append(detail.entry.identifier)
        return build_enrichment(detail, data)

    monkeypatch.setattr("utils.xixou_api.build_enrichment", tracked_build)
    try:
        first, second = await asyncio.gather(client.enrich(item()), client.enrich(item()))
        third = await client.enrich(item())
        assert first == second == third
        assert calls == ["2469"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_enrichment_memo_keeps_image_candidates_and_refreshes_them_with_catalog():
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    client._cache["equipements"].value["data"]["anneaux"][0]["image"] = "47.svg"
    try:
        first = await client.enrich(item())
        assert first.image_candidates == (
            "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/47.png",
        )
        assert (await client.enrich(item())).image_candidates == first.image_candidates
        fresh_payload = json.loads(json.dumps(client._cache["equipements"].value))
        fresh_payload["data"]["anneaux"][0]["image"] = "48.svg"
        client._cache["equipements"] = CatalogCache(fresh_payload, 101)
        assert (await client.enrich(item())).image_candidates == (
            "https://xixou.io/wp-content/uploads/xixou-og/xixou-anneaux/48.png",
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_payload_change_invalidates_enrichment_but_revalidation_keeps_cached_value(monkeypatch):
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    calls = []

    def tracked_build(detail, data):
        calls.append(detail.entry.identifier)
        return build_enrichment(detail, data)

    monkeypatch.setattr("utils.xixou_api.build_enrichment", tracked_build)
    try:
        assert (await client.enrich(item())).weight == 5
        cached = client._cache["equipements"]
        client._cache["equipements"] = CatalogCache(cached.value, 101, cached.etag)
        assert (await client.enrich(item())).weight == 5
        assert len(calls) == 1
        new_payload = json.loads(json.dumps(cached.value))
        new_payload["data"]["anneaux"][0]["pods"] = "6"
        client._cache["equipements"] = CatalogCache(new_payload, 101)
        assert (await client.enrich(item())).weight == 6
        assert len(calls) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_enrichment_memo_recalculates_staleness_without_rebuilding(monkeypatch):
    clock = [100]
    client = XixouClient("test", ttl=60, clock=lambda: clock[0])
    prime_catalogs(client)
    calls = []

    def tracked_build(detail, data):
        calls.append(detail.entry.identifier)
        return build_enrichment(detail, data)

    monkeypatch.setattr("utils.xixou_api.build_enrichment", tracked_build)
    try:
        assert not (await client.enrich(item())).stale
        clock[0] = 161
        with aioresponses() as mocked:
            for family in FAMILIES:
                mocked.get(api_url(family), status=500)
            assert (await client.enrich(item())).stale
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_enrichment_memo_includes_detail_level_and_is_bounded(monkeypatch):
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    calls = []

    def tracked_build(detail, data):
        calls.append(detail.data["level"])
        return ItemEnrichment(level=detail.data["level"])

    monkeypatch.setattr("utils.xixou_api.build_enrichment", tracked_build)
    try:
        detail = item()
        assert (await client.enrich(detail)).level == 60
        assert (await client.enrich(replace(detail, data={**detail.data, "level": 61}))).level == 61
        assert calls == [60, 61]
        for number in range(129):
            await client.enrich(item(identifier=number + 3000))
        assert len(client._enrichment_cache) == 128
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_enrichment_waiter_cancellation_does_not_cancel_shared_computation(monkeypatch):
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    started, release = threading.Event(), threading.Event()

    def delayed_build(detail, data):
        started.set()
        release.wait(timeout=5)
        return ItemEnrichment(description="Calcul partagé")

    monkeypatch.setattr("utils.xixou_api.build_enrichment", delayed_build)
    try:
        first = asyncio.create_task(client.enrich(item()))
        await asyncio.to_thread(started.wait, 5)
        second = asyncio.create_task(client.enrich(item()))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second).description == "Calcul partagé"
    finally:
        release.set()
        await client.close()


@pytest.mark.asyncio
async def test_close_waits_for_started_enrichment_without_repopulating_cache(monkeypatch):
    client = XixouClient("test", clock=lambda: 100)
    prime_catalogs(client)
    started, release = threading.Event(), threading.Event()

    def delayed_build(detail, data):
        started.set()
        release.wait(timeout=5)
        return ItemEnrichment(description="Calcul terminé")

    monkeypatch.setattr("utils.xixou_api.build_enrichment", delayed_build)
    task = asyncio.create_task(client.enrich(item()))
    try:
        await asyncio.to_thread(started.wait, 5)
        closing = asyncio.create_task(client.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        assert await task is None
        assert not client._enrichment_cache and not client._enrichment_inflight
    finally:
        release.set()
        await client.close()
