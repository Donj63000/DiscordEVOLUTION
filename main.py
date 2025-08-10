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

def create_bot() -> commands.Bot:
    load_dotenv()
    if not os.getenv("DISCORD_TOKEN"):
        raise RuntimeError("DISCORD_TOKEN manquant")
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    return bot

bot = create_bot()
bot.INSTANCE_ID = os.getenv("RENDER_INSTANCE_ID") or os.getenv("INSTANCE_ID") or uuid.uuid4().hex
bot._singleton_ready = False
bot._lock_channel_id = None
bot._lock_message_id = None

async def find_console_channel():
    for g in bot.guilds:
        ch = discord.utils.get(g.text_channels, name="console")
        if ch:
            return ch
    return None

async def parse_latest_lock(ch: discord.TextChannel):
    async for msg in ch.history(limit=50, oldest_first=False):
        if msg.author == bot.user and msg.content.startswith(LOCK_TAG):
            parts = msg.content.split()
            if len(parts) >= 3:
                inst = parts[1]
                try:
                    ts = int(parts[2])
                except:
                    ts = 0
                return msg, inst, ts
    return None, None, None

async def acquire_singleton():
    ch = await find_console_channel()
    if not ch:
        return True
    msg, inst, ts = await parse_latest_lock(ch)
    now = int(time.time())
    if msg and inst and inst != bot.INSTANCE_ID and now - ts <= 120:
        return False
    my = await ch.send(f"{LOCK_TAG} {bot.INSTANCE_ID} {now}")
    bot._lock_channel_id = ch.id
    bot._lock_message_id = my.id
    last, inst2, ts2 = await parse_latest_lock(ch)
    if last and last.id != my.id:
        return False
    return True

async def heartbeat_loop():
    while not bot.is_closed():
        try:
            if bot._lock_channel_id and bot._lock_message_id:
                ch = bot.get_channel(bot._lock_channel_id)
                if ch:
                    try:
                        msg = await ch.fetch_message(bot._lock_message_id)
                        await msg.edit(content=f"{LOCK_TAG} {bot.INSTANCE_ID} {int(time.time())}")
                    except:
                        pass
        except:
            pass
        await asyncio.sleep(60)

@bot.event
async def on_ready():
    log.info("Connecté comme %s (id:%s)", bot.user, bot.user.id)
    if not bot._singleton_ready:
        ok = await acquire_singleton()
        if not ok:
            log.warning("Instance concurrente détectée. Fermeture.")
            await bot.close()
            return
        bot._singleton_ready = True
        bot.loop.create_task(heartbeat_loop())
    try:
        await bot.change_presence(activity=discord.Game(name="!ia pour discuter en MP"))
    except Exception:
        pass

@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

@bot.event
async def on_command_error(ctx, error):
    try:
        await ctx.reply(f"⚠️ {error.__class__.__name__}: {error}", mention_author=False)
    except Exception:
        pass
    log.exception("on_command_error: %s", error)

async def load_extensions():
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
            await bot.load_extension(ext)
            log.info("Extension chargée: %s", ext)
        except Exception:
            log.exception("Échec de chargement de %s", ext)
            if ext == "ia":
                sys.exit(1)
    try:
        await bot.load_extension("event_conversation")
        log.info("Extension chargée: event_conversation")
    except Exception:
        log.exception("Échec load_extension event_conversation")
        sys.exit(1)
    cmds = [c.name for c in bot.commands]
    log.info("Commandes enregistrées: %s", cmds)

async def main():
    bot.remove_command("help")
    keep_alive()
    await load_extensions()
    await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
