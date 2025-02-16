#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from datetime import datetime

class EntreeCog(commands.Cog):
    """
    Cog gérant l'annonce publique dans le canal '𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞' lors de l'arrivée ou du départ d'un membre.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Événement déclenché lorsqu'un nouveau membre rejoint le serveur.
        Envoie un message de bienvenue dans le canal "𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞" avec l'image entree.png.
        """
        # Éviter les messages de bienvenue pour les bots
        if member.bot:
            return

        bienvenue_channel = discord.utils.get(member.guild.text_channels, name="𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞")
        if not bienvenue_channel:
            print("[DEBUG] Le canal '𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞' est introuvable.")
            return

        # Préparation du message et de l'image
        # On utilise un ton sympathique et chaleureux
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
        """
        Événement déclenché lorsqu'un membre quitte ou est expulsé du serveur.
        Envoie un message d'au revoir dans le canal "𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞" avec l'image quitter.png,
        et mentionne depuis combien de temps le membre était présent.
        """
        # Les bots ne sont pas concernés par les messages d’au revoir
        if member.bot:
            return

        bienvenue_channel = discord.utils.get(member.guild.text_channels, name="𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞")
        if not bienvenue_channel:
            print("[DEBUG] Le canal '𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞' est introuvable.")
            return

        # Calculer la durée de présence sur le serveur
        # NOTE: member.joined_at est un datetime (UTC). On compare avec datetime.utcnow() pour un calcul cohérent.
        if member.joined_at is None:
            # Il arrive (rarement) que joined_at soit None si on n'a pas eu le temps de récupérer l'info.
            duree_str = "inconnue"
        else:
            duree = datetime.utcnow() - member.joined_at.replace(tzinfo=None)
            # On formatte la durée en jours/heures/minutes
            jours = duree.days
            heures = duree.seconds // 3600
            minutes = (duree.seconds % 3600) // 60

            # Construction d'un petit texte en français
            parts = []
            if jours > 0:
                parts.append(f"{jours} jour{'x' if jours > 1 else ''}")
            if heures > 0:
                parts.append(f"{heures} heure{'s' if heures > 1 else ''}")
            if minutes > 0:
                parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
            
            if not parts:
                # Membre resté moins d'une minute
                duree_str = "moins d'une minute"
            else:
                duree_str = ", ".join(parts)

        # Préparation du message et de l'image
        message_texte = (
            f"😢 **Un joueur nous quitte...** 😢\n\n"
            f"Le membre {member.mention} a quitté le serveur.\n"
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
    """
    Fonction d'initialisation du Cog dans le bot.
    """
    await bot.add_cog(EntreeCog(bot))
