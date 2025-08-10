#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import uuid
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
from alive import keep_alive

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("main")

LOCK_TAG = "===BOTLOCK==="

class EvoBot(commands.Bot):
    def __init__(self):
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_TOKEN manquant")
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.token = token
        self.INSTANCE_ID = os.getenv("RENDER_INSTANCE_ID") or os.getenv("INSTANCE_ID") or uuid.uuid4().hex
        self._singleton_ready = False
        self._lock_channel_id = None
        self._lock_message_id = None
        orig = self.process_commands
        async def _once_per_message(message):
            if getattr(message, "_cmds_done", False):
                return
            message._cmds_done = True
            return await orig(message)
        self.process_commands = _once_per_message

    async def setup_hook(self):
        self.remove_command("help")
        extensions = [
            "job",
            "ia",
            "activite",
            "ticket",
            "players",
            "sondage",
            "stats",
            "help",
            "welcome",
            "entree",
            "calcul",
            "defender",
            "moderation",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                logging.info("Extension chargée: %s", ext)
            except Exception:
                logging.exception("Échec de chargement de %s", ext)
                if ext == "ia":
                    await self.close()
                    return
        try:
            await self.load_extension("event_conversation")
            logging.info("Extension chargée: event_conversation")
        except Exception:
            logging.exception("Échec load_extension event_conversation")
            await self.close()
            return
        cmds = [c.name for c in self.commands]
        logging.info("Commandes enregistrées: %s", cmds)

    async def find_console_channel(self):
        for g in self.guilds:
            ch = discord.utils.get(g.text_channels, name="console")
            if ch:
                return ch
        return None

    async def parse_latest_lock(self, ch: discord.TextChannel):
        async for msg in ch.history(limit=50, oldest_first=False):
            if msg.author == self.user and msg.content.startswith(LOCK_TAG):
                parts = msg.content.split()
                if len(parts) >= 3:
                    inst = parts[1]
                    try:
                        ts = int(parts[2])
                    except:
                        ts = 0
                    return msg, inst, ts
        return None, None, None

    async def acquire_singleton(self):
        ch = await self.find_console_channel()
        if not ch:
            return True
        msg, inst, ts = await self.parse_latest_lock(ch)
        now = int(time.time())
        if msg and inst and inst != self.INSTANCE_ID and now - ts <= 120:
            return False
        my = await ch.send(f"{LOCK_TAG} {self.INSTANCE_ID} {now}")
        self._lock_channel_id = ch.id
        self._lock_message_id = my.id
        last, inst2, ts2 = await self.parse_latest_lock(ch)
        if last and last.id != my.id:
            return False
        return True

    async def heartbeat_loop(self):
        while not self.is_closed():
            try:
                if self._lock_channel_id and self._lock_message_id:
                    ch = self.get_channel(self._lock_channel_id)
                    if ch:
                        try:
                            msg = await ch.fetch_message(self._lock_message_id)
                            await msg.edit(content=f"{LOCK_TAG} {self.INSTANCE_ID} {int(time.time())}")
                        except:
                            pass
            except:
                pass
            await asyncio.sleep(60)

    async def on_ready(self):
        logging.info("Connecté comme %s (id:%s)", self.user, self.user.id)
        if not self._singleton_ready:
            ok = await self.acquire_singleton()
            if not ok:
                logging.warning("Instance concurrente détectée. Fermeture.")
                await self.close()
                return
            self._singleton_ready = True
            asyncio.create_task(self.heartbeat_loop())
            if os.getenv("KEEP_ALIVE") == "1":
                try:
                    keep_alive()
                except Exception:
                    logging.exception("keep_alive a échoué")

bot = EvoBot()

@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

@bot.event
async def on_command_error(ctx, error):
    try:
        await ctx.reply(f"⚠️ {error.__class__.__name__}: {error}", mention_author=False)
    except Exception:
        pass
    logging.exception("on_command_error: %s", error)

if __name__ == "__main__":
    bot.run(bot.token)
