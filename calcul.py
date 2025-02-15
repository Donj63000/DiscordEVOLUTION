#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
import os
import re
import json
import base64
import sqlite3
import logging
import time
from urllib.parse import urlparse
import requests
import validators
from cryptography.fernet import Fernet

PALIER_100 = {
    "force": {"normale": 4, "pa": 9, "ra": 34},
    "intelligence": {"normale": 4, "pa": 9, "ra": 34},
    "chance": {"normale": 4, "pa": 9, "ra": 34},
    "agilite": {"normale": 4, "pa": 9, "ra": 34},
    "vitalite": {"normale": 5, "pa": 27, "ra": 104},
    "initiative": {"normale": 17, "pa": 84, "ra": 334},
    "pods": {"normale": 17, "pa": 84, "ra": 334},
    "sagesse": {"normale": 2, "pa": 6, "ra": 25},
    "prospection": {"normale": 2, "pa": 6, "ra": 25}
}

STATS_SPECIALES = ["pa", "pm", "po", "invocation"]

def estimer_probabilites(stat: str, valeur_jet: int):
    """
    Retourne un dictionnaire avec la probabilité (en pourcentage) d'obtenir
    chaque type de rune (normale, Pa, Ra) à partir de 'valeur_jet' pour la stat 'stat'.
    """
    if stat in STATS_SPECIALES:
        return {
            "special": True,
            "message": (
                f"La caractéristique **{stat.upper()}** ne peut pas atteindre 100% d'obtention "
                "pour sa rune (Ga Pa / Ga Pme / Ga Po...). Les taux dépassent rarement 66%. "
                "Impossible de calculer un palier 100%."
            )
        }
    if stat not in PALIER_100:
        return {
            "error": True,
            "message": f"Statistique inconnue : {stat}. Paliers non définis dans le module."
        }
    paliers = PALIER_100[stat]
    p_n = paliers["normale"]
    p_pa = paliers["pa"]
    p_ra = paliers["ra"]
    prob_normale = 0
    prob_pa = 0
    prob_ra = 0
    if valeur_jet <= 0:
        prob_normale = 0
    elif 1 <= valeur_jet < p_n:
        base = (valeur_jet / p_n) * 80 + 20
        prob_normale = min(base, 100)
    else:
        prob_normale = 100
    if valeur_jet < p_pa:
        if valeur_jet <= p_n:
            prob_pa = 0
        else:
            interval = p_pa - p_n
            base = (valeur_jet - p_n) / interval * 80 + 20
            prob_pa = min(base, 100)
    else:
        prob_pa = 100
    if valeur_jet < p_pa:
        prob_ra = 0
    elif p_pa <= valeur_jet < p_ra:
        interval = p_ra - p_pa
        base = (valeur_jet - p_pa) / interval * 70 + 20
        prob_ra = min(base, 100)
    else:
        prob_ra = 100
    return {
        "error": False,
        "special": False,
        "normale": round(prob_normale),
        "pa": round(prob_pa),
        "ra": round(prob_ra)
    }

