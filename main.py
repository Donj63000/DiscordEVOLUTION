#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fichier principal main.py
-------------------------
Ce script démarre ton bot Discord et utilise la fonction keep_alive()
pour rester actif en continu sur des plateformes d'hébergement gratuites.
"""

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Import de la fonction keep_alive depuis notre module alive.py
from alive import keep_alive

# -----------------------
# 1) Chargement du token
# -----------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Le token Discord est introuvable dans .env")

# -----------------------
# 2) Configuration des intents & bot
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
# 5) Chargement des cogs
# -----------------------
if __name__ == "__main__":
    # Si tu veux retirer la commande help par défaut
    bot.remove_command("help")

    # Charger tes cogs (extension)
    bot.load_extension("job")
    bot.load_extension("ia")
    bot.load_extension("ticket")
    bot.load_extension("players")
    bot.load_extension("defender")
    bot.load_extension("calcul")
    bot.load_extension("sondage")
    bot.load_extension("activite")
    bot.load_extension("stats")
    bot.load_extension("help")
    bot.load_extension("welcome")

    # -----------------------
    # 6) Lancement du keep_alive
    # -----------------------
    # On appelle la fonction depuis alive.py
    keep_alive()

    # -----------------------
    # 7) Lancement du bot Discord
    # -----------------------
    bot.run(TOKEN)
