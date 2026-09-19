"""Prix exacts et objectif dégâts vérifiés avec le calculateur partagé."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from utils.build.calculator import calculate
from utils.build.catalog import freeze_catalog
from utils.build.combat import AttackLine, CombatScenario, ElementResistance, freeze_attack, simulate_attack, weapon_attack
from utils.build.models import Build, Effect, EffectValue, Instance, SLOTS, WeaponMetadata, equip, revised, values
from utils.build.optimizer import Constraints, Limits, Price, optimize, purchase_cost
from utils.build.prices import jet_signature, latest_quotes, quote_for


@pytest.fixture
def offensive_case(item_factory, build):
    weak = item_factory(80, "coiffe", {"ine": 30})
    strong = item_factory(81, "coiffe", {"ine": 70})
    flat = item_factory(82, "coiffe", {"do": 12})
    catalog = freeze_catalog((weak, strong, flat))
    build = revised(build, catalog_id=catalog.id)
    attack = freeze_attack(id="spell:3", name="Attaque de test", normal_lines=(
        AttackLine(element="fe", minimum=9, maximum=13),), critical_denominator=0)
    constraints = Constraints(objective="damage", attack=attack,
        scenario=CombatScenario(resistances=(ElementResistance(element="fe", flat=3, percent=20),)),
        locked=("cape", "amulette", "ceinture", "bottes", "anneau_1", "anneau_2", "arme"))
    return build, catalog, constraints


def test_damage_score_matches_simulator_for_every_finalist(offensive_case, rules):
    build, catalog, constraints = offensive_case
    result = optimize(build, catalog, rules, constraints, Limits(beam=32, candidates=32))
    assert result["status"] == "FOUND"
    assert not result["global_optimum"]
    for solution in result["solutions"]:
        candidate = Build.model_validate(solution["build"])
        report = calculate(candidate, catalog, rules)
        simulation = simulate_attack(report, constraints.attack, constraints.scenario)
        assert solution["score"] == simulation.average_damage
        assert solution["attack_result"] == simulation.model_dump(mode="json")
        assert solution["constraints_verified"]
    assert Build.model_validate(result["solutions"][0]["build"]).in_slot("coiffe").template_ref == "item:82"


def test_damage_unknown_effect_never_becomes_verified_solution(offensive_case, rules):
    build, catalog, constraints = offensive_case
    constraints = revised(constraints, attack=revised(constraints.attack,
        unsupported_effects=("Effet conditionnel synthétique",)))
    result = optimize(build, catalog, rules, constraints)
    assert result["status"] == "DATA_INCOMPLETE"
    assert not result["solutions"]
    assert result["tentative"]
    assert all(s["score"] is None and not s["constraints_verified"] for s in result["tentative"])


def test_damage_objective_requires_attack():
    with pytest.raises(ValidationError, match="attaque"):
        Constraints(objective="damage")


def test_budget_missing_price_cannot_be_confirmed(offensive_case, rules):
    build, catalog, constraints = offensive_case
    result = optimize(build, catalog, rules, revised(constraints, budget=1000))
    assert not result["solutions"]
    assert all(row["cost"] is None and row["unknown_prices"] for row in result["tentative"])


def test_budget_with_exact_quotes_filters_too_expensive_attack_build(offensive_case, rules):
    build, catalog, constraints = offensive_case
    quotes = tuple(quote_for(equip(template), template, "Serveur test", 500 if template.ref == "item:82" else 100)
                   for template in catalog.items)
    result = optimize(build, catalog, rules, revised(constraints, budget=100, quotes=quotes,
                                                    price_context="Serveur test"))
    assert result["solutions"]
    assert all(row["cost"] <= 100 for row in result["solutions"])
    assert all(Build.model_validate(row["build"]).in_slot("coiffe").template_ref != "item:82"
               for row in result["solutions"])


def test_jet_signature_ignores_instance_identity_and_mode_when_values_identical(item_factory):
    template = item_factory(stats={"fo": 30})
    first, second = equip(template), equip(template)
    custom = Instance(template_ref=template.ref, template_revision=template.revision,
                      mode="natural_custom", final_values=(EffectValue(ref="e0", value=30),))
    assert first.id != second.id
    assert jet_signature(first, template) == jet_signature(second, template) == jet_signature(custom, template)
    changed = revised(custom, final_values=(EffectValue(ref="e0", value=29),))
    assert jet_signature(changed, template) != jet_signature(first, template)


def test_owned_ring_is_consumed_once_when_candidate_uses_two(item_factory, build):
    ring = item_factory(71, "anneau_1", {"fo": 20})
    catalog = freeze_catalog((ring,))
    original = revised(build, catalog_id=catalog.id).with_slot("anneau_1", equip(ring))
    candidate = original.with_slot("anneau_2", equip(ring))
    quote = quote_for(equip(ring), ring, "Test", 250)
    constraints = Constraints(owned_slots=("anneau_1",), quotes=(quote,), price_context="Test")
    assert purchase_cost(original, candidate, catalog, constraints) == (250, [])
    assert purchase_cost(candidate, candidate, catalog, revised(constraints,
        owned_slots=("anneau_1", "anneau_2"))) == (0, [])


def test_owned_equipment_can_move_slots_without_being_charged_again(item_factory, build):
    ring = item_factory(71, "anneau_1", {"fo": 20})
    catalog = freeze_catalog((ring,))
    original = revised(build, catalog_id=catalog.id).with_slot("anneau_1", equip(ring))
    candidate = revised(build, catalog_id=catalog.id).with_slot("anneau_2", equip(ring))
    assert purchase_cost(original, candidate, catalog, Constraints(owned_slots=("anneau_1",))) == (0, [])


def test_owned_custom_jets_do_not_cover_natural_best(item_factory, build):
    ring = item_factory(71, "anneau_1", {"fo": 20})
    catalog = freeze_catalog((ring,))
    custom = Instance(template_ref=ring.ref, template_revision=ring.revision,
                      mode="declared_fm", final_values=(EffectValue(ref="e0", value=21),))
    original = revised(build, catalog_id=catalog.id).with_slot("anneau_1", custom)
    candidate = original.with_slot("anneau_1", equip(ring))
    cost, missing = purchase_cost(original, candidate, catalog, Constraints(owned_slots=("anneau_1",)))
    assert cost is None and missing == ["anneau_1:item:71"]


def test_mismatching_quote_jets_never_resolve_cost(item_factory, build):
    template = item_factory(stats={"fo": 30})
    catalog = freeze_catalog((template,))
    custom = Instance(template_ref=template.ref, template_revision=template.revision,
                      mode="declared_fm", final_values=(EffectValue(ref="e0", value=31),))
    quote = quote_for(custom, template, "Test", 10)
    candidate = revised(build, catalog_id=catalog.id).with_slot("coiffe", equip(template))
    cost, missing = purchase_cost(build, candidate, catalog, Constraints(quotes=(quote,), price_context="Test"))
    assert cost is None and missing


def test_legacy_price_does_not_apply_to_declared_fm(item_factory, build):
    template = item_factory(stats={"fo": 30})
    catalog = freeze_catalog((template,))
    custom = Instance(template_ref=template.ref, template_revision=template.revision,
                      mode="declared_fm", final_values=(EffectValue(ref="e0", value=31),))
    candidate = revised(build, catalog_id=catalog.id).with_slot("coiffe", custom)
    constraints = Constraints(prices=(Price(reference=template.ref, kamas=1),), price_context="Test")
    assert purchase_cost(build, candidate, catalog, constraints)[0] is None


def test_quotes_are_server_specific_and_latest_uses_timezone(item_factory):
    template = item_factory()
    first = quote_for(equip(template), template, "Test", 100, "2020-01-01T12:00:00+02:00")
    newer = quote_for(equip(template), template, "Test", 200, "2020-01-01T11:00:00+00:00")
    other = quote_for(equip(template), template, "Other", 1, "2020-01-02T00:00:00+00:00")
    assert latest_quotes([q.model_dump(mode="json") for q in (first, newer, other)], "test") == (newer,)
    with pytest.raises(ValidationError, match="serveur"):
        Constraints(quotes=(first, other), price_context="Test")


def test_price_date_requires_past_timezone(item_factory):
    template = item_factory()
    with pytest.raises(ValidationError):
        quote_for(equip(template), template, "Test", 100, "2020-01-01")
    future = str(datetime.now(timezone.utc).year + 10) + "-01-01T00:00:00+00:00"
    with pytest.raises(ValidationError):
        quote_for(equip(template), template, "Test", 100, future)


def test_locked_custom_weapon_uses_final_jets_and_real_critical_metadata(item_factory, build, rules):
    weapon = item_factory(71, "arme", effects=(
        Effect(ref="attack", kind="damage", element="te", low=10, high=10),
        Effect(ref="force", kind="stat", stat="fo", low=20, high=30)),
        weapon=WeaponMetadata(critical_denominator=50, critical_bonus=5, failure_denominator=50))
    catalog = freeze_catalog((weapon,))
    custom = Instance(template_ref=weapon.ref, template_revision=weapon.revision,
                      mode="declared_fm", final_values=(EffectValue(ref="force", value=40),),
                      extras=values({"cc": 48}))
    current = revised(build, catalog_id=catalog.id).with_slot("arme", custom)
    constraints = Constraints(objective="damage", attack=weapon_attack(weapon), locked=SLOTS,
                              allow_exos=True)
    result = optimize(current, catalog, rules, constraints)
    assert result["status"] == "DATA_INCOMPLETE"
    assert not result["solutions"]
    solution = result["tentative"][0]
    assert not solution["constraints_verified"]
    assert solution["attack_result"]["critical_probability"] == .5
    assert solution["score"] == 17.5
    assert solution["attack_result"]["normal"]["damage"]["average"] == 14
    assert solution["attack_result"]["critical"]["damage"]["average"] == 21
