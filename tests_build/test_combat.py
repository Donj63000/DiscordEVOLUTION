"""Scénarios synthétiques : validation algorithmique, pas relevés en jeu."""
import pytest
from pydantic import ValidationError

from utils.build.combat import (AttackLine, CombatScenario, ElementResistance,
                                compare_attacks, critical_probability, freeze_attack,
                                simulate_attack, weapon_attack)
from utils.build.models import Diagnostic, Effect, Metric, Report, STAT_LABELS, WeaponMetadata, revised, values


def report_with(*, unknown=(), **stats):
    return Report(metrics=tuple(Metric(stat=s, value=stats.get(s, 0),
                                       status="unknown" if s in unknown else "known")
                                for s in STAT_LABELS), contributions=(), equipability="valid",
                  completeness="partial" if unknown else "complete", nature="declared",
                  catalog_id="a" * 64, rules_id="b" * 64)


def attack_with(*, lines=None, **kwargs):
    return freeze_attack(id="spell:3", name="Donnée synthétique",
                         normal_lines=lines or (AttackLine(element="fe", minimum=9, maximum=13),),
                         **kwargs)


def test_expected_damage_enumerates_integer_rolls_after_rounding():
    attack = attack_with(critical_denominator=0)
    result = simulate_attack(report_with(ine=51, do=2), attack, CombatScenario(
        resistances=(ElementResistance(element="fe", flat=3, percent=21),)))
    samples = [((b * 151 // 100 + 2 - 3) * 79 // 100) for b in range(9, 14)]
    assert result.normal.damage.average == sum(samples) / 5
    assert result.average_damage == sum(samples) / 5
    assert result.normal.damage.minimum == min(samples)
    assert result.normal.damage.maximum == max(samples)
    assert not result.certified
    assert result.normal.lines[0].trace


def test_each_element_uses_its_resistance_and_attribute():
    attack = attack_with(lines=tuple(AttackLine(element=e, minimum=10, maximum=10)
                                    for e in ("ne", "te", "fe", "ea", "ai")), critical_denominator=0)
    scenario = CombatScenario(resistances=(ElementResistance(element="fe", flat=5),
                                           ElementResistance(element="te", percent=50)))
    result = simulate_attack(report_with(fo=100, ine=200, cha=300, age=400), attack, scenario)
    assert [line.output.minimum for line in result.normal.lines] == [20, 10, 25, 40, 50]
    assert result.normal.damage.minimum == 145


def test_normal_critical_and_failure_probabilities_are_separate():
    attack = attack_with(critical_denominator=10, failure_denominator=100,
                         critical_lines=(AttackLine(element="fe", minimum=20, maximum=20),))
    result = simulate_attack(report_with(cc=8), attack, CombatScenario())
    assert result.critical_probability == 0.5
    assert result.normal.damage.average == 11
    assert result.critical.damage.average == 20
    assert result.average_damage == 15.5
    assert result.average_damage_per_attempt == pytest.approx(15.345)


@pytest.mark.parametrize("agility,bonus,expected", [(0, 0, 1/50), (0, 100, .5), (384, 40, .2), (-50, 0, .02)])
def test_sourced_critical_probability(agility, bonus, expected):
    assert critical_probability(50, agility, bonus) == expected


def test_unknown_critical_never_becomes_zero_chance():
    result = simulate_attack(report_with(), attack_with(), CombatScenario())
    assert result.normal.complete
    assert result.average_damage is None
    assert result.critical_probability is None
    assert not result.complete


def test_unknown_critical_stats_do_not_block_noncritical_attack():
    result = simulate_attack(report_with(unknown=("cc", "age", "pod")),
                             attack_with(critical_denominator=0), CombatScenario())
    assert result.complete
    assert result.average_damage == 11


def test_buff_does_not_repair_unknown_base_characteristic():
    result = simulate_attack(report_with(unknown=("ine",)), attack_with(critical_denominator=0),
                             CombatScenario(buffs=values({"ine": 100})))
    assert result.normal.damage is None
    assert result.normal.lines[0].unknown_dependencies == ("ine",)
    assert result.average_damage is None


def test_unsupported_effect_never_yields_partial_total():
    result = simulate_attack(report_with(), attack_with(critical_denominator=0,
        unsupported_effects=("Dommages dépendants de la vie du lanceur",)), CombatScenario())
    assert result.normal.lines[0].output is not None
    assert result.normal.damage is None
    assert result.average_damage is None


def test_healing_ignores_power_damage_resistances_and_mastery():
    attack = attack_with(lines=(AttackLine(kind="heal", minimum=10, maximum=12),),
                         critical_denominator=0)
    result = simulate_attack(report_with(ine=100, so=3, do=200, pui=400), attack,
                             CombatScenario(mastery_percent=60, target_missing_hp=24))
    assert result.normal.healing.minimum == 23
    assert result.normal.healing.maximum == 24
    assert result.normal.healing.average == pytest.approx(71/3)
    assert result.normal.damage.maximum == 0


def test_life_steal_rounding_and_shared_hp_caps():
    attack = attack_with(lines=(AttackLine(kind="steal", element="ea", minimum=9, maximum=9),
                                AttackLine(kind="steal", element="ea", minimum=9, maximum=9)),
                         critical_denominator=0)
    result = simulate_attack(report_with(so=1000), attack,
                             CombatScenario(target_hp=12, source_missing_hp=100))
    assert result.normal.damage.minimum == 12
    assert result.normal.life_steal.minimum == 5
    capped = simulate_attack(report_with(), attack, CombatScenario(source_missing_hp=3))
    assert capped.normal.life_steal.maximum == 3


def test_pvp_uses_specific_resistances_with_player_cap():
    attack = attack_with(lines=(AttackLine(element="te", minimum=100, maximum=100),),
                         critical_denominator=0)
    resistance = (ElementResistance(element="te", flat=10, percent=40, pvp_flat=10, pvp_percent=40),)
    pvm = simulate_attack(report_with(), attack, CombatScenario(resistances=resistance))
    pvp = simulate_attack(report_with(), attack, CombatScenario(mode="pvp", resistances=resistance))
    assert pvm.average_damage == 54
    assert pvp.average_damage == 40


def test_mastery_and_class_coefficient_only_affect_weapon_damage():
    spell = attack_with(lines=(AttackLine(element="te", minimum=20, maximum=20),),
                        critical_denominator=0)
    weapon = revised(spell, kind="weapon")
    scenario = CombatScenario(mastery_percent=50, weapon_skill_percent=90, buffs=values({"fo": 100}))
    assert simulate_attack(report_with(do=2), spell, scenario).average_damage == 42
    assert simulate_attack(report_with(do=2), weapon, scenario).average_damage == 56


def test_negative_stats_and_damage_cannot_create_negative_output():
    result = simulate_attack(report_with(ine=-100, pui=-200, do=-20),
                             attack_with(critical_denominator=0), CombatScenario())
    assert result.average_damage == 0


def test_comparison_keeps_exact_attack_and_scenario():
    attack = attack_with(critical_denominator=0)
    result = compare_attacks(report_with(), report_with(ine=100), attack, CombatScenario())
    assert result["average_damage_delta"] == 11
    assert result["left"]["attack_revision"] == result["right"]["attack_revision"]
    assert result["left"]["scenario_id"] == result["right"]["scenario_id"]


def test_weapon_uses_archived_damage_lines(item_factory):
    item = item_factory(slot="arme", effects=(Effect(ref="e1", kind="damage", element="fe", low=2, high=4),))
    attack = weapon_attack(item)
    assert attack.kind == "weapon"
    assert attack.normal_lines[0].maximum == 4
    assert attack.critical_denominator is None
    assert attack.critical_lines is None


def test_weapon_metadata_provides_bonus_critical_and_failure(item_factory):
    item = item_factory(slot="arme", effects=(Effect(ref="e1", kind="damage", element="fe", low=2, high=4),),
                        weapon=WeaponMetadata(critical_denominator=30, critical_bonus=5,
                                              failure_denominator=50, ap_cost=4))
    attack = weapon_attack(item)
    assert attack.critical_lines[0].minimum == 7
    assert attack.critical_lines[0].maximum == 9
    assert attack.critical_denominator == 30
    assert attack.failure_denominator == 50


def test_weapon_explicit_critical_effects_take_priority_over_bonus(item_factory):
    item = item_factory(slot="arme", effects=(Effect(ref="e1", kind="damage", element="fe", low=2, high=4),),
        weapon=WeaponMetadata(critical_denominator=30, critical_bonus=5, critical_effects=(
            Effect(ref="c1", kind="damage", element="ea", low=10, high=12),)))
    attack = weapon_attack(item)
    assert attack.critical_lines[0].minimum == 10
    assert attack.critical_lines[0].element == "ea"


def test_weapon_explicit_no_critical_does_not_fabricate_critical_lines(item_factory):
    item = item_factory(slot="arme", effects=(Effect(ref="e1", kind="damage", element="fe", low=2, high=4),),
                        weapon=WeaponMetadata(critical_denominator=0, critical_bonus=0))
    attack = weapon_attack(item)
    assert attack.critical_lines is None
    assert simulate_attack(report_with(), attack, CombatScenario()).complete


@pytest.mark.parametrize("data", [{"resistances": [{"element": "fe"}, {"element": "fe"}]},
                                   {"buffs": [{"stat": "pv", "value": 10}]},
                                   {"critical_probability_override": float("nan")},
                                   {"target_hp": -1}])
def test_invalid_scenario_rejected(data):
    with pytest.raises(ValidationError):
        CombatScenario.model_validate(data)


def test_attack_and_scenario_are_immutable():
    scenario = CombatScenario()
    with pytest.raises(ValidationError):
        scenario.mode = "pvp"
    with pytest.raises(ValidationError):
        AttackLine(element="fe", minimum=10, maximum=3)


def test_invalid_equipment_never_yields_attack_total():
    report = revised(report_with(), equipability="invalid",
                     errors=(Diagnostic(code="LEVEL", text="Niveau trop faible pour l'arme."),))
    result = simulate_attack(report, attack_with(critical_denominator=0), CombatScenario())
    assert not result.complete
    assert result.average_damage is None
    assert result.normal.damage is None
    assert "Build invalide" in result.normal.warnings[0]


def test_unrelated_equipability_uncertainty_is_explicit_but_not_fake_unknown_damage():
    report = revised(report_with(unknown=("pod",)), equipability="unknown")
    result = simulate_attack(report, attack_with(critical_denominator=0), CombatScenario())
    assert result.complete
    assert result.average_damage == 11
    assert any("Équipabilité" in warning for warning in result.warnings)
