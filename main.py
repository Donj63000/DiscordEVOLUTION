import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Le token Discord est introuvable dans .env")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot connecté : {bot.user} (ID: {bot.user.id})")

@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

async def main():
    bot.remove_command("help")
    keep_alive()
    await bot.load_extension("activite")
    await bot.load_extension("job")
    await bot.load_extension("ia")
    await bot.load_extension("ticket")
    await bot.load_extension("players")
    await bot.load_extension("defender")
    await bot.load_extension("calcul")
    await bot.load_extension("sondage")
    await bot.load_extension("stats")
    await bot.load_extension("help")
    await bot.load_extension("welcome")
    await bot.load_extension("entree")
    await bot.load_extension("up")
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
