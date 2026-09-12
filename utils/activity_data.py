"""Activity validation and migrations, independent of Discord and external services."""

from __future__ import annotations

import copy
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
DEFAULT_CAPACITY = 8
MAX_CAPACITY = 100
MAX_WAITLIST = 100
SCHEMA_VERSION = 2


class ActivityError(ValueError):
    """An actionable error that can safely be displayed to a guild member."""


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=PARIS)
    return value.astimezone(timezone.utc)


def clean_text(value: object, maximum: int, label: str, *, required: bool = False) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    text = "".join(c for c in text if c in "\n\t" or
                   unicodedata.category(c) not in {"Cc", "Cf", "Cs"})
    if required and not text:
        raise ActivityError(f"Renseigne {label}.")
    if len(text.encode("utf-16-le")) // 2 > maximum:
        raise ActivityError(f"{label.capitalize()} : {maximum} caractères maximum.")
    return text


def parse_activity_when(value: str, *, now: datetime | None = None) -> datetime:
    """Parse explicit French dates without probabilistic natural-language inference."""
    now = utc(now or datetime.now(timezone.utc)).astimezone(PARIS)
    text = " ".join(value.lower().replace("’", "'").strip().split())
    match = re.fullmatch(r"(.+?)\s+(\d{1,2})(?::|h)(\d{2})?", text)
    if not match:
        raise ActivityError(
            "Indique par exemple « demain 21h », « vendredi 20h30 » "
            "ou « 18/09/2026 21:00 » (heure de Paris)."
        )
    day_text, hour, minute = match.groups()
    if text.endswith(":"):
        raise ActivityError("Précise les minutes après les deux-points, par exemple 21:00.")
    if int(hour) > 23 or int(minute or 0) > 59:
        raise ActivityError("L'heure doit être comprise entre 00:00 et 23:59.")
    weekdays = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    if day_text in {"aujourd'hui", "aujourdhui", "demain", "après-demain", "apres-demain"}:
        delta = {"demain": 1, "après-demain": 2, "apres-demain": 2}.get(day_text, 0)
        day = now.date() + timedelta(days=delta)
    elif day_text in weekdays:
        delta = (weekdays.index(day_text) - now.weekday()) % 7
        day = now.date() + timedelta(days=delta)
        if delta == 0 and (int(hour), int(minute or 0)) <= (now.hour, now.minute):
            day += timedelta(days=7)
    else:
        date_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", day_text)
        if not date_match:
            raise ActivityError("Date inconnue. Utilise JJ/MM/AAAA, demain ou un jour de la semaine.")
        d, m, year = date_match.groups()
        try:
            day = datetime(int(year or now.year), int(m), int(d)).date()
        except ValueError as exc:
            raise ActivityError("Cette date n'existe pas.") from exc
        if year is None and day < now.date():
            raise ActivityError("Cette date est passée cette année. Précise l'année souhaitée.")
    if day.year > 2100:
        raise ActivityError("Choisis une date au plus tard en 2100.")
    naive = datetime(day.year, day.month, day.day, int(hour), int(minute or 0))
    local = naive.replace(tzinfo=PARIS)
    if utc(local).astimezone(PARIS).replace(tzinfo=None) != naive:
        raise ActivityError("Cette heure n'existe pas à Paris lors du passage à l'heure d'été.")
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ActivityError(
            "Cette heure existe deux fois lors du changement d'heure. "
            "Choisis un horaire non ambigu, par exemple 01:30 ou 03:00."
        )
    if utc(local) <= utc(now):
        raise ActivityError("Choisis une date et une heure à venir, à l'heure de Paris.")
    return local


