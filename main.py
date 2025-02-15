#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Import du mini-serveur Flask
from alive import keep_alive

# -----------------------
# 1) Chargement du token
# -----------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Le token Discord est introuvable dans .env")

# -----------------------
# 2) Configuration du bot
# -----------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------
# 3) Événement on_ready
# -----------------------
@bot.event
async def on_ready():
    print(f"Bot connecté : {bot.user} (ID: {bot.user.id})")

# -----------------------
# 4) Exemple de commande ping
# -----------------------
@bot.command(name="ping")
async def ping_cmd(ctx):
    await ctx.send("Pong!")

# -----------------------
# 5) Fonction principale asynchrone
# -----------------------
async def main():
    """
    Lance le mini-serveur Flask (keep_alive),
    charge toutes les extensions en mode asynchrone,
    puis démarre le bot Discord.
    """
    # 1) Démarrer le mini-serveur HTTP pour garder le service actif
    keep_alive()

    # 2) Charger les extensions (Cogs) avec 'await'
    #    Si tu veux retirer la commande help d'origine :
    #    bot.remove_command("help")
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

    # 3) Démarrer le bot (bloquant tant que le bot reste en ligne)
    await bot.start(TOKEN)

# -----------------------
# 6) Lancement du script
# -----------------------
if __name__ == "__main__":
    # On exécute la fonction asynchrone 'main()' dans l'event loop
    asyncio.run(main())
