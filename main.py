#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

from alive import keep_alive  # ton mini-serveur Flask

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
    # 1) Lancement du mini-serveur keep-alive
    keep_alive()

    # 2) Charger les cogs en mode asynchrone
    #    bot.remove_command("help") si tu veux virer le help par défaut
    await bot.load_extension("job")
    await bot.load_extension("ia")
    await bot.load_extension("ticket")
    await bot.load_extension("players")
    await bot.load_extension("defender")
    await bot.load_extension("calcul")
    await bot.load_extension("sondage")
    await bot.load_extension("activite")
    await bot.load_extension("stats")
    await bot.load_extension("help")
    await bot.load_extension("welcome")

    # 3) Démarrer le bot
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
