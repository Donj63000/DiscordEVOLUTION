"""Validation logicielle et confrontation aux observations : deux résultats distincts.

Les cas embarqués sont synthétiques. Une validation externe exige des captures
déclarées de séances disjointes ; aucune fréquence simulée n'est une mesure du jeu.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path

from utils.exo_engine import Item, Rune, State, STATS, attempt, observe, rates_for, simulation_blocker
from utils.exo_math import integer
from utils.exo_session import Session
from utils.fm_retro_observations import Corpus, DEFAULT_PATH, load_corpus
from utils.fm_retro_reference import PROFILE, RETRO
from utils.fm_retro_statistics import total_variation, wilson

CASES_PATH = Path(__file__).resolve().parent.parent / "data" / "fm_retro" / "reference-cases-v3.json"


def reference_cases() -> list[dict]:
    """Exemples comptables conditionnels, pas relevés ni fréquences de serveur."""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    if data["reference"] != PROFILE or data["kind"] != "synthetic_conditional_examples":
        raise ValueError("Cas de référence incompatibles.")
    results = []
    for case in data["cases"]:
        item = Item(case["id"], case["id"], {k: tuple(v) for k, v in case["bounds"].items()}, "régression")
        state = State(dict(case["before"]["jets"]), Decimal(case["before"]["sink"]))
        row = observe(item, state, Rune(**case["rune"]), case["outcome"], case["losses"])
        expected = case["after"]
        passed = (state.jets == expected["jets"]
                  and state.sink == (None if expected["sink"] is None else Decimal(expected["sink"])))
        results.append({"id": case["id"], "passed": passed, "ledger": row["ledger"],
                        "kind": "conditional_accounting_not_game_observation"})
    return results


def _identity(outcome: str, jets, sink) -> tuple:
    return outcome, tuple(sorted((k, v) for k, v in dict(jets).items() if v)), sink


def monte_carlo(draws: int = 100000, seed: int = 129) -> dict:
    """Teste de vrais appels au moteur, sur tous les tiers et trois régimes.

    Chaque pose repart du même avant : le test contrôle une distribution
    conditionnelle, non une chaîne de jets changeants. Seuil six écarts-types,
    graines fixées, pour éviter une recette aléatoire à seuil 5 %.
    """
    integer(draws, 1000, 10000000, "Nombre de poses")
    integer(seed, 0, 2**32 - 1, "Graine")
    corpus = load_corpus(DEFAULT_PATH)
    scenarios = []
    for key, stat in STATS.items():
        for tier, gain in enumerate(stat.gains):
            # Maximum naturel haut : pas de blocage d'over sur les lignes lourdes.
            maximum = max(gain * 12, 1)
            item = Item("Cas synthétique", f"audit-{key}-{tier}",
                        {key: (0, maximum)}, "Monte-Carlo logiciel")
            for sink in (Decimal(0), Decimal(200)):
                scenarios.append((f"{key}:{tier}:puits={sink}", item,
                                  {key: gain * 2}, sink, Rune(key, tier)))
    base = Item("Gelano synthétique", "audit-exo", {"fo": (0, 10)}, "Monte-Carlo logiciel")
    for key in ("pa", "pm", "po"):
        scenarios.append((f"exo-{key}", base, {"fo": 10}, Decimal(0), Rune(key)))
    over = Item("Over synthétique", "audit-over", {"fo": (1, 50), "sa": (1, 20)}, "Monte-Carlo logiciel")
    scenarios.append(("over-force", over, {"fo": 50, "sa": 18}, Decimal(20), Rune("fo", 1)))
    complex_item = Item(
        "Remontage synthétique", "audit-complex",
        {"pa": (1, 1), "fo": (1, 50), "vi": (1, 200), "sa": (1, 30), "cc": (1, 4)},
        "Monte-Carlo logiciel",
    )
    for sink in (Decimal(0), Decimal(10), Decimal(101)):
        scenarios.append((f"complex-perfect:puits={sink}", complex_item,
                          {"pa": 1, "fo": 47, "vi": 200, "sa": 30, "cc": 4}, sink, Rune("fo", 1)))
    for sink in (Decimal(0), Decimal(100)):
        scenarios.append((f"complex-vi:puits={sink}", complex_item,
                          {"pa": 1, "fo": 50, "vi": 170, "sa": 30, "cc": 4}, sink, Rune("vi", 2)))
    extra_item = Item("Exo déjà présent", "audit-existing-exo", {"fo": (1, 50)}, "Monte-Carlo logiciel")
    scenarios.append(("sacrifice-existing-pa", extra_item, {"fo": 20, "pa": 1}, Decimal(100), Rune("fo")))
    per_case, remainder = divmod(draws, len(scenarios))
    reports, total, ledger_errors = [], 0, 0
    for index, (ident, item, jets, sink, rune) in enumerate(scenarios):
        n = per_case + int(index < remainder)
        rate = rates_for(item, State(dict(jets), sink), rune, None, corpus=corpus)
        expected = {"SC": rate.sc, "SN": rate.sn, "EC": rate.ec}
        counts, errors = Counter(), 0
        for repetition in range(n):
            state = State(dict(jets), sink)
            row = attempt(item, state, rune, None, seed + repetition, corpus=corpus)
            counts[row["outcome"]] += 1
            if row["ledger"]["residual"] != "0" or state.sink < 0:
                errors += 1
            if row["outcome"] == "SC" and (row["losses"] or state.sink != sink):
                errors += 1
        within = all(abs(counts[key] - n * p) <= 6 * math.sqrt(n * p * (1 - p)) + 1
                     for key, p in expected.items())
        reports.append({"case": ident, "draws": n, "expected": expected, "counts": dict(counts),
                        "wilson_95": {key: list(wilson(counts[key], n)) for key in expected},
                        "within_six_sigma": within, "ledger_errors": errors})
        total += n
        ledger_errors += errors
    references = reference_cases()
    return {
        "kind": "software_monte_carlo_not_server_validation", "reference": PROFILE,
        "reference_sha256": RETRO.digest, "corpus_sha256": corpus.digest, "seed": seed, "draws": total,
        "passed": all(r["within_six_sigma"] for r in reports) and not ledger_errors
                  and all(r["passed"] for r in references),
        "ledger_errors": ledger_errors, "cases": reports, "reference_cases": references,
        "server_fidelity_demonstrated": False,
    }


def evaluate(corpus: Corpus, draws_per_context: int = 2000, seed: int = 129,
             max_contexts: int = 100) -> dict:
    """Confronte le moteur SERVI à des séances tenues hors apprentissage.

    On rapporte aussi les contextes non couverts, les puits inconnus et les
    probabilités nulles. Ni exclusion silencieuse ni certificat automatique.
    La variation totale jointe est descriptive et dépend de la taille du relevé.
    """
    integer(draws_per_context, 100, 100000, "Poses par contexte")
    integer(max_contexts, 1, 100, "Maximum de contextes")
    integer(seed, 0, 2**32 - 1, "Graine")
    grouped, unavailable = defaultdict(list), Counter()
    validation = [row for row in corpus.observations if row.split == "validation"]
    for row in validation:
        blocker = simulation_blocker(row.item(), row.state_before(), row.rune(), corpus=corpus)
        if blocker or row.sink_after is None:
            unavailable[blocker or "Puits final inconnu"] += 1
        else:
            grouped[row.context].append(row)
    selected = sorted(grouped.values(), key=lambda rows: (-len(rows), rows[0].id))[:max_contexts]
    reports, scored, brier_total, log_total, zeros = [], 0, 0.0, 0.0, 0
    for rows in selected:
        prototype = rows[0]
        item, rune = prototype.item(), prototype.rune()
        rate = rates_for(item, prototype.state_before(), rune, None, corpus=corpus)
        probabilities = {"SC": rate.sc, "SN": rate.sn, "EC": rate.ec}
        actual = Counter(_identity(row.outcome, row.after, row.sink_after) for row in rows)
        observed_outcomes = Counter(row.outcome for row in rows)
        predicted, predicted_losses, predicted_sink = Counter(), Counter(), Counter()
        for index in range(draws_per_context):
            state = prototype.state_before()
            event = attempt(item, state, rune, None, seed + index, corpus=corpus)
            predicted[_identity(event["outcome"], state.jets, state.sink)] += 1
            predicted_losses.update(key for key, amount in event["losses"].items() if amount)
            predicted_sink[state.sink] += 1
        n = len(rows)
        actual_losses = Counter(key for row in rows for key, amount in row.losses if amount)
        actual_sink = Counter(row.sink_after for row in rows)
        for row in rows:
            brier_total += sum((p - int(row.outcome == key)) ** 2 for key, p in probabilities.items())
            p = probabilities[row.outcome]
            if not p:
                zeros += 1
            else:
                log_total -= math.log(p)
        scored += n
        reports.append({
            "item": item.token, "before": {"jets": dict(prototype.before), "sink": str(prototype.context[3])},
            "rune": {"stat": rune.stat, "tier": rune.tier}, "source": rate.source,
            "validation_n": n, "simulation_n": draws_per_context,
            "outcomes_observed": dict(observed_outcomes), "outcomes_predicted": probabilities,
            "outcomes_wilson_95": {key: list(wilson(observed_outcomes[key], n)) for key in probabilities},
            "joint_total_variation": total_variation(
                {key: count / n for key, count in actual.items()},
                {key: count / draws_per_context for key, count in predicted.items()}),
            "sink_total_variation": total_variation(
                {key: count / n for key, count in actual_sink.items()},
                {key: count / draws_per_context for key, count in predicted_sink.items()}),
            "loss_frequency_observed": {key: value / n for key, value in actual_losses.items()},
            "loss_frequency_predicted": {key: value / draws_per_context for key, value in predicted_losses.items()},
            "mean_sink_observed": sum(float(row.sink_after) for row in rows) / n,
            "mean_sink_predicted": sum(float(key) * value for key, value in predicted_sink.items()) / draws_per_context,
        })
    return {
        "kind": "external_holdout_comparison", "reference": PROFILE, "corpus": corpus.summary(),
        "status": "measured_on_declared_holdout" if scored else "insufficient_data",
        "validation_n": len(validation), "scored_n": scored,
        "coverage": scored / len(validation) if validation else 0,
        "unavailable": dict(unavailable), "omitted_contexts_by_limit": max(0, len(grouped) - len(selected)),
        "brier": brier_total / scored if scored else None,
        "log_loss": log_total / scored if scored and not zeros else None,
        "zero_probability_observations": zeros,
        "contexts": reports, "server_fidelity_demonstrated": False,
        "warning": "Provenances déclaratives, corrélation au sein d'une séance possible ; aucune certification globale.",
    }


def collect(session: Session, *, session_id: str, split: str, source: dict, corpus_id: str) -> dict:
    """Exporte UNIQUEMENT le mode suivi. L'exploitant doit vérifier les captures."""
    if session.item.unsupported:
        raise ValueError("Les effets inconnus doivent être résolus avant de constituer un corpus.")
    rows = session.observed.journal
    if not rows or session.observed.profile != PROFILE:
        raise ValueError("Un historique de suivi v3 non vide est requis.")
    observations = []
    for row in rows:
        if row.get("profile") != PROFILE or row["mode"] != "observation":
            raise ValueError("Ne mélangez pas simulations, anciens modèles et relevés.")
        observations.append({
            "id": f"{session_id}:{row['n']}", "session": session_id, "split": split,
            "origin": "game_observation", "source": dict(source),
            "item": {"name": session.item.name, "token": session.item.token,
                     "bounds": {key: list(pair) for key, pair in session.item.bounds.items()}},
            # Le suivi calcule son puits : ce n'est pas une mesure indépendante.
            # Le curateur pourra le renseigner après reconstruction depuis une
            # capture complète, jamais en recyclant les calculs de notre moteur.
            "before": {"jets": row["before"], "sink": None},
            "after": {"jets": row["after"], "sink": None},
            "rune": {"stat": row["stat"], "tier": row["tier"]}, "outcome": row["outcome"],
        })
    return {"schema": 1, "reference": PROFILE, "id": corpus_id,
            "description": "Relevés déclarés depuis le suivi manuel ; captures à contrôler par l'exploitant.",
            "observations": observations}
