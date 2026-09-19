"""Simulation de lignes explicites. Formule expérimentale, jamais annoncée exacte.

Pas de conversion de sorts/maîtrises/critique implicite : les effets absents ne
sont pas inventés. Les bornes d'attaque normale et critique sont distinctes.
"""
from typing import Annotated, Literal
from pydantic import Field, StrictInt
from .models import Frozen, BuildError, Report

class DamageLine(Frozen):
    element: Literal["ne", "te", "fe", "ea", "ai"]
    minimum: Annotated[StrictInt, Field(ge=0, le=10000)]
    maximum: Annotated[StrictInt, Field(ge=0, le=10000)]
    life_steal: bool = False

class Scenario(Frozen):
    coefficient_percent: Annotated[StrictInt, Field(ge=0, le=1000)] = 100
    flat_resistance: Annotated[StrictInt, Field(ge=0, le=10000)] = 0
    percent_resistance: Annotated[StrictInt, Field(ge=-100, le=100)] = 0

ELEMENT_STAT = {"ne": "fo", "te": "fo", "fe": "ine", "ea": "cha", "ai": "age"}


def simulate(report: Report, lines: tuple[DamageLine, ...], scenario: Scenario):
    if not lines or len(lines) > 16:
        raise BuildError("Une à seize lignes de dégâts explicites sont nécessaires.")
    results = []
    for line in lines:
        if line.minimum > line.maximum:
            raise BuildError("Intervalle de dégâts inversé.")
        stat = ELEMENT_STAT[line.element]
        # Refuser une simulation si des bonus numériques nécessaires sont inconnus.
        if not all(report.known(s) for s in (stat, "pui", "do")):
            raise BuildError("Caractéristiques offensives incomplètes. Aucun dégât calculé sur un total incertain.")
        total = report.totals
        def damage(base):
            increased = base * (100 + max(0, total[stat]) + total["pui"]) // 100
            increased = increased * scenario.coefficient_percent // 100 + total["do"]
            reduced = max(0, increased - scenario.flat_resistance)
            return max(0, reduced * (100 - scenario.percent_resistance) // 100)
        low, high = damage(line.minimum), damage(line.maximum)
        results.append({"element": line.element, "minimum": low, "maximum": high,
                        "vol_de_vie": line.life_steal,
                        "soin_min_hypothese": low // 2 if line.life_steal else None,
                        "soin_max_hypothese": high // 2 if line.life_steal else None})
    return {"lines": results, "minimum": sum(r["minimum"] for r in results), "maximum": sum(r["maximum"] for r in results),
            "certified": False, "formula": "linear-experimental-v1",
            "warning": "ESTIMATION EXPÉRIMENTALE : coefficient déclaré, résistance fixe puis %. Ordre/arrondis non validés sur un personnage étalon Retro. Hors buffs, maîtrises automatiques, probabilités critiques, effets spéciaux et plafonds de soins."}
