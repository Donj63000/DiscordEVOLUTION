"""Je protège les formats observés et distingue exemples synthétiques et recette réelle."""
import json
from types import SimpleNamespace

import pytest

from utils.build.calculator import calculate
from utils.build.catalog import freeze_catalog, normalize, verify_snapshot
from utils.build.conditions import Truth, evaluate, parse, recognized
from utils.build.diagnostics import catalog_audit
from utils.build.effects import parse_lines
from utils.build.models import (Catalog, Effect, ItemTemplate, Profile, WeaponMetadata, canonical,
                                digest, equip, revised, values)
from utils.build.reference_cases import ReferenceCase, check_reference_cases, load_reference_cases
from utils.build.rules import Rules, load_rules


def source_row(name="Objet de référence", **changes):
    return {"name": name, "level": "1", "effets": [], "conditions": [], "panoplie": None, **changes}


def normalized(category, *rows):
    return normalize([], {"famille": "equipements", "data": {category: list(rows)}})


@pytest.mark.parametrize("category,slot", [("familiers", "compagnon"), ("dragodindes", "compagnon"),
                                         ("dofus", "dofus_1"), ("boucliers", "bouclier"),
                                         ("pierre-d-ame", "arme"), ("filet-de-capture", "arme")])
def test_api_only_equipment_has_stable_identity(category, slot):
    catalog = normalized(category, source_row())
    item = catalog.items[0]
    assert item.ref.startswith("xixou:") and slot in item.allowed_slots
    assert item.ref == normalized(category, source_row(effets=["+1 PA"])).items[0].ref
    assert item.revision != normalized(category, source_row(effets=["+1 PA"])).items[0].revision
    verify_snapshot(Catalog.model_validate_json(canonical(catalog)))


def test_full_catalog_audit_keeps_rejection_and_original_unknown_text():
    payload = {"famille": "equipements", "data": {"boucliers": [
        source_row(effets=["Mécanique jamais observée"], conditions=["Abonné = 1"], panoplie="Panoplie inconnue"),
        source_row("Identité douteuse", id=999), source_row("Saisonnier", effets=["Objet saisonnier"])]}}
    catalog = normalize([], payload)
    audit = catalog_audit(catalog)
    codes = {issue["code"] for issue in audit["issues"]}
    assert {"EFFECT_UNKNOWN", "CONDITION_UNKNOWN", "SET_MISSING", "IDENTITY_AMBIGUOUS", "SEASONAL_ITEM"} <= codes
    assert len(catalog.items) == 1
    assert all(len(issue["source_hash"]) == 64 and issue["source"] for issue in audit["issues"])
    assert catalog.items[0].effects[0].text == "Mécanique jamais observée"


def test_weapon_metadata_from_observed_explicit_source_lines():
    item = normalized("epees", source_row(arme_stats=["PA : 4", "Portée : 1 à 1",
                      "Bonus coup critiques : +5", "Critique : 1/40 - Échec : 1/50"])).items[0]
    assert (item.weapon.ap_cost, item.weapon.critical_bonus, item.weapon.critical_denominator) == (4, 5, 40)
    assert item.weapon.failure_denominator == 50
    assert normalized("epees", source_row()).items[0].weapon is None
    assert normalized("epees", source_row(arme_stats=["PA : 4"])).items[0].weapon.critical_denominator is None


def test_legacy_item_catalog_and_rule_serialization_stays_identical(item_factory):
    item = item_factory()
    item_body = item.model_dump(mode="json")
    assert "weapon" not in item_body
    restored = ItemTemplate.model_validate(item_body)
    assert canonical(restored) == canonical(item_body)
    catalog = freeze_catalog([item])
    assert "audit" not in catalog.model_dump(mode="json")
    verify_snapshot(Catalog.model_validate_json(canonical(catalog)))
    body = load_rules().model_dump(mode="json")
    body.pop("evidence")
    body.pop("id")
    legacy = {"id": digest(body), **body}
    assert canonical(Rules.model_validate(legacy)) == canonical(legacy)


@pytest.mark.parametrize("line", ["Lié au compte", "Résistance : 10 / 10",
                                  "Vaincre 40x Mufafah (+1 en intelligence)", "Échangeable dès le : 600"])
def test_noncombat_item_metadata_is_preserved_without_stat_poisoning(line):
    effect, = parse_lines([line])
    assert effect.kind == "cosmetic" and effect.text == line


def test_pet_bonus_and_healing_are_distinct_from_feed_instructions():
    bonus, heal = parse_lines(["+0 à 80 en intelligence (Capacités accrues : 90)", "PDV rendus : 2 à 3"])
    assert (bonus.kind, bonus.stat, bonus.high) == ("stat", "ine", 80)
    assert "90" in bonus.text
    assert (heal.kind, heal.low, heal.high) == ("heal", 2, 3)


@pytest.mark.parametrize("condition,context,expected", [
    ("Classe = Enutrof", {"class_id": 3}, Truth.TRUE),
    ("Classe non Sadida", {"class_id": 10}, Truth.FALSE),
    ("Vitalité > 9 Intelligence > 6", {"vi": 10, "ine": 6}, Truth.FALSE),
    ("BI=non équipable", {}, Truth.FALSE),
    ("Alignement = Mercenaire", {"alignment": 3}, Truth.TRUE),
])
def test_observed_condition_formats(condition, context, expected):
    node = parse(condition)
    assert recognized(node) and evaluate(node, context) is expected


def test_unknown_pvp_resistance_does_not_poison_force(build, rules, item_factory):
    item = item_factory(effects=parse_lines(["+5 % résistance feu contre les joueurs"]))
    catalog = freeze_catalog([item])
    report = calculate(revised(build, catalog_id=catalog.id).with_slot("coiffe", equip(item)), catalog, rules)
    assert report.known("fo") and not report.known("rp_fe")


@pytest.mark.parametrize("classe,stat,spent,scroll,expected", [
    ("iop", "fo", 100, 0, 100), ("iop", "fo", 102, 0, 101),
    ("iop", "fo", 2, 101, 102), ("sacrieur", "vi", 5, 101, 111),
    ("pandawa", "cha", 50, 0, 50), ("pandawa", "cha", 52, 0, 51),
])
def test_documented_allocation_boundaries(classe, stat, spent, scroll, expected):
    assert load_rules().allocated_value(classe, stat, spent, scroll) == expected


def test_naked_documented_profile_has_usable_combat_stats(build, catalog):
    rules = load_rules()
    character = revised(build, rules_id=rules.id, profile=Profile(classe="enutrof", level=100))
    report = calculate(character, catalog, rules)
    assert report.totals["pv"] == 545 and report.totals["pp"] == 120
    assert all(report.known(stat) for stat in ("pa", "pm", "pp", "pv", "fo", "ine", "cha", "age"))
    assert not report.known("ini") and not report.known("pod")
    assert all(evidence.status == "documented" for evidence in rules.evidence)


def test_reference_import_requires_observation_and_never_promotes_synthetic(build, catalog, rules):
    data = dict(name="Niveau nu synthétique", origin="synthetic", game_version="synthetic-only",
                build=build.model_dump(mode="json"), expected=[{"stat": "pa", "value": 7}])
    cases = load_reference_cases(json.dumps({"schema_version": 1, "cases": [data]}).encode())
    outcome, = check_reference_cases(cases, catalog, rules)
    assert outcome["matches"] and not outcome["in_game_evidence_supplied"]
    with pytest.raises(ValueError):
        ReferenceCase.model_validate({**data, "origin": "in_game"})
