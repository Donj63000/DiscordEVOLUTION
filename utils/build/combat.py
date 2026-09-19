"""Simulation pure d'une attaque, avec hypothèses et inconnues conservées."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .damage import ELEMENT_STAT
from .models import BuildError, Frozen, Hash, Report, StatValue, as_stats, digest

Element = Literal["ne", "te", "fe", "ea", "ai"]
Amount = Annotated[StrictInt, Field(ge=0, le=10000)]
Health = Annotated[StrictInt, Field(ge=0, le=10000000)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
FORMULA_VERSION = "retro-single-attack-estimate-v1"
CRITICAL_SOURCE = "https://xixou.io/wp-content/plugins/xixou-builder/assets/js/xb-builder-v2.js"


class AttackLine(Frozen):
    kind: Literal["damage", "steal", "heal"] = "damage"
    element: Element | None = None
    minimum: Amount
    maximum: Amount
    text: Annotated[str, Field(max_length=600)] = ""

    @model_validator(mode="after")
    def coherent(self):
        if self.minimum > self.maximum:
            raise ValueError("Intervalle d'attaque inversé.")
        if self.kind != "heal" and self.element is None:
            raise ValueError("Élément requis pour les dégâts.")
        return self


class AttackDefinition(Frozen):
    schema_version: Literal[1] = 1
    id: Annotated[str, Field(min_length=1, max_length=180)]
    name: Annotated[str, Field(min_length=1, max_length=180)]
    kind: Literal["spell", "weapon"] = "spell"
    level: Annotated[StrictInt, Field(ge=1, le=6)] = 1
    required_level: Annotated[StrictInt, Field(ge=1, le=200)] | None = None
    classe: Annotated[str, Field(max_length=30)] | None = None
    normal_lines: Annotated[tuple[AttackLine, ...], Field(max_length=16)] = ()
    critical_lines: Annotated[tuple[AttackLine, ...], Field(max_length=16)] | None = None
    critical_denominator: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None
    failure_denominator: Annotated[StrictInt, Field(ge=0, le=10000)] | None = None
    ap_cost: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    unsupported_effects: Annotated[tuple[str, ...], Field(max_length=100)] = ()
    critical_unsupported_effects: Annotated[tuple[str, ...], Field(max_length=100)] = ()
    conditions: Annotated[tuple[str, ...], Field(max_length=16)] = ()
    source: Annotated[str, Field(max_length=400)] = "https://xixou.io/les-outils/api/"
    revision: Hash = "0" * 64

    @model_validator(mode="after")
    def denominators(self):
        if self.critical_denominator == 1 or self.failure_denominator == 1:
            raise ValueError("Un dénominateur doit être nul ou au moins égal à deux.")
        if self.critical_denominator == 0 and self.critical_lines:
            raise ValueError("Des effets critiques contredisent l'absence de critique.")
        return self


def freeze_attack(**kwargs):
    """Je calcule l'empreinte du contrat complet, valeurs par défaut comprises."""
    attack = AttackDefinition(**kwargs)
    body = attack.model_dump(mode="json")
    body.pop("revision")
    return AttackDefinition(revision=digest(body), **body)


class ElementResistance(Frozen):
    element: Element
    flat: Amount = 0
    percent: Annotated[StrictInt, Field(ge=-100, le=100)] = 0
    pvp_flat: Amount = 0
    pvp_percent: Annotated[StrictInt, Field(ge=-100, le=100)] = 0


class CombatScenario(Frozen):
    schema_version: Literal[1] = 1
    mode: Literal["pvm", "pvp"] = "pvm"
    resistances: Annotated[tuple[ElementResistance, ...], Field(max_length=5)] = ()
    buffs: Annotated[tuple[StatValue, ...], Field(max_length=8)] = ()
    mastery_percent: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
    weapon_skill_percent: Annotated[StrictInt, Field(ge=0, le=200)] = 100
    critical_probability_override: Probability | None = None
    target_hp: Health | None = None
    source_missing_hp: Health | None = None
    target_missing_hp: Health | None = None

    @model_validator(mode="after")
    def coherent(self):
        if len({r.element for r in self.resistances}) != len(self.resistances):
            raise ValueError("Résistance élémentaire en double.")
        if set(as_stats(self.buffs)) - {"fo", "ine", "cha", "age", "pui", "do", "so", "cc"}:
            raise ValueError("Buff offensif non pris en charge.")
        return self


