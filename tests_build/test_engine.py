"""Invariants mathématiques et de couverture ; fixtures exclusivement synthétiques."""
import json
import pytest
from pydantic import ValidationError
from utils.build.models import *
from utils.build.effects import parse_lines, resolve_values
from utils.build.rules import load_rules
from utils.build.calculator import calculate
from utils.build.catalog import freeze_catalog, verify_snapshot
from utils.build.conditions import parse, evaluate, Truth
from utils.build.comparison import compare


@pytest.mark.parametrize("line,stat,low,high", [
    ("+1 PA", "pa", 1, 1), ("+10 à 20 en force", "fo", 10, 20),
    ("-40 à -20 en force", "fo", -40, -20), ("+5 de dommages", "do", 5, 5),
    ("+20 en prospection", "pp", 20, 20), ("Augmente les dommages de 10 à 20 %", "pui", 10, 20),
])
def test_effect_lines(line, stat, low, high):
    effect, = parse_lines((line,))
    assert (effect.kind, effect.stat, effect.low, effect.high) == ("stat", stat, low, high)


def test_weapon_damage_is_not_character_bonus():
    effect, = parse_lines(("4 à 7 (feu)",))
    assert effect.kind == "damage" and effect.element == "fe"


def test_unknown_kept_not_zero():
    effect, = parse_lines(("Double une mécanique non implémentée",))
    assert effect.kind == "unknown" and "Double" in effect.text


def test_malus_uses_best_signed_value(item_factory):
    item = item_factory(effects=parse_lines(("-40 à -20 en force",)))
    contributions, unknown = resolve_values(item, equip(item), "coiffe")
    assert sum(c.value for c in contributions) == -20
    assert not unknown


def test_jets_replace_not_add_twice(item_factory):
    item = item_factory(stats={"pa": 1, "fo": 20})
    instance = Instance(template_ref=item.ref, template_revision=item.revision, mode="declared_fm",
                        final_values=(EffectValue(ref="e0", value=1), EffectValue(ref="e1", value=35)),
                        extras=values({"pm": 1}))
    rows, _ = resolve_values(item, instance, "coiffe")
    totals = {r.stat:r.value for r in rows}
    assert totals == {"pa":1, "fo":35, "pm":1}
    with pytest.raises(BuildError):
        resolve_values(item, revised(instance, extras=values({"pa":1})), "coiffe")


def test_custom_jets_are_complete_and_within_bounds(item_factory):
    item = item_factory(stats={"fo":20})
    base = equip(item)
    with pytest.raises(BuildError):
        resolve_values(item, revised(base, mode="natural_custom"), "coiffe")
    with pytest.raises(BuildError):
        resolve_values(item, revised(base, mode="natural_custom", final_values=[{"ref":"e0","value":21}]), "coiffe")
    with pytest.raises(BuildError):
        resolve_values(item, revised(base, mode="natural_custom", final_values=[{"ref":"bogus","value":20}]), "coiffe")


@pytest.mark.parametrize("value", [True, 2.5, "10", -1, 201])
def test_level_is_strict(value):
    with pytest.raises(ValidationError):
        Profile(classe="enutrof", level=value)


def test_profile_cannot_double_count_parcho(profile):
    with pytest.raises(ValidationError):
        revised(profile, scrolled=values({"cha":101}))
    with pytest.raises(ValidationError):
        Profile(classe="iop", level=2, allocated=values({"fo":6}))
    with pytest.raises(ValidationError):
        Profile(classe="iop", level=200, scrolled=values({"fo":102}))


def test_all_class_softcaps_present():
    rules = load_rules()
    assert len(rules.allocations) == 72
    assert not rules.fixtures
    assert not rules.base_verified and not rules.allocation_verified
    assert rules.id == digest(rules.model_dump(mode="json", exclude={"id"}))


@pytest.mark.parametrize("classe", CLASSES)
def test_all_classes_have_six_primary_rules(classe):
    rules = load_rules()
    assert {r.stat for r in rules.allocations if r.classe == classe} == set(PRIMARY)
    assert rules.allocated_value(classe, "vi", 0, 101) == 101


def test_points_spent_are_not_flat_stats(rules):
    assert rules.allocated_value("iop", "fo", 100, 0) == 100
    assert rules.allocated_value("iop", "fo", 102, 0) == 101
    assert rules.allocated_value("sacrieur", "vi", 10, 0) == 20
    with pytest.raises(BuildError):
        rules.allocated_value("iop", "fo", 1, 100)


@pytest.mark.parametrize("expression,ctx,expected", [
    ("Force > 50", {"fo":50}, Truth.FALSE),
    ("Force >= 50", {"fo":50}, Truth.TRUE),
    ("Force > 50 et Intelligence > 10", {"fo":0}, Truth.FALSE),
    ("Force > 50 et Intelligence > 10", {"fo":100}, Truth.UNKNOWN),
    ("Force > 50 ou Intelligence > 10", {"fo":100}, Truth.TRUE),
    ("(CS>50|CI>20)&PL>100", {"fo":100, "level":200}, Truth.TRUE),
    ("Grade > 4", {"grade":None}, Truth.UNKNOWN),
    ("__import__('os').system('danger')", {}, Truth.UNKNOWN),
    ("Force > 0; print(1)", {"fo":10}, Truth.UNKNOWN),
    ("("*30 + "CS>0" + ")"*30, {"fo":10}, Truth.UNKNOWN),
    ("X"*800, {}, Truth.UNKNOWN),
])
def test_conditions(expression, ctx, expected):
    assert evaluate(parse(expression), ctx) == expected


