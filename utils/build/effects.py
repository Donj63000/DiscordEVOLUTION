"""Un effet externe est une donnée ; les inconnues ne deviennent jamais zéro."""
from __future__ import annotations
import re
from .models import BuildError, Contribution, Effect, Instance, ItemTemplate, STAT_LABELS

ELEMENTS = {"neutre": "ne", "terre": "te", "feu": "fe", "eau": "ea", "air": "ai"}
ATTACK = re.compile(r"^(?:(dommages?|vole(?:r)?|vol de vie)\s*:?\s*)?\+?(\d+)(?:\s*(?:à|a|-)\s*\+?(\d+))?\s*(?:PDV\s*)?\((?:(dommages?|vol(?:e| de vie)?)\s+)?(neutre|terre|feu|eau|air)\)$", re.I)


def parse_line(raw: str, index: int) -> Effect:
    from utils.exo_data import parse_effects
    text = raw.strip().replace("−", "-").replace("–", "-")
    ref = f"e{index}"
    original = text
    text = re.sub(r"\s*\(Capacités accrues\s*:\s*\d+\)\s*$", "", text, flags=re.I)
    if text != original:
        parsed = parse_line(text, index)
        return Effect(**{**parsed.model_dump(), "text": original[:600]})
    if re.fullmatch(r"(?:Échangeable dès le\s*:\s*\d+|Vaincre \d+x .+ \([+]\d+ .+\)|"
                    r"Résistance\s*:\s*\d+\s*/\s*\d+|Lié au (?:compte|personnage))", text, re.I):
        return Effect(ref=ref, kind="cosmetic", text=text[:600])
    healing = re.fullmatch(r"(?:PDV rendus|Soigne)\s*:?\s*(\d+)(?:\s*(?:à|a)\s*(\d+))?", text, re.I)
    if healing:
        low, high = sorted((int(healing[1]), int(healing[2] or healing[1])))
        return Effect(ref=ref, kind="heal", low=low, high=high, text=text[:600])
    match = ATTACK.fullmatch(text)
    if match:
        prefix, low, high, label, element = match.groups()
        a, b = sorted((int(low), int(high or low)))
        kind = "steal" if "vol" in ((prefix or "") + (label or "")).lower() else "damage"
        return Effect(ref=ref, kind=kind, low=a, high=b, element=ELEMENTS[element.lower()], text=text[:600])
    # Vie (PV) et vitalité sont distinctes. Ces lignes ne sont pas des jets
    # de forgemagie classiques, mais apparaissent dans les bonus publiés.
    for pattern, stat in (
        (r"^([+-]?\d+)(?:\s*(?:à|a)\s*([+-]?\d+))?\s+(?:en vie|points? de vie|PDV)$", "pv"),
        (r"^Augmente le poids portable de\s+(\d+)(?:\s*(?:à|a)\s*(\d+))?\s+pods$", "pod"),
    ):
        matched = re.fullmatch(pattern, text, re.I)
        if matched:
            low, high = sorted((int(matched[1]), int(matched[2] or matched[1])))
            return Effect(ref=ref, kind="stat", stat=stat, low=low, high=high, text=text[:600])
    # PvP, effets temporaires et conditions sont exclus des statistiques permanentes.
    if re.search(r"\b(?:pvp|tour|tours|contre les joueurs|aux combattants|face aux combattants|si |lorsque |sort )", text, re.I):
        return Effect(ref=ref, kind="context", text=text[:600])
    if text.lower() in {"arme de chasse", "effets de l'arme", "effets de l’arme"}:
        return Effect(ref=ref, kind="cosmetic", text=text)
    if re.search(r"-\d+\s*(?:à|a)\s*\+?\d+", text) and not re.search(r"-\d+\s*(?:à|a)\s*-\d+", text):
        return Effect(ref=ref, kind="unknown", text=text[:600])
    bounds, unsupported, immutable = parse_effects([text])
    if len(bounds) == 1 and not unsupported and not immutable:
        stat, (low, high) = next(iter(bounds.items()))
        return Effect(ref=ref, kind="stat", stat=stat, low=low, high=high, text=text[:600])
    return Effect(ref=ref, kind="unknown", text=text[:600])


def parse_lines(lines) -> tuple[Effect, ...]:
    if not isinstance(lines, (list, tuple)) or len(lines) > 100:
        raise BuildError("Liste d'effets mal formée ou trop longue.")
    if any(not isinstance(line, str) or len(line) > 600 for line in lines):
        raise BuildError("Effet mal formé.")
    return tuple(parse_line(line, i) for i, line in enumerate(lines) if line.strip())


def affected_stats(effect):
    """Je limite l'incertitude aux caractéristiques explicitement identifiables."""
    from .conditions import norm
    text = norm(effect.text)
    match = re.fullmatch(r"[+-]?\d+(?:\s*(?:a|-)\s*[+-]?\d+)?\s*(%)?\s*(?:de\s+)?"
                         r"(?:resistance|res\.)\s+(neutre|terre|feu|eau|air)\s+"
                         r"(?:contre les joueurs|aux combattants|face aux combattants|pvp)", text)
    if match:
        return {("rp_" if match[1] else "r_") + ELEMENTS[match[2]]}
    return set(STAT_LABELS)


def resolve_values(template: ItemTemplate, instance: Instance, origin: str):
    """Valeurs naturelles remplacées, exos distincts ; double comptage interdit."""
    definitions = {e.ref: e for e in template.effects if e.kind == "stat"}
    replacements = {v.ref: v.value for v in instance.final_values}
    if set(replacements) - definitions.keys():
        raise BuildError("Jet portant sur une ligne inconnue ou non modifiable.")
    if instance.mode != "natural_best" and set(replacements) != definitions.keys():
        raise BuildError("Fournir toutes les lignes naturelles pour des jets personnalisés.")
    natural_stats = {e.stat for e in definitions.values()}
    if any(extra.stat in natural_stats for extra in instance.extras):
        raise BuildError("Un over remplace la ligne naturelle ; ne pas l'ajouter aussi comme exo.")
    result = []
    for effect in definitions.values():
        value = replacements.get(effect.ref, effect.high)
        if instance.mode != "declared_fm" and not effect.low <= value <= effect.high:
            raise BuildError("Jet hors bornes naturelles ; choisir explicitement FM déclarée.")
        result.append(Contribution(origin=origin + ":" + effect.ref, stat=effect.stat, value=value))
    for extra in instance.extras:
        result.append(Contribution(origin=origin + ":exo", stat=extra.stat, value=extra.value))
    unknown = tuple(e for e in template.effects if e.kind in {"unknown", "context"})
    return tuple(result), unknown