class RangeResult(Frozen):
    minimum: int
    maximum: int
    average: float


class CombatLineResult(Frozen):
    kind: Literal["damage", "steal", "heal"]
    element: Element | None
    output: RangeResult | None = None
    life_steal: RangeResult | None = None
    unknown_dependencies: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()


class CombatBranch(Frozen):
    lines: tuple[CombatLineResult, ...] = ()
    damage: RangeResult | None = None
    healing: RangeResult | None = None
    life_steal: RangeResult | None = None
    complete: bool = False
    warnings: tuple[str, ...] = ()


class CombatResult(Frozen):
    schema_version: Literal[1] = 1
    attack_id: str
    attack_revision: Hash
    scenario_id: Hash
    formula_version: str = FORMULA_VERSION
    validation: Literal["experimental-unverified-game"] = "experimental-unverified-game"
    certified: Literal[False] = False
    normal: CombatBranch
    critical: CombatBranch | None = None
    critical_probability: Probability | None = None
    failure_probability: Probability | None = None
    average_damage: float | None = None
    average_healing: float | None = None
    average_life_steal: float | None = None
    average_damage_per_attempt: float | None = None
    complete: bool = False
    warnings: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()


def critical_probability(base, agility, bonus):
    """Je reproduis le calcul public Xixou, sans le déclarer vérifié en jeu."""
    if base == 0:
        return 0.0
    after_bonus = max(2, base - bonus)
    denominator = after_bonus
    if agility >= 1 and after_bonus > 2:
        denominator = min(after_bonus, math.floor(after_bonus * math.exp(1.1) / math.log(agility + 12)))
    return 1 / max(2, denominator)


def _range(samples):
    return RangeResult(minimum=min(samples), maximum=max(samples), average=sum(samples) / len(samples))


def _totals(parts):
    return RangeResult(minimum=sum(p.minimum for p in parts), maximum=sum(p.maximum for p in parts),
                       average=sum(p.average for p in parts))