def test_report_reproducible_and_contributions(build, catalog, rules):
    equipped = build.with_slot("coiffe", equip(catalog.items[0]))
    a = calculate(equipped, catalog, rules)
    assert a == calculate(equipped, catalog, rules)
    assert a.totals["fo"] == 30
    assert a.equipability == "valid"
    assert sum(x.value for x in a.contributions if x.stat == "fo") == 30
    assert calculate(equipped.with_slot("coiffe",None),catalog,rules) == calculate(build,catalog,rules)


def test_order_independent(build, catalog, rules):
    equipped = build.with_slot("coiffe",equip(catalog.items[0])).with_slot("cape",equip(catalog.items[1]))
    assert calculate(equipped,catalog,rules) == calculate(revised(equipped,slots=tuple(reversed(equipped.slots))),catalog,rules)


def test_set_total_not_cumulative(build, rules, item_factory):
    items = [item_factory(i,slot,set_ref="s") for i,slot in enumerate(("coiffe","cape","amulette"),1)]
    panoplie = SetDefinition(ref="s",name="Pano synthétique",source="fixture://synthetic",tiers=(
        SetTier(pieces=1,effects=()), SetTier(pieces=2,effects=(Effect(ref="two",kind="stat",stat="fo",low=20,high=20),)),
        SetTier(pieces=3,effects=(Effect(ref="three",kind="stat",stat="fo",low=40,high=40),))))
    cat = freeze_catalog(items,[panoplie]); b = revised(build,catalog_id=cat.id)
    for item in items:
        b = b.with_slot(item.allowed_slots[0],equip(item))
    assert calculate(b,cat,rules).totals["fo"] == 40
    assert calculate(b.with_slot("amulette",None),cat,rules).totals["fo"] == 20


def test_repeated_set_ring_counts_once(build, rules, item_factory):
    item = item_factory(1,"anneau_1",set_ref="s")
    pano = SetDefinition(ref="s",name="Fixture",source="fixture://synthetic",tiers=(SetTier(pieces=1,effects=()),))
    cat = freeze_catalog([item],[pano]); b = revised(build,catalog_id=cat.id)
    b = b.with_slot("anneau_1",equip(item)).with_slot("anneau_2",equip(item))
    assert not any(w.code == "SET_MISSING" for w in calculate(b,cat,rules).warnings)


def test_missing_set_and_unknown_effect_mark_partial(build,rules,item_factory):
    for item in [item_factory(set_ref="unknown"), item_factory(effects=parse_lines(("Effet inédit",)))]:
        cat = freeze_catalog([item]); b = revised(build,catalog_id=cat.id).with_slot("coiffe",equip(item))
        report = calculate(b,cat,rules)
        assert report.completeness == "partial" and not report.known("pa")
        assert report.equipability == "unknown"


def test_level_invalid_kept_as_draft(build, catalog, rules):
    b = revised(build,profile=Profile(classe="iop",level=1)).with_slot("coiffe",equip(catalog.items[0]))
    # Objet niveau 1 : pas d'erreur de niveau, les inconnues restent distinctes.
    assert not any(e.code == "LEVEL" for e in calculate(b,catalog,rules).errors)


def test_wrong_slot_rejected(build,catalog,rules):
    with pytest.raises(BuildError):
        calculate(build.with_slot("cape",equip(catalog.items[0])),catalog,rules)


def test_unique_restriction(build,rules,item_factory):
    item=item_factory(1,"anneau_1",unique=True); cat=freeze_catalog([item])
    b=revised(build,catalog_id=cat.id).with_slot("anneau_1",equip(item)).with_slot("anneau_2",equip(item))
    assert calculate(b,cat,rules).equipability == "invalid"


def test_weapon_shield_restriction(build,rules,item_factory):
    weapon=item_factory(1,"arme",two_handed=True); shield=item_factory(2,"bouclier")
    cat=freeze_catalog([weapon,shield]); b=revised(build,catalog_id=cat.id)
    b=b.with_slot("arme",equip(weapon)).with_slot("bouclier",equip(shield))
    assert calculate(b,cat,rules).equipability == "invalid"


def test_beta_rules_never_claim_exact_game_profile(build,catalog):
    rules=load_rules(); b=revised(build,rules_id=rules.id,profile=Profile(classe="enutrof",level=200))
    report=calculate(b,catalog,rules)
    assert report.totals["pa"] == 7 and report.totals["pm"] == 3
    assert not report.known("pa") and not report.known("pp")
    assert report.equipability == "unknown"


def test_declared_profile_derived_stats_use_delta(build,rules,item_factory):
    stats={s:0 for s in STAT_LABELS}; stats.update(pa=7,pm=3,pp=130,cha=100,pv=1000,vi=200)
    profile=Profile(classe="enutrof",level=200,mode="declared",naked_stats=values(stats))
    item=item_factory(stats={"cha":20,"vi":50});cat=freeze_catalog([item])
    b=revised(build,profile=profile,catalog_id=cat.id).with_slot("coiffe",equip(item))
    report=calculate(b,cat,rules)
    assert report.totals["pp"] == 132 and report.totals["pv"] == 1050


def test_catalog_tamper_detected(catalog):
    verify_snapshot(catalog)
    with pytest.raises(BuildError):
        verify_snapshot(revised(catalog,generated_at="falsifié"))
    with pytest.raises(TypeError):
        catalog.by_ref["new"] = catalog.items[0]


def test_comparison_ignores_instance_uuid(build,catalog,rules):
    first=build.with_slot("coiffe",equip(catalog.items[0])); second=build.with_slot("coiffe",equip(catalog.items[0]))
    assert first.in_slot("coiffe").id != second.in_slot("coiffe").id
    result=compare(first,calculate(first,catalog,rules),second,calculate(second,catalog,rules))
    assert result["emplacements_changes"] == [] and result["ecarts"] == []
