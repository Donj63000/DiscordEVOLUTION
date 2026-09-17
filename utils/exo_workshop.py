"""Parcours d'atelier independant de Discord et testable sans connexion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random

from utils.exo_engine import (
    PROFILE, State, attempt, integer, parse_jets, validate_goals,
)
from utils.exo_session import Session
from utils.fm_retro_observations import active_model_signature


@dataclass(frozen=True)
class BatchResult:
    requested: int
    rows: tuple[dict, ...]
    before: dict[str, int]
    after: dict[str, int]
    sink_before: Decimal
    sink_after: Decimal
    stop_reason: str

    @property
    def changes(self) -> dict[str, list[int]]:
        touched = {key for row in self.rows for key in row["changes"]}
        return {key: [self.before.get(key, 0), self.after.get(key, 0)]
                for key in sorted(touched)}

    @property
    def losses(self) -> dict[str, int]:
        result = {}
        for row in self.rows:
            for key, value in row["losses"].items():
                result[key] = result.get(key, 0) + value
        return {key: value for key, value in sorted(result.items()) if value}


def set_goals(session: Session, text: str) -> None:
    goals = parse_jets(text)
    if not goals:
        raise ValueError("Indiquez au moins un objectif : pm=1 ; pa=1.")
    for value in goals.values():
        integer(value, 1, 10000, "Objectif")
    validate_goals(session.item, goals)
    session.goal_stat, session.goal_value = next(iter(goals.items()))
    session.quality = {key: value for key, value in goals.items() if key != session.goal_stat}


def reset_simulation(session: Session, kind: str, seed: int) -> None:
    if session.mode != "simulation":
        raise ValueError("Les jets de départ automatiques sont réservés à la simulation.")
    if kind not in {"minimum", "random", "perfect"}:
        raise ValueError("Jet de départ inconnu.")
    integer(seed, 0, 2**64 - 1, "Graine")
    rng = random.Random(f"{PROFILE}:initial:{seed}")
    jets = {
        key: low if kind == "minimum" else high if kind == "perfect" else rng.randint(low, high)
        for key, (low, high) in sorted(session.item.bounds.items())
    }
    session.sim = State(jets)
    session.model_signature = active_model_signature()
    session.seed = seed
    session.journal_page = 0
    session.last_changes = {}
    session.notice = (
        "Nouveau jet de simulation, puits 0 et compteurs remis à zéro. "
        "Objectifs, prix et suivi réel conservés. « Annuler » restaure la session précédente."
    )


def simulate_batch(session: Session, count: int) -> BatchResult:
    """Applique un lot a la copie de session fournie par le controleur transactionnel."""
    integer(count, 1, 100, "Taille du lot")
    if session.mode != "simulation":
        raise ValueError("Les tirages sont désactivés dans le suivi réel.")
    session.ensure_model()
    validate_goals(session.item, session.requirements)
    target = session.rune_target
    if count > 1 and session.sim.jets.get(session.rune.stat, 0) >= target:
        raise ValueError(
            "La ligne a déjà atteint son seuil. Modifiez « Objectifs » ou utilisez ×1 "
            "pour tenter volontairement un over."
        )
    before, sink_before = dict(session.sim.jets), session.sim.sink
    rows = []
    reason = "Nombre demandé atteint."
    for _ in range(count):
        current = session.sim.jets.get(session.rune.stat, 0)
        if count > 1 and current + session.rune.gain > target:
            reason = "Prochaine rune au-delà du seuil : choisissez une rune plus petite ou ×1."
            break
        try:
            row = attempt(
                session.item, session.sim, session.rune, session.rates, session.seed, session.price,
            )
        except ValueError as exc:
            if not rows:
                raise
            reason = f"Arrêt avant la rune suivante : {exc}"
            break
        rows.append(row)
        if session.sim.jets.get(session.rune.stat, 0) >= target:
            if session.reached:
                reason = "Tous les objectifs sont atteints."
            elif session.goal_met:
                reason = "Bonus principal obtenu ; les autres seuils restent à remonter."
            else:
                reason = "Seuil de cette ligne atteint ; choisissez la prochaine caractéristique."
            break
    if not rows:
        raise ValueError(reason)
    result = BatchResult(
        count, tuple(rows), before, dict(session.sim.jets),
        sink_before, session.sim.sink, reason,
    )
    session.last_changes = result.changes
    session.journal_page = 0
    return result
