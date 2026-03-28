from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import timezone
from typing import Any, Optional

import discord

from utils.channel_resolver import resolve_text_channel
from utils.discord_history import fetch_channel_history, fetch_channel_message

log = logging.getLogger(__name__)


class ConsoleJSONSnapshotStore:
    """Generic JSON snapshot persistence backed by the Discord #console channel."""

    def __init__(
        self,
        bot: discord.Client,
        *,
        marker: str,
        filename: str,
        default_channel_name: str = "console",
        channel_id_env: str = "CHANNEL_CONSOLE_ID",
        channel_name_env: str = "CHANNEL_CONSOLE",
        history_limit_env: str = "CONSOLE_HISTORY_LIMIT",
        history_limit_default: int = 200,
        pin_messages: bool = True,
    ) -> None:
        self.bot = bot
        self.marker = marker
        self.filename = filename
        self.default_channel_name = default_channel_name
        self.channel_id_env = channel_id_env
        self.channel_name_env = channel_name_env
        self.history_limit_env = history_limit_env
        self.history_limit_default = history_limit_default
        self.pin_messages = pin_messages

    def _history_limit(self) -> int:
        try:
            return max(
                int(
                    os.getenv(
                        self.history_limit_env,
                        os.getenv("CONSOLE_HISTORY_LIMIT", str(self.history_limit_default)),
                    )
                ),
                0,
            )
        except (TypeError, ValueError):
            return max(self.history_limit_default, 0)

    async def resolve_channel(self, guild: Optional[discord.Guild] = None) -> Optional[discord.TextChannel]:
        target_guilds: list[discord.Guild] = []
        if guild is not None:
            target_guilds.append(guild)
        for existing in list(getattr(self.bot, "guilds", []) or []):
            if existing not in target_guilds:
                target_guilds.append(existing)
        for candidate_guild in target_guilds:
            channel = resolve_text_channel(
                candidate_guild,
                id_env=self.channel_id_env,
                name_env=self.channel_name_env,
                default_name=self.default_channel_name,
            )
            if isinstance(channel, discord.TextChannel):
                return channel
        return None

    def _message_sort_key(self, message: discord.Message) -> tuple[float, int]:
        created_at = getattr(message, "created_at", None)
        if created_at is None:
            return 0.0, int(getattr(message, "id", 0) or 0)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at.timestamp(), int(getattr(message, "id", 0) or 0)

    def _is_snapshot_message(self, message: discord.Message) -> bool:
        author = getattr(message, "author", None)
        bot_user = getattr(self.bot, "user", None)
        if bot_user is not None and author is not None and author != bot_user:
            return False
        content = getattr(message, "content", "") or ""
        if self.marker in content:
            return True
        for attachment in getattr(message, "attachments", []) or []:
            if getattr(attachment, "filename", None) == self.filename:
                return True
        return False

    def _extract_inline_json(self, content: str) -> Optional[dict[str, Any]]:
        if "```json" not in content:
            return None
        try:
            start = content.index("```json") + len("```json")
            if start < len(content) and content[start] == "\n":
                start += 1
            end = content.find("```", start)
            if end == -1:
                return None
            raw_json = content[start:end].strip()
            if not raw_json:
                return None
            data = json.loads(raw_json)
            if isinstance(data, dict):
                return data
        except Exception:
            log.debug("Failed to parse inline JSON snapshot for marker %s.", self.marker, exc_info=True)
        return None

    async def extract_payload(self, message: discord.Message) -> Optional[dict[str, Any]]:
        for attachment in getattr(message, "attachments", []) or []:
            if getattr(attachment, "filename", None) != self.filename:
                continue
            try:
                raw = await attachment.read()
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                log.debug("Failed to read JSON attachment %s.", self.filename, exc_info=True)
        content = getattr(message, "content", "") or ""
        return self._extract_inline_json(content)

    async def _candidate_messages(
        self,
        channel: discord.TextChannel,
        *,
        current_message_id: Optional[int] = None,
    ) -> list[discord.Message]:
        checked: set[int] = set()
        candidates: list[discord.Message] = []

        def add_candidate(message: Optional[discord.Message]) -> None:
            if message is None:
                return
            mid = getattr(message, "id", None)
            if mid is None or mid in checked:
                return
            checked.add(mid)
            candidates.append(message)

        if current_message_id:
            current = await fetch_channel_message(channel, current_message_id, reason=f"{self.marker}.current")
            if current and self._is_snapshot_message(current):
                add_candidate(current)

        try:
            pins = await channel.pins()
        except Exception:
            pins = []
        for message in pins:
            if self._is_snapshot_message(message):
                add_candidate(message)

        limit = self._history_limit()
        if limit > 0:
            history = await fetch_channel_history(channel, limit=limit, reason=f"{self.marker}.history")
            for message in history:
                if self._is_snapshot_message(message):
                    add_candidate(message)

        candidates.sort(key=self._message_sort_key, reverse=True)
        return candidates

    async def load_latest(
        self,
        *,
        guild: Optional[discord.Guild] = None,
        channel: Optional[discord.TextChannel] = None,
        current_message_id: Optional[int] = None,
    ) -> tuple[Optional[discord.Message], Optional[dict[str, Any]]]:
        target_channel = channel or await self.resolve_channel(guild)
        if target_channel is None:
            return None, None
        candidates = await self._candidate_messages(target_channel, current_message_id=current_message_id)
        for message in candidates:
            payload = await self.extract_payload(message)
            if payload is not None:
                return message, payload
        return None, None

    async def _ensure_pinned(self, message: discord.Message) -> None:
        if not self.pin_messages:
            return
        try:
            if not getattr(message, "pinned", False):
                await message.pin(reason=f"Snapshot {self.filename}")
        except Exception:
            log.debug("Unable to pin snapshot message for %s.", self.marker, exc_info=True)

    def _dump_payload(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, indent=4, ensure_ascii=False, sort_keys=True)

    def _etag(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.md5(body.encode("utf-8")).hexdigest()

    async def save(
        self,
        payload: dict[str, Any],
        *,
        guild: Optional[discord.Guild] = None,
        channel: Optional[discord.TextChannel] = None,
        current_message_id: Optional[int] = None,
    ) -> Optional[discord.Message]:
        target_channel = channel or await self.resolve_channel(guild)
        if target_channel is None:
            return None

        existing, _ = await self.load_latest(
            channel=target_channel,
            current_message_id=current_message_id,
        )
        data_str = self._dump_payload(payload)
        header = f"{self.marker} etag:{self._etag(payload)}"

        if len(data_str) < 1900:
            content = f"{header}\n```json\n{data_str}\n```"
            try:
                if existing is not None:
                    await existing.edit(content=content, attachments=[])
                    await self._ensure_pinned(existing)
                    return existing
                message = await target_channel.send(content)
                await self._ensure_pinned(message)
                return message
            except Exception:
                log.debug("Inline snapshot save failed for %s; retrying with new message.", self.marker, exc_info=True)
                if existing is not None:
                    try:
                        await existing.delete()
                    except Exception:
                        pass
                message = await target_channel.send(content)
                await self._ensure_pinned(message)
                return message

        tmp = tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8", suffix=".json")
        try:
            tmp.write(data_str)
            tmp.flush()
            tmp.close()
            if existing is not None:
                try:
                    await existing.delete()
                except Exception:
                    pass
            message = await target_channel.send(
                f"{header} (fichier)",
                file=discord.File(tmp.name, filename=self.filename),
            )
            await self._ensure_pinned(message)
            return message
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
