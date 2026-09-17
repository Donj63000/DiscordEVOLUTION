"""Contrat FM partage par le moteur, le guide IA et le routage, sans Discord.

Chaque poids, taux et transition conserve son origine et son degré de preuve.
Le guide ne calcule jamais de résultat à la place du moteur.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from utils.exo_engine import (
    DISCLAIMER, PROFILE, Rune, STATS, eligibility, normalized, rates_for,
    simulation_blocker, validate_item_jets, weight_text,
)

if TYPE_CHECKING:
    from utils.exo_session import Session

from utils.fm_retro_reference import RETRO
from utils.fm_retro_observations import default_corpus

REFERENCE_VERSION = RETRO.version


def model_reference() -> dict:
    result = RETRO.summary()
    result["profil_tirage"] = PROFILE
    result["corpus"] = default_corpus().summary()
    return result


def rune_details(rune: Rune) -> dict:
    return {
        "nom": rune.name,
        "stat": rune.stat,
        "caracteristique": STATS[rune.stat].name,
        "taille": ("normale", "Pa", "Ra")[rune.tier],
        "gain": rune.gain,
        "poids_par_point": weight_text(STATS[rune.stat].weight),
        "poids_total": weight_text(rune.weight),
        "poids_nominal": weight_text(rune.nominal_weight),
        "arrondi_cout": "plafond entier du poids nominal, convention Rétro documentée",
        "sources": list(RETRO.stats[rune.stat].sources),
        "niveau_preuve": RETRO.metadata["stats"][rune.stat]["evidence"],
        "limites": RETRO.metadata["stats"][rune.stat].get("scope", ""),
    }


def _runes() -> dict[str, Rune]:
    values = {}
    for key, stat in STATS.items():
        for tier in range(len(stat.gains)):
            rune = Rune(key, tier)
            values[normalized(rune.name)] = rune
            values[normalized(("", "Pa ", "Ra ")[tier] + stat.name)] = rune
    values.update({"pa": Rune("pa"), "pm": Rune("pm"), "ga pm": Rune("pm")})
    return values


RUNE_NAMES = _runes()
_ACTION = re.compile(
    r"(?:evo )?(?:(?:peux tu|pourrais tu|veux tu) )?"
    r"(?:pose|poser|applique|appliquer|tente|tenter|passe|passer|mets|mettre) "
    r"(?:moi )?(?:(?:une(?: seule)?|1|la) )?(?:rune )?(.+?)"
    r"(?: (?:dans|sur) (?:ma simulation|mon atelier|ma session|mon objet))?"
    r"(?: (?:stp|s il te plait|s il vous plait))?"
)


def requested_rune(text: str) -> Rune | None:
    """Reconnaît une demande unique explicite, sans conseil, condition ni citation."""
    if not isinstance(text, str) or any(mark in text for mark in ('"', "«", "»", "\n", ";")):
        return None
    match = _ACTION.fullmatch(normalized(text))
    return RUNE_NAMES.get(match[1]) if match else None


# Les alternatives longues passent en premier : "Ra Fo" ne devient pas "Fo",
# "Pa Fo" ne devient pas une rune PA. Les bornes couvrent aussi le symbole %.
_RUNE_MENTION = re.compile(
    r"(?<![a-z0-9%])(" + "|".join(
        re.escape(name) for name in sorted(RUNE_NAMES, key=lambda name: (-len(name), name))
    ) + r")(?![a-z0-9%])"
)


def fm_guide(subject: str) -> dict:
    key = normalized(subject)
    selected = list(dict.fromkeys(RUNE_NAMES[match[1]] for match in _RUNE_MENTION.finditer(key)))[:6]
    if not selected:
        selected = [Rune(stat, tier) for stat in ("pa", "pm", "po", "fo", "vi")
                    for tier in range(len(STATS[stat].gains))]
    stats = list(dict.fromkeys(rune.stat for rune in selected))
    return {
        # Ancien champ conserve, mais son unite n'est plus implicite.
        "poids_nominaux": {STATS[stat].name: weight_text(STATS[stat].weight) for stat in stats},
        "unite_poids_nominaux": "poids d'un point, pas poids de la rune entière",
        "runes": [rune_details(rune) for rune in selected],
        "referentiel": model_reference(),
        "source": "data/fm_retro/reference-v3.json ; docs/FM-RETRO-V3.md",
        "principes": [
            "Poids nominal = gain × poids par point ; coût de pose = plafond entier. Ra Vi : 7,5 nominal, coût 8.",
            "Un bonus déjà natif n'est pas un exo de ce même bonus.",
            "Facilité de remontage, coût en kamas et probabilité de passage sont distincts.",
            "Le puits exact demande un état initial connu et un journal complet ; sinon il est inconnu.",
            "Ne pas promettre un passage, inventer un prix ou présenter ces taux comme ceux d'Ankama.",
        ],
        "avertissement": DISCLAIMER,
    }


def session_advice(session: Session) -> dict:
    """Données minimales de conseil ; ni graine, prix, budget, ni historique."""
    state = session.state
    allowed, reason = eligibility(session.item, state, session.rune)
    blocker = simulation_blocker(session.item, state, session.rune, session.rates)
    if session.mode != "simulation":
        blocker = "Suivi déclaratif : aucun tirage autorisé."
    try:
        validate_item_jets(session.item, state.jets)
        valid_state = True
    except ValueError:
        valid_state = False
    rates = rates_for(session.item, state, session.rune, session.rates) if not blocker else None
    return {
        "rune": rune_details(session.rune),
        "admissibilite": {
            "jet_conforme_profil": valid_state,
            "rune_admissible_profil": allowed,
            "motif": reason,
            "simulation_possible": not blocker,
            "blocage": blocker or None,
        },
        # Un taux de scenario bloque n'est jamais presente comme utilisable.
        "taux_prochaine_pose": None if rates is None else {
            "sc": rates.sc, "sn": rates.sn, "ec": rates.ec,
            "unite": "probabilite_entre_0_et_1", "source": rates.source,
            "certifie_ankama": False,
        },
        "referentiel": model_reference(),
        "observations_contexte": (
            sample.summary() if (sample := (
                default_corpus().match(session.item, state, session.rune)
                if session.rates is None and not blocker else None
            )) is not None else None
        ),
    }
