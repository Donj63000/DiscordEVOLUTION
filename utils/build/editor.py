"""Conversion de saisies humaines et modifications immuables, sans Discord."""
from __future__ import annotations
import re

from .conditions import norm
from .models import (BuildError, Profile, PRIMARY, STAT_LABELS, CLASSES, as_stats,
                     values, revised, EffectValue)
from .import_export import parse_json

ALIASES = {norm(label): key for key, label in STAT_LABELS.items()}
ALIASES.update({key: key for key in STAT_LABELS})
ALIASES.update({"vita": "vi", "intel": "ine", "agi": "age", "sag": "sa",
                "vie": "pv", "dommages fixes": "do", "puissance": "pui",
                "coups critiques": "cc", "cc": "cc", "portee": "po"})
SLOT_LABELS = {"coiffe": "Coiffe", "cape": "Cape", "amulette": "Amulette",
               "ceinture": "Ceinture", "bottes": "Bottes", "anneau_1": "Anneau 1",
               "anneau_2": "Anneau 2", "arme": "Arme", "bouclier": "Bouclier",
               "compagnon": "Familier / monture", **{f"dofus_{i}": f"Dofus {i}" for i in range(1, 7)}}


def stat_name(value: str) -> str:
    key = ALIASES.get(norm(value))
    if key is None:
        raise BuildError("Caractéristique inconnue. Exemples : Chance, Vitalité, PA, PM, Portée.")
    return key


def integer(value: str, label: str, low: int, high: int) -> int:
    value = value.strip().replace("\u00a0", "").replace("\u202f", "")
    if not re.fullmatch(r"[+-]?\d{1,6}", value):
        raise BuildError(f"{label} : saisir un nombre entier, sans formule.")
    number = int(value)
    if not low <= number <= high:
        raise BuildError(f"{label} : valeur attendue entre {low} et {high}.")
    return number


def parse_stats(text: str, *, allowed=None, low=-10000, high=10000):
    """Accepte 'Chance: 300' par ligne ; garde le JSON historique compatible."""
    if len(text) > 4000:
        raise BuildError("Saisie de caractéristiques trop longue.")
    text = text.strip()
    if not text:
        return ()
    if text.startswith("{"):
        rows = parse_json(text.encode())
        if not isinstance(rows, dict) or any(type(v) is not int for v in rows.values()):
            raise BuildError("Les caractéristiques doivent être des nombres entiers.")
        pairs = list(rows.items())
    else:
        lines = [line.strip() for line in re.split(r"[\n;,]+", text) if line.strip()]
        pairs = []
        for line in lines:
            match = re.fullmatch(r"(.+?)\s*[:=]\s*([+-]?\d{1,6})", line)
            if not match:
                raise BuildError("Format attendu : Chance: 300, une caractéristique par ligne.")
            pairs.append((match[1], int(match[2])))
    if len(pairs) > len(STAT_LABELS):
        raise BuildError("Trop de caractéristiques.")
    result = {}
    for label, value in pairs:
        key = stat_name(label)
        if key in result or (allowed is not None and key not in allowed):
            raise BuildError("Caractéristique répétée ou non autorisée dans ce champ.")
        if not low <= value <= high:
            raise BuildError(f"{STAT_LABELS[key]} doit être entre {low} et {high}.")
        result[key] = value
    return values(result)


def format_stats(rows) -> str:
    return "\n".join(f"{STAT_LABELS[row.stat]}: {row.value}" for row in rows)


def update_capital(profile: Profile, stat: str, spent: int, scrolled: int) -> Profile:
    if profile.mode != "rules":
        raise BuildError("Profil en statistiques nues : utilise son formulaire dédié ou repasse en calcul par paliers.")
    if stat not in PRIMARY:
        raise BuildError("Cette caractéristique ne peut pas recevoir de capital.")
    allocation, scroll = as_stats(profile.allocated), as_stats(profile.scrolled)
    allocation[stat], scroll[stat] = spent, scrolled
    return revised(profile, allocated=values({k: v for k, v in allocation.items() if v}),
                   scrolled=values({k: v for k, v in scroll.items() if v}))


def edit_jets(template, instance, changes):
    """Complète toutes les lignes ; un over n'est jamais ajouté comme exo."""
    definitions = {effect.ref: effect for effect in template.effects if effect.kind == "stat"}
    previous = {effect.ref: effect.high for effect in definitions.values()}
    previous.update({value.ref: value.value for value in instance.final_values})
    if set(changes) - definitions.keys():
        raise BuildError("Ligne de jet inconnue.")
    previous.update(changes)
    if instance.mode != "declared_fm":
        if any(not definitions[ref].low <= value <= definitions[ref].high for ref, value in previous.items()):
            raise BuildError("Jet hors plage naturelle. Sélectionne FM déclarée avant de saisir un over.")
    mode = "declared_fm" if instance.mode == "declared_fm" else "natural_custom"
    return revised(instance, mode=mode,
                   final_values=tuple(EffectValue(ref=ref, value=value) for ref, value in previous.items()))