def validate_draft(values: dict, *, now: datetime | None = None) -> dict:
    title = clean_text(values.get("titre"), 85, "le titre", required=True)
    if "\n" in title:
        raise ActivityError("Le titre doit tenir sur une seule ligne.")
    location = clean_text(values.get("lieu"), 120, "le lieu ou rendez-vous")
    description = clean_text(values.get("description"), 1500, "la description")
    capacity_value = values.get("capacite")
    raw_capacity = str(DEFAULT_CAPACITY if capacity_value in (None, "") else capacity_value).strip()
    if not re.fullmatch(r"[0-9]{1,3}", raw_capacity):
        raise ActivityError("Le nombre de places doit être un entier entre 1 et 100.")
    capacity = int(raw_capacity)
    if not 1 <= capacity <= MAX_CAPACITY:
        raise ActivityError("Choisis entre 1 et 100 places, organisateur compris.")
    starts = parse_activity_when(str(values.get("date") or ""), now=now)
    return {
        "titre": title,
        "date_str": starts.strftime("%Y-%m-%d %H:%M:%S"),
        "starts_at": utc(starts).isoformat(),
        "lieu": location,
        "description": description,
        "capacity": capacity,
    }


def unique_ids(values) -> list[int]:
    if not isinstance(values, (list, tuple)):
        raise ActivityError("Liste de membres invalide dans la sauvegarde.")
    result = []
    for value in values:
        identifier = int(value)
        if identifier <= 0:
            raise ActivityError("Identifiant de membre invalide dans la sauvegarde.")
        if identifier not in result:
            result.append(identifier)
    return result


class ActiviteData:
    """Backward-compatible record with lossless extension fields and an explicit UTC instant."""

    def __init__(self, i, t, dt, desc, cid, rid=None,
                 reminder_24_sent=False, reminder_1_sent=False):
        self.id = str(i)
        self.titre = t
        self.date_obj = dt
        self.description = desc
        self.creator_id = int(cid)
        self.role_id = rid
        self.participants = []
        self.cancelled = False
        self.reminder_24_sent = reminder_24_sent
        self.reminder_1_sent = reminder_1_sent
        self.extra = {"waitlist": []}

    @property
    def capacity(self) -> int:
        return int(self.extra.get("capacity", DEFAULT_CAPACITY))

    @property
    def waitlist(self) -> list[int]:
        return self.extra.setdefault("waitlist", [])

    def to_dict(self) -> dict:
        local = utc(self.date_obj).astimezone(PARIS)
        return {
            **copy.deepcopy(self.extra),
            "id": self.id, "titre": self.titre,
            "date_str": local.strftime("%Y-%m-%d %H:%M:%S"),
            "starts_at": utc(self.date_obj).isoformat(),
            "description": self.description, "creator_id": self.creator_id,
            "role_id": self.role_id, "participants": list(self.participants),
            "cancelled": self.cancelled,
            "reminder_24_sent": self.reminder_24_sent,
            "reminder_1_sent": self.reminder_1_sent,
        }

    @staticmethod
    def from_dict(data: dict) -> ActiviteData:
        starts = datetime.fromisoformat(data.get("starts_at") or data["date_str"])
        event = ActiviteData(
            data["id"], data["titre"], starts, data.get("description", ""),
            data["creator_id"], data.get("role_id"),
            data.get("reminder_24_sent", False), data.get("reminder_1_sent", False),
        )
        event.participants = unique_ids(data.get("participants", []))
        event.cancelled = bool(data.get("cancelled", False))
        standard = {
            "id", "titre", "date_str", "starts_at", "description", "creator_id", "role_id",
            "participants", "cancelled", "reminder_24_sent", "reminder_1_sent",
        }
        event.extra = {k: copy.deepcopy(v) for k, v in data.items() if k not in standard}
        event.extra["waitlist"] = [
            uid for uid in unique_ids(data.get("waitlist", [])) if uid not in event.participants
        ]
        return event


