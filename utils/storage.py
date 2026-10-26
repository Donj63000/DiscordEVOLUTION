import os
import json
import logging
import asyncio
import io
from typing import Dict, Optional

from models import EventData

import discord
from discord.ext import commands
from utils.channel_resolver import resolve_text_channel
from utils.discord_history import fetch_channel_history
from utils.guild_scope import allowed_guilds, require_guild, is_allowed_guild

logger = logging.getLogger(__name__)
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", os.getenv("STAFF_ROLE_NAME", "Staff"))


class EventStore:
    """Simple storage helper for events and conversations."""

    MARKER = "===EVENTSTORE==="

    def __init__(self, bot: commands.Bot, console_channel: str = "console"):
        self.bot = bot
        self.console_channel_name = console_channel
        self.console_channel: Optional[discord.TextChannel] = None
        self.backend = None
        self.db = None
        self.events: Dict[str, EventData] = {}
        self.conversations: Dict[str, list] = {}
        self._write_lock = asyncio.Lock()
        self._console_auto_create = (os.getenv("CONSOLE_AUTO_CREATE", "1") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    async def _resolve_console_channel(self) -> Optional[discord.TextChannel]:
        default_name = os.getenv("CHANNEL_CONSOLE", self.console_channel_name or "console")
        for guild in allowed_guilds(self.bot):
            channel = resolve_text_channel(
                guild,
                id_env="CHANNEL_CONSOLE_ID",
                name_env="CHANNEL_CONSOLE",
                default_name=default_name,
            )
            if channel:
                return channel
        if not self._console_auto_create:
            return None
        if os.getenv("CHANNEL_CONSOLE_ID"):
            return None
        for guild in allowed_guilds(self.bot):
            me = guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)
            if not me or not getattr(me.guild_permissions, "manage_channels", False):
                continue
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    read_messages=False,
                    send_messages=False,
                    read_message_history=False,
                ),
                me: discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    manage_messages=True,
                    read_message_history=True,
                ),
            }
            staff_role = discord.utils.find(
                lambda role: getattr(role, "name", None) == STAFF_ROLE_NAME,
                getattr(guild, "roles", []) or [],
            )
            if staff_role is not None:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True,
                )
            try:
                return await guild.create_text_channel(
                    default_name,
                    overwrites=overwrites,
                    reason="Console persistence channel",
                )
            except (discord.Forbidden, discord.HTTPException):
                continue
        return None

    async def connect(self):
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            self.backend = "postgres"
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError(
                    "DATABASE_URL is set but asyncpg is not installed. Add asyncpg to requirements.txt."
                ) from exc
            try:
                self.db = await asyncpg.create_pool(dsn=db_url)
                await self._init_db()
                logger.info("EventStore connected to PostgreSQL.")
                return
            except Exception as exc:
                raise RuntimeError(f"PostgreSQL configured but unavailable: {exc}") from exc
        self.backend = "console"
        self.console_channel = await self._resolve_console_channel()
        if not self.console_channel:
            logger.warning(
                "Console channel not found; persistence disabled. "
                "Set CHANNEL_CONSOLE_ID or create #console."
            )

    async def _init_db(self):
        if not self.db:
            return
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                data JSONB
            )
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                data JSONB
            )
            """
        )

    async def load(self):
        if self.backend == "postgres" and self.db:
            await self._load_db()
        else:
            await self._load_console()
        return {"events": self.events, "conversations": self.conversations}

    async def _load_db(self):
        rows = await self.db.fetch("SELECT id, data FROM events")
        self.events = {}
        for row in rows:
            payload = self._json_value(row["data"])
            if isinstance(payload, dict) and is_allowed_guild(payload.get("guild_id"), self.bot):
                try:
                    self.events[str(row["id"])] = EventData.from_dict(payload)
                except (ValueError, TypeError):
                    logger.warning("Invalid event record ignored id=%s", row["id"])
        # Legacy transcripts have no trustworthy source guild and are never
        # resumed after reboot. New conversations are ephemeral input sessions.
        self.conversations = {}
        logger.info("EventStore: data loaded from PostgreSQL.")

    @staticmethod
    def _json_value(value):
        return json.loads(value) if isinstance(value, str) else value

    async def _load_console(self):
        self.events = {}
        self.conversations = {}
        channel = self.console_channel
        if not channel:
            channel = await self._resolve_console_channel()
            self.console_channel = channel
        if not channel:
            return
        require_guild(getattr(channel, "guild", None), self.bot)
        limit = max(
            int(os.getenv("EVENTSTORE_HISTORY_LIMIT", os.getenv("CONSOLE_HISTORY_LIMIT", "200"))),
            0,
        )
        messages = await fetch_channel_history(channel, limit=limit, reason="event_store.load")
        for msg in messages:
            if (getattr(msg.author, "id", None) == getattr(self.bot.user, "id", None)
                    and getattr(self.bot.user, "id", None) is not None
                    and getattr(msg, "webhook_id", None) is None
                    and msg.content.startswith(self.MARKER)):
                data = None
                if msg.attachments:
                    for att in msg.attachments:
                        if att.filename.endswith(".json"):
                            try:
                                raw = await att.read()
                                data = json.loads(raw.decode("utf-8"))
                                break
                            except Exception:
                                pass
                if data is None and "```json" in msg.content:
                    try:
                        start = msg.content.index("```json\n") + len("```json\n")
                        end = msg.content.rindex("\n```")
                        raw_json = msg.content[start:end]
                        data = json.loads(raw_json)
                    except Exception:
                        pass
                if isinstance(data, dict):
                    raw_events = data.get("events", {})
                    if not isinstance(raw_events, dict):
                        continue
                    parsed = {}
                    for eid, ed in raw_events.items():
                        if not isinstance(ed, dict) or not is_allowed_guild(ed.get("guild_id"), self.bot):
                            continue
                        try:
                            parsed[str(eid)] = EventData.from_dict(ed)
                        except (TypeError, ValueError):
                            logger.warning("Invalid event record ignored id=%s", eid)
                    self.events = parsed
                    self.conversations = {}
                    logger.info("EventStore: data loaded from console channel.")
                    break

    async def save_event(self, event_id: str, payload: EventData):
        require_guild(payload.guild_id, self.bot)
        data_dict = payload.model_dump(mode="json", exclude_none=True)
        async with self._write_lock:
            previous = self.events.get(event_id)
            self.events[event_id] = payload
            try:
                if self.backend == "postgres" and self.db:
                    status = await self.db.execute(
                        "INSERT INTO events(id, data) VALUES($1, $2::jsonb)"
                        " ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data"
                        " WHERE events.data->>'guild_id'=$3",
                        event_id,
                        json.dumps(data_dict),
                        str(payload.guild_id),
                    )
                    if status == "INSERT 0 0":
                        raise RuntimeError("Un événement d'un autre serveur ne peut pas être remplacé.")
                else:
                    await self._dump_console()
            except BaseException:
                if previous is None:
                    self.events.pop(event_id, None)
                else:
                    self.events[event_id] = previous
                raise

    async def save_conversation(self, conv_id: str, transcript: Optional[list]):
        if transcript is None:
            self.conversations.pop(conv_id, None)
        else:
            self.conversations[conv_id] = transcript
        if self.backend == "postgres" and self.db:
            if transcript is None:
                await self.db.execute("DELETE FROM conversations WHERE id=$1", conv_id)
            else:
                await self.db.execute(
                    "INSERT INTO conversations(id, data) VALUES($1, $2::jsonb)"
                    " ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data",
                    conv_id,
                    json.dumps(transcript),
                )
        else:
            await self._dump_console()

    async def delete_event(self, event_id: str) -> bool:
        existing = self.events.get(event_id)
        if existing is None:
            # A foreign-guild row filtered during load must never be deletable
            # merely because a caller knows its global event identifier.
            return False
        require_guild(existing.guild_id, self.bot)
        async with self._write_lock:
            previous = self.events.pop(event_id, None)
            if previous is None:
                return False
            try:
                if self.backend == "postgres" and self.db:
                    status = await self.db.execute(
                        "DELETE FROM events WHERE id=$1 AND data->>'guild_id'=$2", event_id, str(previous.guild_id),
                    )
                    if status == "DELETE 0":
                        raise RuntimeError("Événement absent ou appartenant à un autre serveur : suppression refusée.")
                else:
                    await self._dump_console()
            except BaseException:
                self.events[event_id] = previous
                raise
        logger.debug("EventStore: deleted event %s.", event_id)
        return True

    async def _dump_console(self):
        channel = self.console_channel
        if not channel:
            channel = await self._resolve_console_channel()
            self.console_channel = channel
        if not channel:
            raise RuntimeError("Console des événements indisponible : sauvegarde refusée.")
        require_guild(getattr(channel, "guild", None), self.bot)
        data = {
            "events": {eid: e.model_dump(mode="json", exclude_none=True) for eid, e in self.events.items()},
            "conversations": self.conversations,
        }
        data_str = json.dumps(data, indent=4, ensure_ascii=False)
        if len(data_str) < 1900:
            await channel.send(f"{self.MARKER}\n```json\n{data_str}\n```")
        else:
            with discord.File(io.BytesIO(data_str.encode("utf-8")), filename="event_store.json") as file:
                await channel.send(f"{self.MARKER} (fichier)", file=file)

    async def close(self):
        if self.db is not None:
            await self.db.close()
            self.db = None
