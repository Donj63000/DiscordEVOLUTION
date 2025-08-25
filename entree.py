#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from datetime import datetime

class EntreeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        bienvenue_channel = discord.utils.get(member.guild.text_channels, name="🛫 Bienvenue 🛫")
        if not bienvenue_channel:
            print("[DEBUG] Le canal '🛫 Bienvenue 🛫' est introuvable.")
            return

        message_texte = (
            f"🎉 **Un nouveau joueur vient de nous rejoindre !** 🎉\n\n"
            f"Bienvenue à toi, {member.mention} ! Nous sommes ravis de t'accueillir dans la guilde **Evolution** sur Dofus.\n"
            "Fais comme chez toi, discute avec nous et profite du serveur !\n\n"
            "*(Si tu as des questions ou besoin d'aide, n'hésite pas à interpeller un membre du staff ou à passer sur le salon général.)*\n"
        )

        try:
            file = discord.File("entree.png", filename="entree.png")
            await bienvenue_channel.send(content=message_texte, file=file)
            print(f"[DEBUG] Message de bienvenue envoyé pour {member} avec l'image entree.png.")
        except Exception as e:
            print(f"[DEBUG] Erreur lors de l'envoi du message de bienvenue : {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        bienvenue_channel = discord.utils.get(member.guild.text_channels, name="🛫 Bienvenue 🛫")
        if not bienvenue_channel:
            print("[DEBUG] Le canal '🛫 Bienvenue 🛫' est introuvable.")
            return

        if member.joined_at is None:
            duree_str = "inconnue"
        else:
            duree = datetime.utcnow() - member.joined_at.replace(tzinfo=None)
            jours = duree.days
            heures = duree.seconds // 3600
            minutes = (duree.seconds % 3600) // 60

            parts = []
            if jours > 0:
                parts.append(f"{jours} jour{'x' if jours > 1 else ''}")
            if heures > 0:
                parts.append(f"{heures} heure{'s' if heures > 1 else ''}")
            if minutes > 0:
                parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")

            if not parts:
                duree_str = "moins d'une minute"
            else:
                duree_str = ", ".join(parts)

        # Ajout du pseudo local au serveur : member.display_name
        # (nick s’il existe, sinon username Discord)
        message_texte = (
            f"😢 **Un joueur nous quitte...** 😢\n\n"
            f"Le membre {member.mention} (pseudo : **{member.display_name}**) a quitté le serveur.\n"
            f"Il/Elle était parmi nous depuis **{duree_str}**.\n\n"
            "Nous lui souhaitons une bonne continuation !"
        )

        try:
            file = discord.File("quitter.png", filename="quitter.png")
            await bienvenue_channel.send(content=message_texte, file=file)
            print(f"[DEBUG] Message d'au revoir envoyé pour {member} avec l'image quitter.png.")
        except Exception as e:
            print(f"[DEBUG] Erreur lors de l'envoi du message d'au revoir : {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(EntreeCog(bot))