def migrate_snapshot(payload: dict, guild_id: int | None) -> dict:
    """Preserve unknown fields and quarantine malformed records instead of discarding them."""
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), dict):
        raise ActivityError("Sauvegarde d'activités invalide : objet events manquant.")
    if int(payload.get("schema_version", 1)) > SCHEMA_VERSION:
        raise ActivityError("Sauvegarde plus récente que le bot : mets le bot à jour avant de la lire.")
    migrated = copy.deepcopy(payload)
    quarantined = migrated.setdefault("quarantine", {})
    if not isinstance(quarantined, dict):
        raise ActivityError("La quarantaine de la sauvegarde est invalide.")
    events = {}
    next_id = 1
    for key, record in payload["events"].items():
        key = str(key)
        if key.isascii() and key.isdigit():
            next_id = max(next_id, int(key) + 1)
        try:
            if not re.fullmatch(r"[1-9][0-9]{0,19}", key) or not isinstance(record, dict):
                raise ActivityError("Identifiant ou activité invalide.")
            event = ActiviteData.from_dict({**record, "id": key})
            if not 1 <= event.capacity <= MAX_CAPACITY:
                raise ActivityError("Capacité invalide.")
            data = event.to_dict()
            data.setdefault("guild_id", guild_id)
            data.setdefault("capacity", DEFAULT_CAPACITY)
            data.setdefault("lieu", "")
            data.setdefault("revision", 0)
            events[key] = data
        except (KeyError, TypeError, ValueError, OverflowError):
            quarantined[key] = copy.deepcopy(record)
    migrated.update({
        "schema_version": SCHEMA_VERSION,
        "events": events,
        "next_id": max(next_id, int(payload.get("next_id", 1))),
    })
    return migrated


def change_roster(record: dict, user_id: int, action: str, *, now: datetime) -> tuple[str, list[int]]:
    """Mutate a candidate snapshot only; the caller serialises and durably commits it."""
    participants = record.setdefault("participants", [])
    waitlist = record.setdefault("waitlist", [])
    if action not in {"join", "leave"}:
        raise ActivityError("Action d'inscription inconnue.")
    if record.get("cancelled"):
        raise ActivityError("Activité annulée.")
    starts = datetime.fromisoformat(record.get("starts_at") or record["date_str"])
    if utc(starts) <= utc(now):
        raise ActivityError("Le début de cette activité est déjà passé ; la liste est conservée.")
    capacity = int(record.get("capacity", DEFAULT_CAPACITY))
    if action == "join":
        if user_id in participants:
            raise ActivityError("Déjà inscrit.")
        if user_id in waitlist:
            raise ActivityError(f"Déjà en liste d'attente, position {waitlist.index(user_id) + 1}.")
        if len(participants) < capacity and not waitlist:
            participants.append(user_id)
            return "rejoint l'activité.", []
        if len(waitlist) >= MAX_WAITLIST:
            raise ActivityError("La liste d'attente est complète.")
        waitlist.append(user_id)
        return f"est en liste d'attente, position {len(waitlist)}.", []
    if user_id in waitlist:
        waitlist.remove(user_id)
        return "quitte la liste d'attente.", []
    if user_id not in participants:
        raise ActivityError("Pas inscrit sur cet événement.")
    participants.remove(user_id)
    promoted = []
    while waitlist and len(participants) < capacity:
        member_id = waitlist.pop(0)
        if member_id not in participants:
            participants.append(member_id)
            promoted.append(member_id)
    return "se retire de l'activité.", promoted


def apply_draft(record: dict, draft: dict, *, revision: int | None = None) -> None:
    if record.get("cancelled"):
        raise ActivityError("Cette activité est annulée.")
    if revision is not None and revision != int(record.get("revision", 0)):
        raise ActivityError("La fiche a changé entre-temps. Rouvre Modifier pour ne rien écraser.")
    if draft["capacity"] < len(record.get("participants", [])):
        raise ActivityError("La capacité ne peut pas être inférieure au nombre de membres inscrits.")
    previous_start = utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
    record.update(draft)
    record["revision"] = int(record.get("revision", 0)) + 1
    if previous_start != utc(datetime.fromisoformat(draft["starts_at"])):
        record["reminder_24_sent"] = record["reminder_1_sent"] = False
        record["closed"] = False
