"""Adaptation stricte des catalogues existants, sans nouveau client HTTP ni cle dans une URL."""

from __future__ import annotations

import logging
import re

from utils.exo_engine import Item, normalized, stat_key

log = logging.getLogger(__name__)
RESISTANCE_ELEMENTS = {
    "neutre": "ne", "au neutre": "ne",
    "terre": "te", "a la terre": "te",
    "feu": "fe", "au feu": "fe",
    "eau": "ea", "a l eau": "ea",
    "air": "ai", "a l air": "ai",
}
EFFECT_ALIASES = {
    "a la chance": "cha",
    **{
        f"{prefix}{label} {element}{suffix}": stat_prefix + key
        for element, key in RESISTANCE_ELEMENTS.items()
        for label in ("resistance", "resistances")
        for prefix, suffix, stat_prefix in (
            ("", "", "r_"), ("% ", "", "rp_"),
            ("% de ", "", "rp_"), ("", " %", "rp_"),
        )
    },
}

MAGEABLE = frozenset({
    "amulette", "amulettes", "anneau", "anneaux", "ceinture", "ceintures",
    "bottes", "botte", "cape", "capes", "chapeau", "chapeaux", "coiffe", "coiffes",
    "sac a dos", "epee", "epees", "hache", "haches", "marteau", "marteaux",
    "pelle", "pelles", "dague", "dagues", "arc", "arcs", "baton", "batons",
    "baguette", "baguettes",
})
IMMUTABLE = re.compile(
    r"^(?:dommages?|vole(?:r)?\s+\d|vol de vie|arme de chasse|effets? de l arme)\b"
)
RANGE = re.compile(
    r"^\s*([+-]?\d+)\s*(?:(?:à|a|[-–])\s*([+-]?\d+))?\s*(.*?)\s*$",
    re.IGNORECASE,
)


def is_mageable(entry) -> bool:
    return normalized(entry.category) in MAGEABLE and entry.kind == "item"


def parse_effects(lines: tuple[str, ...] | list[str]) -> tuple[dict, tuple, tuple]:
    bounds: dict[str, tuple[int, int]] = {}
    unsupported, immutable = [], []
    for raw in lines:
        text = str(raw).strip()
        if not text:
            continue
        clean = normalized(text)
        weapon_damage = re.fullmatch(
            r"[+]?\d+(?:\s*(?:à|a|-)\s*\d+)?\s*"
            r"\((?:dommages?|vol(?:e| de vie)?)[^)]*\)", text, re.IGNORECASE,
        )
        if IMMUTABLE.match(clean) or weapon_damage:
            immutable.append(text[:300])
            continue
        damage_percent = re.fullmatch(
            r"Augmente les dommages de\s+([+]?[\d]+(?:\s*(?:à|a|-)\s*[+]?[\d]+)?)\s*%",
            text, re.IGNORECASE,
        )
        if damage_percent:
            text = damage_percent.group(1) + " % de dommages"
        match = RANGE.fullmatch(text.replace("−", "-"))
        if match is None:
            unsupported.append(text[:300])
            continue
        first, second, label = match.groups()
        low, high = sorted((int(first), int(second or first)))
        label = re.sub(r"^(?:en |de |d['’])", "", label.strip(), flags=re.IGNORECASE)
        label = label.replace("résistances", "résistance")
        try:
            key = EFFECT_ALIASES.get(normalized(label.replace("’", "'").replace("‘", "'")))
            if key is None:
                key = stat_key(label)
            else:
                log.debug("exo: effect_alias_resolved stat=%s", key)
            if key in bounds or not -10000 <= low <= high <= 10000:
                raise ValueError("Ligne ambiguë.")
            bounds[key] = (low, high)
        except ValueError:
            unsupported.append(text[:300])
            log.debug("exo: effect_unsupported_or_ambiguous")
    return bounds, tuple(unsupported), tuple(immutable)


def from_detail(detail, enrichment=None) -> Item:
    if not is_mageable(detail.entry):
        raise ValueError("Ce type d'objet n'est pas couvert : choisissez un bijou, vêtement ou une arme mageable.")
    if enrichment is not None and enrichment.effects:
        effects = enrichment.effects
        source = "Xixou.io — effets du catalogue"
        if enrichment.stale:
            source += " (cache ancien)"
    else:
        raw = detail.data.get("stats", [])
        effects = raw if isinstance(raw, list) else []
        source = "Wiki Moon — repli sans effets Xixou"
        if detail.stale:
            source += " (cache ancien)"
    if not effects or len(effects) > 100:
        raise ValueError("Fiche sans effets exploitables. Le calculateur reste disponible sans objet.")
    bounds, unsupported, immutable = parse_effects(effects)
    return Item(
        detail.entry.name[:200], detail.entry.token, bounds, source, unsupported, immutable,
    )


def demo_item() -> Item:
    return Item(
        "Gelano · démonstration locale", "demo:gelano", {"pa": (1, 1)},
        "Exemple intégré, pas une fiche téléchargée",
    )