def _branch(report, attack, lines, unsupported, scenario):
    stats = report.totals
    for stat, value in as_stats(scenario.buffs).items():
        stats[stat] = stats.get(stat, 0) + value
    results, samples, warnings = [], [], list(unsupported)
    for line in lines:
        required = ("ine", "so") if line.kind == "heal" else (ELEMENT_STAT[line.element], "pui", "do")
        missing = tuple(s for s in required if not report.known(s))
        if missing:
            results.append(CombatLineResult(kind=line.kind, element=line.element,
                                           unknown_dependencies=missing))
            warnings.append("Caractéristiques inconnues : " + ", ".join(missing))
            continue
        resist = next((r for r in scenario.resistances if r.element == line.element), None)
        flat = resist.flat if resist else 0
        percent = resist.percent if resist else 0
        if scenario.mode == "pvp":
            flat += resist.pvp_flat if resist else 0
            percent = min(50, percent + (resist.pvp_percent if resist else 0))
        percent = max(-100, min(100, percent))
        trace = []
        if line.kind == "heal":
            def output(base):
                return max(0, base * (100 + max(0, stats["ine"])) // 100 + stats["so"])
            trace.append("Soin : plancher(base × (100 + intelligence positive) / 100) + soins.")
        else:
            power = max(0, stats[ELEMENT_STAT[line.element]]) + stats["pui"]
            mastery = 100 + scenario.mastery_percent if attack.kind == "weapon" else 100
            skill = scenario.weapon_skill_percent if attack.kind == "weapon" else 100

            def output(base):
                value = base * max(0, 100 + power) * mastery * skill // 1000000 + stats["do"]
                return max(0, max(0, value - flat) * (100 - percent) // 100)
            trace.append("Dégâts : plancher(base × (100 + caractéristique positive + puissance)"
                         " × (100 + maîtrise) × coefficient arme / 1000000) + dommages.")
            trace.append(f"Résistances : fixe {flat}, puis {percent} %, plancher après réduction.")
        rolled = tuple(output(base) for base in range(line.minimum, line.maximum + 1))
        steal = tuple(v // 2 for v in rolled) if line.kind == "steal" else ()
        if steal:
            trace.append("Vol de vie : plancher(dégâts / 2), sans bonus soins.")
        results.append(CombatLineResult(kind=line.kind, element=line.element, output=_range(rolled),
                                       life_steal=_range(steal) if steal else None, trace=tuple(trace)))
        samples.append((line.kind, rolled))
    if not lines:
        warnings.append("Aucune ligne d'attaque directement calculable.")
    if warnings:
        return CombatBranch(lines=tuple(results), warnings=tuple(warnings))
    totals = [_totals([r.output for r in results if r.kind != "heal"]),
              _totals([r.output for r in results if r.kind == "heal"]),
              _totals([r.life_steal for r in results if r.life_steal is not None])]
    if any(v is not None for v in (scenario.target_hp, scenario.source_missing_hp, scenario.target_missing_hp)):
        states = {(0, 0, 0): 1.0}
        transitions = 0
        for kind, rolls in samples:
            frequencies = defaultdict(int)
            for roll in rolls:
                frequencies[roll] += 1
            next_states = defaultdict(float)
            for (damage, healing, stolen), probability in states.items():
                transitions += len(frequencies)
                if transitions > 2000000:
                    return CombatBranch(lines=tuple(results), warnings=("Distribution avec plafonds trop complexe.",))
                for amount, count in frequencies.items():
                    d, h, s = damage, healing, stolen
                    if kind == "heal":
                        h += amount
                        if scenario.target_missing_hp is not None:
                            h = min(h, scenario.target_missing_hp + d)
                    else:
                        actual = amount
                        if scenario.target_hp is not None:
                            actual = min(actual, max(0, scenario.target_hp + h - d))
                        d += actual
                        if kind == "steal":
                            s += actual // 2
                            if scenario.source_missing_hp is not None:
                                s = min(s, scenario.source_missing_hp)
                    next_states[(d, h, s)] += probability * count / len(rolls)
            if len(next_states) > 20000:
                return CombatBranch(lines=tuple(results), warnings=("Distribution avec plafonds trop complexe.",))
            states = next_states
        totals = [RangeResult(minimum=min(s[index] for s in states),
                              maximum=max(s[index] for s in states),
                              average=sum(s[index] * p for s, p in states.items())) for index in range(3)]
    return CombatBranch(lines=tuple(results), damage=totals[0], healing=totals[1],
                        life_steal=totals[2], complete=True)


def simulate_attack(report: Report, attack: AttackDefinition, scenario: CombatScenario) -> CombatResult:
    """Je sépare les branches, leur fréquence et les plafonds d'une seule attaque."""
    blocking = tuple("Build invalide : " + error.text for error in report.errors)
    normal = _branch(report, attack, attack.normal_lines, attack.unsupported_effects + blocking, scenario)
    critical = None
    if attack.critical_lines is not None:
        critical = _branch(report, attack, attack.critical_lines,
                           attack.critical_unsupported_effects + blocking, scenario)
    warnings = ["Estimation expérimentale : ordre, maîtrise, résistances et arrondis non validés en jeu.",
                "Espérance sur jets entiers équiprobables, conditionnée à une attaque réussie."]
    if attack.conditions:
        warnings.append("Conditions de lancement à vérifier : " + " ; ".join(attack.conditions))
    if report.equipability == "unknown":
        warnings.append("Équipabilité non confirmée : résultats conditionnels aux équipements déclarés.")
    if attack.kind == "spell" and attack.classe is None:
        warnings.append("Classe et obtention du sort non documentées par la source : à vérifier en jeu.")
    buffs = as_stats(scenario.buffs)
    probability = scenario.critical_probability_override
    if attack.critical_denominator == 0:
        probability = 0.0
    elif probability is None and attack.critical_denominator is not None:
        if all(report.known(s) for s in ("age", "cc")):
            probability = critical_probability(attack.critical_denominator,
                                               report.totals["age"] + buffs.get("age", 0),
                                               report.totals["cc"] + buffs.get("cc", 0))
    if probability is None:
        warnings.append("Probabilité critique inconnue : espérance globale indisponible.")
    if scenario.critical_probability_override is not None:
        warnings.append("Probabilité critique saisie explicitement pour ce scénario.")
    complete = normal.complete and probability is not None and (
        probability == 0 or (critical is not None and critical.complete))
    averages = [None, None, None]
    if complete:
        for i, field in enumerate(("damage", "healing", "life_steal")):
            averages[i] = getattr(normal, field).average * (1 - probability)
            if probability:
                averages[i] += getattr(critical, field).average * probability
    failure = None if attack.failure_denominator is None else (
        0.0 if attack.failure_denominator == 0 else 1 / attack.failure_denominator)
    if failure is None:
        warnings.append("Échec critique non renseigné : espérance par tentative indisponible.")
    return CombatResult(attack_id=attack.id, attack_revision=attack.revision,
                        scenario_id=digest(scenario), normal=normal, critical=critical,
                        critical_probability=probability, failure_probability=failure,
                        average_damage=averages[0], average_healing=averages[1],
                        average_life_steal=averages[2],
                        average_damage_per_attempt=(averages[0] * (1 - failure)
                            if averages[0] is not None and failure is not None else None),
                        complete=complete, warnings=tuple(warnings),
                        sources=(attack.source, CRITICAL_SOURCE))


def compare_attacks(left: Report, right: Report, attack: AttackDefinition, scenario: CombatScenario):
    """Les deux résultats conservent les mêmes empreintes d'attaque et de scénario."""
    before, after = (simulate_attack(report, attack, scenario) for report in (left, right))
    delta = (after.average_damage - before.average_damage
             if before.average_damage is not None and after.average_damage is not None else None)
    return {"left": before.model_dump(mode="json"), "right": after.model_dump(mode="json"),
            "average_damage_delta": delta}


def weapon_attack(template):
    """Je prends les dégâts et critiques de l'arme archivée, sans bonus supposé."""
    if "arme" not in template.allowed_slots:
        raise BuildError("Cet objet n'est pas une arme.")
    lines = tuple(AttackLine(kind=e.kind, element=e.element, minimum=e.low, maximum=e.high,
                             text=e.text) for e in template.effects if e.kind in {"damage", "steal", "heal"})
    unsupported = tuple(e.text for e in template.effects if e.kind in {"unknown", "context"})
    metadata = getattr(template, "weapon", None)
    denominator = getattr(metadata, "critical_denominator", None)
    critical = None
    critical_unsupported = ()
    effects = getattr(metadata, "critical_effects", None)
    bonus = getattr(metadata, "critical_bonus", None)
    if denominator == 0:
        critical = None
    elif effects is not None:
        critical = tuple(AttackLine(kind=e.kind, element=e.element, minimum=e.low, maximum=e.high,
                                    text=e.text) for e in effects if e.kind in {"damage", "steal", "heal"})
        critical_unsupported = tuple(e.text for e in effects if e.kind not in {"damage", "steal", "heal"})
    elif bonus is not None:
        critical = tuple(AttackLine(kind=l.kind, element=l.element,
                                    minimum=l.minimum + (bonus if l.kind != "heal" else 0),
                                    maximum=l.maximum + (bonus if l.kind != "heal" else 0), text=l.text)
                         for l in lines)
    return freeze_attack(id=template.ref, name=template.name, kind="weapon", normal_lines=lines,
                         critical_lines=critical, critical_denominator=denominator,
                         failure_denominator=getattr(metadata, "failure_denominator", None),
                         ap_cost=getattr(metadata, "ap_cost", None), unsupported_effects=unsupported,
                         critical_unsupported_effects=critical_unsupported, source=template.source)
