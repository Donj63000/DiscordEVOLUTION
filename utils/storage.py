import os
import json
import logging
from typing import Dict, Optional

from models import EventData

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


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

    async def connect(self):
        db_url = os.getenv("DATABASE_URL")
        if db_url:
            self.backend = "postgres"
            try:
                import asyncpg

                self.db = await asyncpg.create_pool(dsn=db_url)
                await self._init_db()
                logger.info("EventStore connected to PostgreSQL.")
            except Exception as e:
                logger.warning(f"PostgreSQL unavailable: {e}")
                self.backend = "console"
        else:
            self.backend = "console"

        if self.backend == "console":
            self.console_channel = discord.utils.get(
                self.bot.get_all_channels(), name=self.console_channel_name
            )
            if not self.console_channel:
                logger.warning("Console channel not found; persistence disabled.")

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
        self.events = {r["id"]: EventData.from_dict(dict(r["data"])) for r in rows}
        rows = await self.db.fetch("SELECT id, data FROM conversations")
        self.conversations = {r["id"]: dict(r["data"]) for r in rows}
        logger.info("EventStore: data loaded from PostgreSQL.")

    async def _load_console(self):
        self.events = {}
        self.conversations = {}
        channel = self.console_channel
        if not channel:
            return
        async for msg in channel.history(limit=1000):
            if msg.author == self.bot.user and self.MARKER in msg.content:
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
                if data:
                    raw_events = data.get("events", {})
                    self.events = {eid: EventData.from_dict(ed) for eid, ed in raw_events.items()}
                    self.conversations = data.get("conversations", {})
                    logger.info("EventStore: data loaded from console channel.")
                    break

    async def save_event(self, event_id: str, payload: EventData):
        self.events[event_id] = payload
        data_dict = payload.model_dump(mode="python", exclude_none=True)
        if self.backend == "postgres" and self.db:
            await self.db.execute(
                "INSERT INTO events(id, data) VALUES($1, $2)"
                " ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data",
                event_id,
                data_dict,
            )
        else:
            await self._dump_console()

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
                    "INSERT INTO conversations(id, data) VALUES($1, $2)"
                    " ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data",
                    conv_id,
                    transcript,
                )
        else:
            await self._dump_console()

    async def delete_event(self, event_id: str) -> bool:
        existed = self.events.pop(event_id, None) is not None
        if self.backend == "postgres" and self.db:
            await self.db.execute("DELETE FROM events WHERE id=$1", event_id)
        else:
            await self._dump_console()
        if existed:
            logger.debug("EventStore: deleted event %s.", event_id)
        return existed

    async def _dump_console(self):
        channel = self.console_channel
        if not channel:
            return
        data = {
            "events": {eid: e.model_dump(mode="python", exclude_none=True) for eid, e in self.events.items()},
            "conversations": self.conversations,
        }
        data_str = json.dumps(data, indent=4, ensure_ascii=False)
        if len(data_str) < 1900:
            await channel.send(f"{self.MARKER}\n```json\n{data_str}\n```")
        else:
            temp_path = "temp_event_store.json"
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(data_str)
                await channel.send(
                    f"{self.MARKER} (fichier)",
                    file=discord.File(fp=temp_path, filename="event_store.json"),
                )
            finally:
                try:
                    os.remove(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logger.warning(
                        "Failed to remove temporary event store file %s: %s",
                        temp_path,
                        exc,
                    )
