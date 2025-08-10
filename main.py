#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import uuid
import asyncio
import logging
import discord
from importlib.util import find_spec
from discord.ext import commands
from dotenv import load_dotenv
from alive import keep_alive
from collections import deque

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
        self._seen_ids = set()
        self._seen_order = deque()
        self._seen_max = 2048

        orig = self.process_commands

        async def _once_per_message(message):
            mid = getattr(message, "id", None)
            if mid is not None and mid in self._seen_ids:
                return
            if mid is not None:
                self._seen_ids.add(mid)
                self._seen_order.append(mid)
                if len(self._seen_order) > self._seen_max:
                    old = self._seen_order.popleft()
                    self._seen_ids.discard(old)
            return await orig(message)

        self.process_commands = _once_per_message

    async def _safe_load(self, ext_name: str) -> bool:
        try:
            await self.load_extension(ext_name)
            logging.info("Extension chargée: %s", ext_name)
            return True
        except Exception as e:
            logging.error("Échec de chargement de %s: %s", ext_name, e, exc_info=True)
            return False

    async def _load_iastaff_anywhere(self):
        # Essaie d'abord à la racine, puis dans cogs/
        if find_spec("iastaff") is not None and await self._safe_load("iastaff"):
            return
        if find_spec("cogs.iastaff") is not None and await self._safe_load("cogs.iastaff"):
            return
        logging.error("Extension iastaff introuvable. Assure-toi d'avoir un fichier 'iastaff.py' "
                      "à la racine du projet (ou 'cogs/iastaff.py').")

    async def setup_hook(self):
        self.remove_command("help")

        base_exts = [
            "job",
            "ia",           # IA publique (Gemini) – inchangé
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

        for ext in base_exts:
            await self._safe_load(ext)

        # Charge iastaff en prenant en compte l'endroit où le fichier a été posé
        await self._load_iastaff_anywhere()

        try:
            await self.load_extension("event_conversation")
            logging.info("Extension chargée: event_conversation")
        except Exception as e:
            logging.error("Échec load_extension event_conversation: %s", e, exc_info=True)
            await self.close()
            os._exit(1)

        cmds = [c.name for c in self.commands]
        logging.info("Commandes enregistrées: %s", cmds)

    async def wait_console_channel(self, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            for g in self.guilds:
                ch = discord.utils.get(g.text_channels, name="console")
                if ch:
                    return ch
            await asyncio.sleep(1)
        return None

    async def parse_latest_lock(self, ch: discord.TextChannel):
        async for msg in ch.history(limit=50, oldest_first=False):
            if msg.author == self.user and msg.content.startswith(LOCK_TAG):
                parts = msg.content.split()
                if len(parts) >= 3:
                    inst = parts[1]
                    try:
                        ts = int(parts[2])
                    except Exception:
                        ts = 0
                    return msg, inst, ts
        return None, None, None

    async def acquire_leadership(self):
        ch = await self.wait_console_channel(timeout=30)
        if not ch:
            logging.warning("Salon #console introuvable: pas de lock distribué, on continue.")
            return True
        my = await ch.send(f"{LOCK_TAG} {self.INSTANCE_ID} {int(time.time())}")
        self._lock_channel_id = ch.id
        self._lock_message_id = my.id
        last, inst, ts = await self.parse_latest_lock(ch)
        if last and last.id == my.id:
            logging.info("Lock acquis par %s", self.INSTANCE_ID)
            return True
        logging.warning("Lock non acquis, une autre instance est leader.")
        return False

    async def heartbeat_loop(self):
        while not self.is_closed():
            try:
                if self._lock_channel_id and self._lock_message_id:
                    ch = self.get_channel(self._lock_channel_id)
                    if ch:
                        last, inst, ts = await self.parse_latest_lock(ch)
                        if not last or last.id != self._lock_message_id:
                            logging.warning("Perte du lock au profit de %s, fermeture.", inst or "inconnu")
                            await self.close()
                            os._exit(0)
                        try:
                            msg = await ch.fetch_message(self._lock_message_id)
                            await msg.edit(content=f"{LOCK_TAG} {self.INSTANCE_ID} {int(time.time())}")
                        except Exception:
                            pass
            except Exception:
                pass
            await asyncio.sleep(15)

    async def on_ready(self):
        logging.info("Connecté comme %s (id:%s)", self.user, self.user.id)
        if not self._singleton_ready:
            ok = await self.acquire_leadership()
            if not ok:
                logging.warning("Instance concurrente détectée. Fermeture.")
                await self.close()
                os._exit(0)
            self._singleton_ready = True
            asyncio.create_task(self.heartbeat_loop())


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
    keep_alive()
    bot.run(bot.token)
