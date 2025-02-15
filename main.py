#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
# Ton mini-serveur Flask "keep_alive"
from alive import keep_alive  

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Le token Discord est introuvable dans .env")

# Configuration des intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Création du bot
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot connecté : {bot.user} (ID: {bot.user.id})")

# Exemple de commande test
@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

# -------------------------------
# 1) Fonction principale asynchrone
# -------------------------------
async def main():
    """
    - Lance le serveur Flask (keep_alive)
    - Charge tes cogs avec 'await'
    - Lance le bot Discord
    """

    # (A) Lancement du mini-serveur Flask
    keep_alive()

    # (B) Charger tes Cogs en mode asynchrone
    #     Si tu veux retirer la commande help d’origine :
    #     bot.remove_command("help")  
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

    # (C) Démarrer le bot (bloquant tant que le bot est vivant)
    await bot.start(TOKEN)

# -------------------------------
# 2) Lancement via asyncio.run
# -------------------------------
if __name__ == "__main__":
    asyncio.run(main())