class CalculRuneCog(commands.Cog):
    """
    Cog permettant la commande !rune pour estimer les probabilités d'obtention de runes
    à partir d'un jet donné dans une statistique (selon des formules communautaires).
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="rune")
    async def rune_command(self, ctx: commands.Context, *, args: str = None):
        """
        Utilisation : !rune jet <valeur_jet> <stat>
        Exemple : "!rune jet 30 force" ou "!rune jet 104 vitalite"
        """
        if not args:
            usage_msg = (
                "**Commande :** `!rune jet <valeur_jet> <stat>`\n\n"
                "**Description :**\n"
                "Cette commande permet d'estimer les probabilités d'obtention de runes lors du brisage d'un objet dans Dofus Retro, "
                "selon la valeur du jet (la statistique de l'objet) et la caractéristique concernée.\n\n"
                "**Paramètres :**\n"
                " - `jet` : Mot-clé obligatoire indiquant que l'on fournit la valeur du jet.\n"
                " - `<valeur_jet>` : Un entier représentant la valeur du jet obtenu (ex. 30 ou 104).\n"
                " - `<stat>` : La statistique concernée (par exemple : force, intelligence, chance, agilite, vitalite, etc.).\n\n"
                "**Exemples :**\n"
                " - `!rune jet 30 force` : Estime les probabilités pour un objet affichant 30 points en force.\n"
                " - `!rune jet 104 vitalite` : Estime les probabilités pour un objet affichant 104 points en vitalite, ce qui correspond souvent à un palier garanti pour une rune Ra vitalite.\n\n"
                "**Notes :**\n"
                " - Les statistiques supportées dans ce calcul sont celles définies dans le dictionnaire (force, intelligence, chance, agilite, vitalite, sagesse, initiative, pods, prospection).\n"
                " - Pour les statistiques spéciales (ex. `pa`, `pm`, `po`, `invocation`), aucun calcul n'est effectué car leur taux maximal reste plafonné (~66%).\n\n"
                "Les valeurs sont calculées par interpolation linéaire entre des paliers définis par la communauté. "
                "Pour plus d'informations, consultez par exemple [les taux de brisage sur JeuxOnLine](https://forums.jeuxonline.info/sujet/1045383/les-taux-de-brisage).\n\n"
                "Utilisez cette commande pour obtenir une estimation rapide des chances d'obtenir chaque type de rune "
                "en fonction du jet de l'objet et de la statistique choisie. Les résultats sont arrondis et fournis à titre indicatif."
            )
            await ctx.send(usage_msg)
            return
        tokens = args.split()
        if len(tokens) < 3:
            await ctx.send("Syntaxe incorrecte. Exemple : `!rune jet 30 force`")
            return
        if tokens[0].lower() != "jet":
            await ctx.send("Syntaxe incorrecte. Il manque le mot-clé 'jet' après !rune.")
            return
        try:
            valeur_jet = int(tokens[1])
        except ValueError:
            await ctx.send(f"`{tokens[1]}` n'est pas un nombre valide.")
            return
        stat_input = " ".join(tokens[2:]).lower().replace("é", "e").replace("è", "e")
        remplacement = {
            "agilite": ["agi", "agilite"],
            "force": ["fo", "force"],
            "intelligence": ["intell", "intelligence", "intel"],
            "chance": ["cha", "chance"],
            "vitalite": ["vita", "vitalite"],
            "initiative": ["ini", "initiative"],
            "pods": ["pod", "pods"],
            "sagesse": ["sagesse", "sage"],
            "prospection": ["pp", "prospection"],
            "pa": ["pa"],
            "pm": ["pm"],
            "po": ["po"],
            "invocation": ["invoc", "invocation"]
        }
        matched_stat = None
        for cle, aliases in remplacement.items():
            if stat_input in aliases:
                matched_stat = cle
                break
        if not matched_stat:
            matched_stat = stat_input if stat_input in PALIER_100 or stat_input in STATS_SPECIALES else None
            if not matched_stat:
                await ctx.send(f"Statistique '{stat_input}' inconnue.")
                return
        result = estimer_probabilites(matched_stat, valeur_jet)
        if result.get("error"):
            await ctx.send(result["message"])
            return
        if result.get("special"):
            await ctx.send(result["message"])
            return
        prob_norm = result["normale"]
        prob_pa = result["pa"]
        prob_ra = result["ra"]
        embed = discord.Embed(
            title="Estimation de brisage",
            description=f"**Jet** : {valeur_jet} en {matched_stat}\nProbabilités estimées d'obtention :",
            color=0x03a9f4
        )
        embed.add_field(name="Rune normale", value=f"{prob_norm}%", inline=True)
        embed.add_field(name="Rune Pa", value=f"{prob_pa}%", inline=True)
        embed.add_field(name="Rune Ra", value=f"{prob_ra}%", inline=True)
        note = ("⚠️ Ces chiffres sont approximatifs, basés sur des formules communautaires.\n"
                "Au-dessus de chaque palier, la rune inférieure est déjà incluse (ex. si Pa=100%, alors normale=100% aussi).")
        embed.set_footer(text=note)
        await ctx.send(embed=embed)

def setup(bot: commands.Bot):
    bot.add_cog(CalculRuneCog(bot))
