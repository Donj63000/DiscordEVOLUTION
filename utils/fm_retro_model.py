"""Interpolation explicite des enveloppes historiques, jamais une formule serveur.

Les ancrages sont publiés/reproduits ; leur interpolation ne l'est pas. Elle sert
uniquement de repli lorsqu'aucun contexte observé n'est couvert par le corpus.
Aucun résultat généré ici ne peut devenir une observation d'entraînement.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from utils.fm_retro_reference import RETRO, MODEL

if TYPE_CHECKING:
    from utils.exo_engine import Item, State, Rune


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))


def _mix(left: tuple[float, float], right: tuple[float, float], fraction: float):
    t = _unit(fraction)
    return tuple(a + (b - a) * t for a, b in zip(left, right))


def forecast(item: Item, state: State, rune: Rune) -> tuple[float, float, str]:
    """Retourne une estimation conditionnelle ; les cinq ancrages ne sont pas un corpus.

    La pression est calculée sur les AUTRES lignes. L'adéquation dépend du
    gain de la rune, pas de son nom ou de son prix. Le puits n'est pas assimilé
    à la différence avec le jet parfait et ne bonifie pas le taux de passage.
    Le niveau du métier est supposé 100 ; aucun coefficient de niveau inventé.
    """
    from utils.exo_engine import surplus  # Dépendance locale : aucun cycle à l'import.

    current = max(0, state.jets.get(rune.stat, 0))
    maximum = max(0, item.maximum(rune.stat))
    target = current + rune.gain
    anchor = RETRO.anchors
    comfort = RETRO.metadata["rules"]["comfort_multiplier"]["value"]
    adequacy = min(1.0, comfort * rune.gain / max(rune.gain, current))
    other_max = sum(
        max(0, high) * float(RETRO.stats[key].weight)
        for key, (_, high) in item.bounds.items() if key != rune.stat
    )
    other_now = sum(
        max(0, value) * float(RETRO.stats[key].weight)
        for key, value in state.jets.items() if key != rune.stat
    )
    # Début au jet moyen, saturation au parfait : forme communautaire, non mesurée.
    pressure = _unit(2 * other_now / other_max - 1) if other_max else float(other_now > 0)
    if rune.stat in item.bounds and target <= maximum:
        start = RETRO.metadata["rules"]["natural_difficulty_start"]["value"]
        # Une ligne naturellement fixe n'a pas de zone 80 %-100 % à parcourir.
        fixed = item.bounds[rune.stat][0] == maximum
        finish = 0.0 if fixed else _unit((target / maximum - start) / (1 - start))
        rates = _mix(anchor["easy_rebuild"], anchor["simple_perfect"], finish)
        rates = _mix(rates, anchor["complex_perfect"], pressure)
        kind = "remontage"
    else:
        projected = {**state.jets, rune.stat: target}
        cap = float(RETRO.metadata["rules"]["extra_cap"]["value"])
        occupation = _unit(float(surplus(item, projected)) / cap)
        # La combinaison multiplicative conserve les bornes sans coefficient ajusté.
        # Ce choix de surface est une HYPOTHÈSE, pas une déduction de ces bornes.
        severity = 1 - (1 - occupation) * (1 - pressure)
        rates = _mix(anchor["creation_best"], anchor["creation_worst"], severity)
        kind = "exo" if rune.stat not in item.bounds else "over"
    rates = _mix(anchor["creation_worst"], rates, adequacy)
    sc, sn = (round(value, 12) for value in rates)
    # L'arrondi ne peut ni produire un EC négatif ni dépasser les bornes publiées.
    sn = min(sn, 1 - sc)
    return sc, sn, f"{MODEL} · {kind} · interpolation hypothétique, non calibrée"
