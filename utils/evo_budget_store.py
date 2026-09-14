"""Snapshot budget épinglé, vérifié après écriture, sans remplacement silencieux."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re

import discord

from utils.console_json_store import ConsoleJSONSnapshotStore
from utils.discord_history import fetch_channel_history
from utils.evo_config import EvoError, resolve_console_channel

log = logging.getLogger(__name__)
MARKER = "===BOTEVOBUDGET==="
FILENAME = "evo_budget.json"
MAX_BYTES = 8 * 1024 * 1024


class ConsoleBudgetStore(ConsoleJSONSnapshotStore):
    def __init__(self, bot, guild_id: int):
        super().__init__(bot, marker=MARKER, filename=FILENAME, pin_messages=True)
        self.guild_id = guild_id
        self.message = None
        self.channel_id = None
        pending = getattr(bot, "_evo_budget_safety", None)
        if pending is None:
            pending = {}
            bot._evo_budget_safety = pending
        self.safety = pending.setdefault(guild_id, {"ledger_id": None, "months": set(), "usage": {}})

    async def check_ready(self):
        await self.bot.ensure_evo_leadership()

    def _channel(self):
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            raise EvoError("Le serveur configuré pour le registre budget est indisponible.")
        channel = resolve_console_channel(guild)
        if channel is None or channel.guild.id != self.guild_id:
            raise EvoError("Salon #console introuvable dans le serveur configuré.")
        if self.channel_id not in (None, channel.id):
            raise EvoError("Le salon du registre budget a changé : appels IA bloqués.")
        self.channel_id = channel.id
        return channel

    def _matches(self, message):
        return (getattr(message, "author", None) == self.bot.user
                and (getattr(message, "content", "") or "").startswith(MARKER))

    async def _read_payload(self, message):
        content = message.content
        first_line = content.split("\n", 1)[0]
        header = re.fullmatch(re.escape(MARKER) + r" revision:(\d+) sha256:([0-9a-f]{64})", first_line)
        if header is None:
            raise EvoError("L'en-tête du registre budget est illisible : appels IA bloqués.")
        attachments = list(getattr(message, "attachments", []) or [])
        if attachments:
            if len(attachments) != 1 or attachments[0].filename != FILENAME:
                raise EvoError("La pièce jointe du registre budget est invalide.")
            if getattr(attachments[0], "size", 0) > MAX_BYTES:
                raise EvoError("Le registre budget dépasse la taille autorisée.")
            raw = await attachments[0].read()
        else:
            prefix, suffix = first_line + "\n```json\n", "\n```"
            if not content.startswith(prefix) or not content.endswith(suffix):
                raise EvoError("Le contenu du registre budget est incomplet.")
            raw = content[len(prefix):-len(suffix)].encode("utf-8")
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != header[2]:
            raise EvoError("Le registre budget est incomplet ou altéré : appels IA bloqués.")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise EvoError("Le JSON du registre budget est illisible.") from None
        if (not isinstance(payload, dict) or type(payload.get("revision")) is not int
                or payload["revision"] != int(header[1])):
            raise EvoError("La révision du registre budget est incohérente.")
        return payload

    async def load(self):
        await self.check_ready()
        channel = self._channel()
        try:
            candidates = {}
            if self.message is not None:
                try:
                    current = await channel.fetch_message(self.message.id)
                except discord.NotFound:
                    raise EvoError("Le message du registre budget a disparu : appels IA bloqués.") from None
                if not self._matches(current):
                    raise EvoError("Le message du registre budget a été remplacé.")
                candidates[current.id] = current
            pins = await channel.pins()
            recent = await fetch_channel_history(
                channel, limit=200, reason="evo.budget.restore", raise_errors=True,
            )
            for message in [*pins, *recent]:
                if self._matches(message):
                    candidates[message.id] = message
            if not candidates:
                async for message in channel.history(limit=None):
                    if self._matches(message):
                        candidates[message.id] = message
            if len(candidates) > 1:
                raise EvoError("Plusieurs registres budget existent dans #console : contrôle Staff requis.")
            if not candidates:
                return None
            message = next(iter(candidates.values()))
            payload = await self._read_payload(message)
            await self.check_ready()
            self.message = message
            log.debug("evo budget snapshot read guild_id=%s channel_id=%s message_id=%s revision=%s",
                      self.guild_id, channel.id, message.id, payload["revision"])
            return payload
        except EvoError:
            raise
        except Exception as exc:
            log.debug("evo budget snapshot read failed guild_id=%s channel_id=%s error=%s",
                      self.guild_id, channel.id, type(exc).__name__)
            raise EvoError("Lecture du registre #console impossible : aucun appel IA autorisé.") from None

    async def save(self, payload: dict, *, expected_revision: int | None):
        current = await self.load()
        if expected_revision is None:
            if current is not None:
                raise EvoError("Le registre budget existe déjà : aucune réinitialisation autorisée.")
        elif current is None or current.get("revision") != expected_revision:
            raise EvoError("Le registre budget a changé : relecture nécessaire avant tout appel IA.")
        target_revision = 1 if expected_revision is None else expected_revision + 1
        if payload.get("revision") != target_revision:
            raise EvoError("Révision d'écriture budget invalide.")
        channel = self._channel()
        body = json.dumps(payload, ensure_ascii=False, indent=2).replace("`", "\\u0060")
        raw = body.encode("utf-8")
        if len(raw) > min(MAX_BYTES, getattr(channel.guild, "filesize_limit", MAX_BYTES)):
            raise EvoError("Le registre budget atteint la taille Discord autorisée : appels IA bloqués.")
        header = f"{MARKER} revision:{target_revision} sha256:{hashlib.sha256(raw).hexdigest()}"
        content = f"{header}\n```json\n{body}\n```"
        file = None
        if len(content.encode("utf-16-le")) // 2 > 1950:
            file = discord.File(io.BytesIO(raw), filename=FILENAME)
            content = header
        try:
            await self.check_ready()
            existing = self.message
            if existing is not None:
                if not existing.pinned:
                    await existing.pin(reason="Source de vérité du budget Evo")
                await existing.edit(
                    content=content, attachments=[file] if file else [],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                kwargs = {"file": file} if file else {}
                self.message = await channel.send(
                    content, allowed_mentions=discord.AllowedMentions.none(), **kwargs,
                )
                await self.message.pin(reason="Source de vérité du budget Evo")
            confirmed = await self.load()
            if confirmed != payload:
                raise EvoError("La sauvegarde budget n'a pas été confirmée dans #console.")
            log.debug("evo budget snapshot confirmed guild_id=%s message_id=%s revision=%s",
                      self.guild_id, self.message.id, target_revision)
            return confirmed
        except EvoError:
            raise
        except Exception as exc:
            log.debug("evo budget snapshot write failed guild_id=%s revision=%s error=%s",
                      self.guild_id, target_revision, type(exc).__name__)
            raise EvoError("Sauvegarde budget incertaine dans #console : relecture obligatoire.") from None
        finally:
            if file:
                file.close()

    async def close(self):
        """Le client Discord appartient au bot, aucune ressource locale à supprimer."""
