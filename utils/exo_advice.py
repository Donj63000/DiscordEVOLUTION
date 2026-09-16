"""Contrat FM partage par le moteur, le guide IA et le routage, sans Discord.

Les valeurs de jeu ci-dessous decrivent uniquement le modele local existant.
Aucune calibration serveur ni modification des tirages n'est introduite ici.
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

REFERENCE_VERSION = "retro-workshop-v2-audit-1"


def model_reference() -> dict:
    """Metadata versionnee ; le profil de tirage reste v2 pour la reproductibilite."""
    return {
        "version": REFERENCE_VERSION,
        "profil_tirage": PROFILE,
        "statut": "pedagogique_non_calibre",
        "certifie_ankama": False,
        "poids": "conventions du moteur local ; version serveur non vérifiée",
        "taux_ordinaires": "formules heuristiques, non mesurées sur le serveur",
        "exo_pa_pm_po": "hypothèse communautaire : 1 % SC, 0 % SN si bonus absent",
        "pertes": "heuristique locale : surplus tiers, puits, puis lignes au hasard",
    }


def rune_details(rune: Rune) -> dict:
    return {
        "nom": rune.name,
        "stat": rune.stat,
        "caracteristique": STATS[rune.stat].name,
        "taille": ("normale", "Pa", "Ra")[rune.tier],
        "gain": rune.gain,
        "poids_par_point": weight_text(STATS[rune.stat].weight),
        "poids_total": weight_text(rune.weight),
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
        "source": "utils/exo_engine.py et docs/EXO-FM-PATCH.md ; modèle local du bot",
        "principes": [
            "Poids total de la rune = gain × poids par point. Utiliser poids_total pour le coût en poids.",
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
    blocker = simulation_blocker(session.item, state, session.rune)
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
    }
