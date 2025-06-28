import os
import sys
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

from alive import keep_alive


_bot_already_created = False

def create_bot() -> commands.Bot:
    """
    Instancie le Bot une seule fois. 
    Si déjà créé, renvoie None pour éviter un deuxième bot.
    """
    global _bot_already_created
    if _bot_already_created:
        print("Bot déjà instancié, on ne recrée pas.")
        return None

    _bot_already_created = True

    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise ValueError("Le token Discord est introuvable dans .env")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True


    new_bot = commands.Bot(command_prefix="!", intents=intents)
    return new_bot


bot = create_bot()

@bot.event
async def on_ready():
    print(f"Bot connecté : {bot.user} (ID: {bot.user.id})")

@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

async def main():
    if bot is None:

        print("Bot est None => on skip le lancement pour éviter un second process.")
        return


    bot.remove_command("help")


    keep_alive()


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
        # "slash_events",  # disabled: duplicate with event_conversation
    ]

    for ext in extensions:
        if ext not in bot.extensions:
            try:
                await bot.load_extension(ext)
                print(f"Extension chargée: {ext}")
            except Exception as e:
                print(f"Erreur lors du chargement de {ext}: {e}")

    try:
        await bot.load_extension("event_conversation")
    except Exception:
        logging.exception("❌ Échec load_extension")
        sys.exit(1)

    TOKEN = os.getenv("DISCORD_TOKEN")
    await bot.start(TOKEN)

if __name__ == "__main__":

    if bot:
        asyncio.run(main())
    else:
        print("Impossible de lancer main() : bot=None.")
