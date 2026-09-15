"""Deterministic request-bound validation. No Discord, network or model dependency."""
from __future__ import annotations

import re
import unicodedata


def words(value: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_POLITE = (r"^(?:(?:bonjour|salut|evo|stp|svp|s il te plait) )*"
           r"(?:(?:peux tu|tu peux|pourrais tu|tu pourrais|je veux que tu) )?")
_CREATE = re.compile(_POLITE + r"(?:me |m )?(?:cree|creer|organise|organiser|planifie|planifier)\b")
_CONDITIONAL = re.compile(
    r"\b(?:si|sinon|hypothese|supposons|exemple|citation|cite|disait|rapporte|"
    r"ne|pas|jamais|comment|pourquoi)\b"
)
_WHEN = re.compile(
    r"(?<![\w/])(?:aujourd['’]?hui|demain|apr[eè]s-demain|"
    r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|"
    r"\d{1,2}/\d{1,2}(?:/\d{4})?)\s+(?:[àa]\s+)?"
    r"\d{1,2}(?::\d{2}|h(?:\d{2})?)(?!\w)", re.I,
)


def normalized_when(value: str) -> str:
    text = " ".join(value.casefold().replace("’", "'").split())
    return re.sub(r"\s+[àa]\s+", " ", text)


def creation_values(request: str, *, title: str, when: str, description: str,
                    location: str, capacity: int | None, duration: int | None) -> dict:
    """The model extracts fields; the current user's text authorizes every field.

    No authority from previous turns, web content or tool output. No guessed
    calendar date, invented title, mention, capacity or duration.
    """
    key = words(request)
    if (not _CREATE.match(key) or _CONDITIONAL.search(key)
            or request.lstrip().startswith(("'", '"', "«", ">", "`"))
            or re.search(r"<[@#]|@(?:everyone|here)|```", request, re.I)):
        raise ValueError("Formule une demande directe de création, sans condition ni mention.")
    if not re.search(r"\b(?:activite|sortie|donjon|evenement)\b", key):
        raise ValueError("Précise que tu souhaites créer une activité ou une sortie.")
    for label, value in (("titre", title), ("description", description), ("lieu", location)):
        normalized = words(value)
        if (label == "titre" and not normalized) or (
            normalized and not re.search(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)", key)
        ):
            raise ValueError(f"Le {label} doit être repris de ta demande, sans information ajoutée.")
    dates = {normalized_when(match.group()) for match in _WHEN.finditer(request)}
    if len(dates) != 1 or normalized_when(when) not in dates:
        raise ValueError("Précise une seule date avec son heure, par exemple « demain à 21h ».")
    capacities = {int(n) for n in re.findall(
        r"\b(\d{1,3})\s+(?:places?|personnes?|joueurs?|membres?)\b", key
    )}
    capacities.update(int(n) for n in re.findall(r"\bcapacite\s+(?:de\s+)?(\d{1,3})\b", key))
    if len(capacities) > 1:
        raise ValueError("Précise un seul nombre de places.")
    actual_capacity = next(iter(capacities), 8)
    if capacity is not None and (type(capacity) is not int or capacity != actual_capacity):
        raise ValueError("Le nombre de places ne correspond pas à ta demande.")
    durations = set()
    for match in re.finditer(
        r"\b(?:pendant|duree(?: de)?)\s+(\d{1,4})\s*(h(?:eures?)?|min(?:utes?)?)(?:\s*(\d{2}))?\b",
        "".join(c for c in unicodedata.normalize("NFKD", request.casefold())
                if not unicodedata.combining(c)),
    ):
        if match[3] and (not match[2].startswith("h") or int(match[3]) >= 60):
            raise ValueError("Durée invalide : indique par exemple 2h30 ou 150 minutes.")
        value = int(match[1]) * (60 if match[2].startswith("h") else 1)
        durations.add(value + int(match[3] or 0))
    if len(durations) > 1:
        raise ValueError("Précise une seule durée.")
    actual_duration = next(iter(durations), 180)
    if duration is not None and (type(duration) is not int or duration != actual_duration):
        raise ValueError("La durée ne correspond pas à ta demande.")
    return {
        "titre": title.strip(), "date": next(iter(dates)),
        "description": description.strip(), "lieu": location.strip(),
        "capacite": actual_capacity, "duree": actual_duration,
    }
