"""Authenticated references for the resources an event may change.

Only immutable action inputs are signed: participant lists and Discord message
objects are deliberately outside the projection.  All callers fail closed when
the key or a signed field is absent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import uuid


class EventSecurityError(ValueError):
    """An event cannot safely mutate Discord resources."""


def provenance_key() -> bytes:
    value = os.getenv("EVENT_PROVENANCE_KEY", "").strip()
    if len(value) != 64:
        raise EventSecurityError("Les actions événements sont suspendues : clé de provenance absente ou invalide.")
    try:
        key = bytes.fromhex(value)
    except ValueError:
        raise EventSecurityError("La clé de provenance des événements est invalide.") from None
    if len(key) != 32:
        raise EventSecurityError("La clé de provenance des événements est invalide.")
    return key


def utc_datetime(value: datetime | str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise EventSecurityError("L'échéance de l'événement est invalide.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventSecurityError("L'échéance de l'événement doit préciser son fuseau horaire.")
    return parsed.astimezone(timezone.utc)


def _snowflake(value) -> int:
    if isinstance(value, bool):
        raise EventSecurityError("Identifiant de ressource invalide.")
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        raise EventSecurityError("Identifiant de ressource invalide.") from None
    if str(value) != str(result) or not 0 < result < 2**64:
        raise EventSecurityError("Identifiant de ressource invalide.")
    return result


def action_projection(record: dict) -> dict:
    try:
        event_uuid = str(uuid.UUID(record["event_uuid"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise EventSecurityError("L'événement ne possède pas de provenance vérifiable.") from None
    if not isinstance(record.get("cleanup_enabled"), bool):
        raise EventSecurityError("L'autorisation de nettoyage de l'événement est invalide.")
    return {
        "version": 1,
        "event_uuid": event_uuid,
        **{field: _snowflake(record.get(field)) for field in (
            "guild_id", "event_id", "channel_id", "message_id", "role_id",
            "event_channel_id", "category_id",
        )},
        "ends_at": utc_datetime(record.get("ends_at")).isoformat(timespec="microseconds"),
        "cleanup_enabled": record["cleanup_enabled"],
    }


def _encoded_projection(record: dict) -> bytes:
    return json.dumps(action_projection(record), sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_event(record: dict) -> dict:
    key = provenance_key()
    signature = hmac.new(key, _encoded_projection(record), hashlib.sha256).hexdigest()
    return {**record, "provenance": {"version": 1, "signature": signature}}


def verify_event(record: dict) -> None:
    key = provenance_key()
    proof = record.get("provenance")
    if not isinstance(proof, dict) or proof.get("version") != 1:
        raise EventSecurityError("Ancien événement en lecture seule : revalidation Staff nécessaire.")
    signature = proof.get("signature")
    if (not isinstance(signature, str) or len(signature) != 64
            or any(character not in "0123456789abcdef" for character in signature)):
        raise EventSecurityError("La preuve de provenance de l'événement est invalide.")
    expected = hmac.new(key, _encoded_projection(record), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise EventSecurityError("La preuve de provenance de l'événement est invalide.")


def resource_marker(record: dict) -> str:
    try:
        return f"evolution:event:{uuid.UUID(record['event_uuid'])}"
    except (KeyError, ValueError, TypeError, AttributeError):
        raise EventSecurityError("Identifiant interne d'événement invalide.") from None
