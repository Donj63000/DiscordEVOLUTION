import os
import sys
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
from alive import keep_alive

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("main")

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

@bot.event
async def on_ready():
    log.info("Connecté comme %s (id:%s)", bot.user, bot.user.id)
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
