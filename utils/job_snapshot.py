"""Lecture des sauvegardes métiers, sans dépendance au transport Discord.

Le format historique est conservé : dictionnaire de fiches, JSON inline ou
pièce jointe jobs_data.json. Un aperçu ne remplace jamais un fichier illisible.
"""
from __future__ import annotations

from datetime import timezone
import hashlib
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord

MARKER = "===BOTJOBS==="
FILENAME = "jobs_data.json"
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


class InvalidJobSnapshot(ValueError):
    """La source existe, mais son contenu ne peut pas être utilisé sans risque."""


def validate_jobs_payload(payload: object) -> dict:
    """Valider le conteneur sans renommer, fusionner ou attribuer les fiches."""
    if not isinstance(payload, dict) or any(
        not isinstance(row, dict) or not isinstance(row.get("jobs", {}), dict)
        for row in payload.values()
    ):
        raise InvalidJobSnapshot("Le format du registre métiers est invalide.")
    return payload


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidJobSnapshot("Le JSON métiers contient une clé dupliquée.")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise InvalidJobSnapshot("Le JSON métiers contient un nombre non JSON.")


def decode_jobs_payload(raw: bytes) -> dict:
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise InvalidJobSnapshot("La sauvegarde métiers dépasse 8 Mio.")
    try:
        payload = json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InvalidJobSnapshot("La sauvegarde métiers n'est pas un JSON UTF-8 complet.") from exc
    return validate_jobs_payload(payload)


class JobSnapshotReader:
    """Même contrat de décodage pour consultation, mutation et confirmation."""

    marker = MARKER
    filename = FILENAME

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    def _is_snapshot_message(self, message: discord.Message) -> bool:
        bot_id = getattr(getattr(self.bot, "user", None), "id", None)
        author_id = getattr(getattr(message, "author", None), "id", None)
        if bot_id is None or author_id != bot_id or getattr(message, "webhook_id", None):
            return False
        content = (getattr(message, "content", "") or "").lstrip()
        return content.startswith(MARKER) or any(
            getattr(attachment, "filename", None) == FILENAME
            for attachment in getattr(message, "attachments", ())
        )

    @staticmethod
    def version_key(message: discord.Message) -> tuple[float, int]:
        updated_at = getattr(message, "edited_at", None) or getattr(message, "created_at", None)
        if updated_at is None:
            return 0.0, message.id
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return updated_at.timestamp(), message.id

    async def extract_payload(self, message: discord.Message) -> dict | None:
        if not self._is_snapshot_message(message):
            return None
        content = getattr(message, "content", "") or ""
        attachments = [
            attachment for attachment in getattr(message, "attachments", ())
            if getattr(attachment, "filename", None) == FILENAME
        ]
        if attachments:
            if len(attachments) != 1:
                raise InvalidJobSnapshot("Plusieurs fichiers métiers rendent la sauvegarde ambiguë.")
            attachment = attachments[0]
            if getattr(attachment, "size", 0) > MAX_SNAPSHOT_BYTES:
                raise InvalidJobSnapshot("La sauvegarde métiers dépasse 8 Mio.")
            # Le nom du fichier fait foi, pas la présence du mot « fichier ».
            # Les erreurs réseau remontent : aucun repli sur un aperçu partiel.
            payload = decode_jobs_payload(await attachment.read())
        else:
            if "(fichier)" in content:
                raise InvalidJobSnapshot("Le fichier métiers annoncé n'est plus attaché au message.")
            block = re.search(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", content, re.DOTALL)
            if block is None:
                raise InvalidJobSnapshot("La sauvegarde métiers ne contient aucun JSON complet.")
            try:
                raw = block[1].encode("utf-8")
            except UnicodeError as exc:
                raise InvalidJobSnapshot("Le texte métiers n'est pas un UTF-8 valide.") from exc
            payload = decode_jobs_payload(raw)
        # Les sauvegardes anciennes sans etag restent lisibles. Lorsqu'il existe,
        # vérifier celui produit par le bot évite d'accepter une pièce jointe obsolète.
        header = content.splitlines()[0] if content else ""
        etag = re.search(r"\betag:([^\s]+)", header)
        try:
            canonical = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        except (UnicodeError, RecursionError) as exc:
            raise InvalidJobSnapshot("Le contenu métiers n'est pas exportable.") from exc
        if etag is not None and etag[1].lower() != hashlib.md5(canonical).hexdigest():
            raise InvalidJobSnapshot("L'empreinte etag de la sauvegarde métiers ne correspond pas.")
        return payload
