"""Pinned console snapshots with fail-closed reads and non-destructive writes."""

from __future__ import annotations

import io
import json
import logging

import discord

from utils.activity_data import ActivityError
from utils.console_json_store import ConsoleJSONSnapshotStore
from utils.discord_history import fetch_channel_history

log = logging.getLogger(__name__)
MARKER = "===BOTACTIVITES==="


class ActivitySnapshotStore(ConsoleJSONSnapshotStore):
    def __init__(self, bot):
        super().__init__(
            bot, marker=MARKER, filename="activities_data.json",
            history_limit_env="ACTIVITE_HISTORY_LIMIT", pin_messages=True,
        )
        self.message = None
        self.channel_id = None

    def _matches(self, message) -> bool:
        return (
            getattr(message, "author", None) == self.bot.user
            and MARKER in (getattr(message, "content", "") or "")
        )

    def _extract_inline_json(self, content: str) -> dict | None:
        if "```json" not in content:
            return None
        raw = content.split("```json", 1)[1].lstrip()
        try:
            payload, end = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict) or raw[end:].strip() != "```":
            return None
        return payload

    async def load(self, channel) -> dict | None:
        """Never interpret a refused history request or a damaged snapshot as an empty database."""
        pins = await channel.pins()
        recent = await fetch_channel_history(
            channel, limit=max(self._history_limit(), 200),
            reason="activities.restore", raise_errors=True,
        )
        candidates = {
            message.id: message for message in [*pins, *recent] if self._matches(message)
        }
        if not candidates:
            async for message in channel.history(limit=None):
                if self._matches(message):
                    candidates[message.id] = message
                    break
        if not candidates:
            self.message = None
            self.channel_id = channel.id
            return None
        message = max(candidates.values(), key=lambda item: (
            getattr(item, "edited_at", None) or item.created_at, item.id,
        ))
        payload = await self.extract_payload(message)
        if payload is None:
            raise ActivityError(
                "Le dernier snapshot d'activités dans #console est illisible. "
                "Restaure-le avant de reprendre les écritures."
            )
        self.message = message
        self.channel_id = channel.id
        log.debug("Activities restored channel_id=%s message_id=%s", channel.id, message.id)
        return payload

    async def persist(self, channel, payload: dict):
        """Keep the previous message on any failure other than a confirmed deletion."""
        if self.channel_id not in (None, channel.id):
            raise ActivityError("La console des activités ne peut pas changer pendant une écriture.")
        body = json.dumps(payload, ensure_ascii=False, indent=2).replace("`", "\\u0060")
        header = f"{MARKER} etag:{self._etag(payload)}"
        content = f"{header}\n```json\n{body}\n```"
        file = None
        if len(content.encode("utf-16-le")) // 2 > 1950:
            raw = body.encode("utf-8")
            if len(raw) > getattr(channel.guild, "filesize_limit", 8 * 1024 * 1024):
                raise ActivityError("Le snapshot dépasse la limite Discord. Contacte le Staff.")
            file = discord.File(io.BytesIO(raw), filename=self.filename)
            content = f"{header} (fichier)"
        existing = self.message
        created = False
        try:
            if existing is not None:
                try:
                    if not existing.pinned:
                        await existing.pin(reason="Source de vérité des activités")
                    edited = await existing.edit(
                        content=content, attachments=[file] if file else [],
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.NotFound:
                    self.message = None
                    if file:
                        file.reset(seek=True)
                else:
                    self.message = edited or existing
            if self.message is None:
                kwargs = {"file": file} if file else {}
                self.message = await channel.send(
                    content, allowed_mentions=discord.AllowedMentions.none(), **kwargs,
                )
                created = True
            self.channel_id = channel.id
            if not self.message.pinned:
                try:
                    await self.message.pin(reason="Source de vérité des activités")
                except discord.HTTPException:
                    if created:
                        uncommitted = self.message
                        self.message = None
                        try:
                            await uncommitted.delete()
                        except discord.HTTPException:
                            log.error("Unpinned activity snapshot could not be rolled back", exc_info=True)
                    raise
            log.debug("Activities committed channel_id=%s message_id=%s",
                      channel.id, self.message.id)
            return self.message
        except discord.HTTPException as exc:
            log.warning("Activities console write failed; previous snapshot not deleted",
                        exc_info=True)
            raise ActivityError(
                "Sauvegarde #console impossible. Vérifie Lecture de l'historique, "
                "Envoi, Pièces jointes et Gestion des messages (épinglage). "
                "La demande n'est pas confirmée ; réessaie après correction."
            ) from exc
        finally:
            if file:
                file.close()
