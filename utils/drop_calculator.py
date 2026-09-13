"""Calcul de prospection Rétro, distinct du partage du butin entre joueurs.

Les taux sont exprimés en pourcentage, jamais en fractions de probabilité.
Les règles, exceptions et limites du modèle sont détaillées dans docs/PROSPECTION.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
import re
from typing import TYPE_CHECKING
import unicodedata

if TYPE_CHECKING:
    from utils.xixou_api import DropSource


MAX_PERSONAL_PP = 10_000
MAX_GROUP_PP = 100_000
HUNDRED = Decimal(100)
_RATE_PATTERN = re.compile(r"[0-9]+(?:[.,][0-9]+)?(?:[eE][+-]?[0-9]{1,3})?")


@dataclass(frozen=True)
class ProspectingSettings:
    """Une PP totale inclut le personnage ; son absence ne signifie pas « solo »."""

    personal_pp: int
    group_pp: int | None = None

    def __post_init__(self) -> None:
        if type(self.personal_pp) is not int or not 0 <= self.personal_pp <= MAX_PERSONAL_PP:
            raise ValueError("La prospection personnelle doit être un entier entre 0 et 10 000.")
        if self.group_pp is not None:
            if type(self.group_pp) is not int or not 0 <= self.group_pp <= MAX_GROUP_PP:
                raise ValueError("La prospection du groupe doit être un entier entre 0 et 100 000.")
            if self.group_pp < self.personal_pp:
                raise ValueError("La PP totale du groupe doit inclure ta PP personnelle.")


def parse_settings(personal: str, group: str) -> ProspectingSettings | None:
    """Deux champs vides restaurent les taux de base sans convertir une erreur en zéro."""
    personal, group = personal.strip(), group.strip()
    if not personal:
        if group:
            raise ValueError("Renseigne ta PP personnelle, ou vide les deux champs pour réinitialiser.")
        return None
    if not re.fullmatch(r"[0-9]{1,5}", personal):
        raise ValueError("La prospection personnelle doit être un entier entre 0 et 10 000.")
    if group and not re.fullmatch(r"[0-9]{1,6}", group):
        raise ValueError("La prospection du groupe doit être un entier entre 0 et 100 000.")
    return ProspectingSettings(int(personal), int(group) if group else None)


def parse_rate(value: object) -> Decimal | None:
    """Je refuse les valeurs ambiguës et borne la précision avant tout calcul.

    Xixou normalise déjà ses taux ; cette validation protège aussi les appels
    directs et les évolutions de schéma. Un vrai zéro reste différent d'une absence.
    """
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        return None
    if isinstance(value, int) and not 0 <= value <= 100:
        return None
    text = str(value).strip()
    if len(text) > 64:
        return None
    text = text.removesuffix("%").strip()
    if not _RATE_PATTERN.fullmatch(text):
        return None
    rate = Decimal(text.replace(",", "."))
    if not 0 <= rate <= HUNDRED or (rate and rate.adjusted() < -18):
        return None
    return rate


def rate_bounds(drop: DropSource) -> tuple[Decimal, Decimal] | None:
    """Je conserve la plage agrégée même si certains rangs n'ont pas de niveau.

    Aucun taux n'est interpolé. Les valeurs par niveau ne doivent pas masquer
    les autres rangs actifs déjà présents dans la plage publiée par Xixou.
    """
    values = []
    raw = drop.rate
    if isinstance(raw, str) and len(raw) <= 132:
        parts = re.split(r"\s*(?:[–—]|(?<![eE])-|\sà\s)\s*", raw.strip())
        parsed = [parse_rate(part) for part in parts]
        if len(parsed) in (1, 2) and all(value is not None for value in parsed):
            if len(parsed) == 1 or parsed[0] <= parsed[1]:
                values.extend(parsed)
    for _, raw_rate in drop.level_rates:
        rate = parse_rate(raw_rate)
        if rate is not None:
            values.append(rate)
    return (min(values), max(values)) if values else None


def source_count(value: object) -> int | None:
    """Les champs PP et quantité Xixou sont des entiers, pas des pourcentages."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if isinstance(value, int) and not 0 <= value <= 999_999_999:
        return None
    text = str(value).strip()
    return int(text) if re.fullmatch(r"[0-9]{1,9}", text) else None


def threshold_met(settings: ProspectingSettings, required_pp: object) -> bool | None:
    """Un seuil inconnu ou un groupe inconnu sous le seuil reste indéterminé."""
    required = source_count(required_pp)
    if required is None:
        return None
    if settings.personal_pp >= required:
        return True
    if settings.group_pp is None:
        return None
    return settings.group_pp >= required


def personal_rate(
    base_rate: Decimal, settings: ProspectingSettings, *, fixed: bool = False,
) -> Decimal:
    """Taux du jet individuel, avant seuil, quota partagé et bonus de combat.

    La PP est linéaire sur Rétro. La précision locale évite tout arrondi
    intermédiaire, indépendamment du contexte Decimal de l'appelant.
    """
    validated = parse_rate(base_rate)
    if validated is None:
        raise ValueError("Le taux de base doit être un pourcentage valide entre 0 et 100.")
    if fixed:
        return validated
    with localcontext() as context:
        context.prec = max(50, len(validated.as_tuple().digits) + 10)
        return min(HUNDRED, validated * settings.personal_pp / HUNDRED)


def format_percent(rate: Decimal) -> str:
    """Je garde les petits taux visibles : 0,00435 % n'est pas arrondi à 0 %."""
    text = format(rate, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",") + " %"


class DropRule(Enum):
    STANDARD = "standard"
    FIXED = "fixed"
    QUEST = "quest"


def item_drop_rule(category: str, name: str) -> DropRule:
    """Les exceptions reposent sur le type d'objet, jamais sur son taux ou sa PP."""
    def key(text: str) -> str:
        normalized = unicodedata.normalize("NFKD", text.casefold())
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        return " ".join(normalized.replace("-", " ").split())

    category, name = key(category), key(name)
    if category in {"paquet de cartes", "paquets de cartes"}:
        return DropRule.FIXED
    if category in {"bouclier", "boucliers"} and name.startswith("bouclier trophee "):
        return DropRule.FIXED
    if category in {"objet de quete", "objets de quete", "objet de mission", "objets de mission"}:
        return DropRule.QUEST
    return DropRule.STANDARD
