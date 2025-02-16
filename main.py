#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Fichier principal main.py
-------------------------
Ce script démarre ton bot Discord et utilise la fonction keep_alive()
pour rester actif en continu sur des plateformes d'hébergement gratuites.
"""

import os
import asyncio
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
# 5) Fonction principale asynchrone
# -----------------------
async def main():
    # Optionnel : retirer la commande help par défaut si désiré
    bot.remove_command("help")
    
    # Lancer le mini-serveur Flask pour le keep-alive
    keep_alive()
    
    # Charger les cogs en mode asynchrone avec 'await'

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

    
    # Démarrer le bot Discord
    await bot.start(TOKEN)

# -----------------------
# 6) Lancement du script
# -----------------------
if __name__ == "__main__":
    asyncio.run(main())
