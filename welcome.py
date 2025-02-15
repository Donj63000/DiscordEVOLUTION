#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
import asyncio
from discord.ext import commands
from datetime import datetime

class WelcomeCog(commands.Cog):
    """
    Cog de gestion de l'accueil des nouveaux membres sur un serveur Discord.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Se déclenche lorsqu'un membre rejoint le serveur.
        1. MP de bienvenue + question sur le règlement
        2. Confirmation 'oui', sinon rappel
        3. Demande s'il est invité ou membre
        4. Si invité => rôle Invités + fin
        5. Si membre => pseudo Dofus, recruteur => role Membre validé
        6. Message de bienvenue dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥
        7. Détails dans #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭
        8. Enregistrement auto via PlayersCog si présent
        """

        # Étape 1 : MP de bienvenue
        try:
            dm_channel = await member.create_dm()
            bienvenue_msg = (
                f"🎉 Bienvenue dans Evolution, {member.mention} ! 🎉\n\n"
                "Nous sommes ravis de t’accueillir. Merci de prendre un moment pour lire le règlement.\n"
                "As-tu lu et accepté le règlement ? (Réponds simplement par **oui**.)"
            )
            # Envoi éventuel d’une image
            file = discord.File("welcome1.png", filename="welcome1.png")
            await dm_channel.send(content=bienvenue_msg, file=file)

        except discord.Forbidden:
            print(f"Impossible d’envoyer un MP à {member}.")
            return

        # Étape 2 : Attente de la confirmation
        def check_reglement(msg: discord.Message):
            return (
                msg.author == member
                and msg.channel == dm_channel
                and msg.content.lower().startswith("oui")
            )

        try:
            await self.bot.wait_for("message", timeout=300.0, check=check_reglement)
        except asyncio.TimeoutError:
            # Pas de réponse => rappel
            try:
                rappel_msg = (
                    f"⏳ Hé, {member.mention}, je n’ai pas encore reçu ta confirmation !\n"
                    "Pour avancer, réponds simplement **oui** si tu acceptes le règlement."
                )
                await dm_channel.send(rappel_msg)
            except discord.Forbidden:
                pass
            return

        # Étape 3 : Demander s’il est invité ou membre
        invite_or_member_msg = (
            "Parfait ! Es-tu **membre** de la guilde ou simplement **invité** sur le serveur ?\n"
            "(Réponds par `membre` ou `invité`.)"
        )
        await dm_channel.send(invite_or_member_msg)

        def check_status(msg: discord.Message):
            return (
                msg.author == member
                and msg.channel == dm_channel
                and msg.content.lower() in ["membre", "invité"]
            )

        try:
            status_response = await self.bot.wait_for("message", timeout=300.0, check=check_status)
            user_status = status_response.content.lower()
        except asyncio.TimeoutError:
            user_status = "invité"
            try:
                await dm_channel.send(
                    "Temps écoulé. Je considère que tu es **invité** pour le moment."
                )
            except discord.Forbidden:
                pass

        # Si invité => rôle + fin
        if user_status == "invité":
            guests_role = discord.utils.get(member.guild.roles, name="Invités")
            if guests_role:
                try:
                    await member.add_roles(guests_role)
                    await dm_channel.send(
                        "Tu as reçu le rôle **Invités**. Pour nous rejoindre plus tard, contacte le staff !"
                    )
                except Exception as e:
                    print(f"Impossible d'ajouter le rôle Invités à {member}: {e}")
            else:
                await dm_channel.send("Le rôle 'Invités' n’existe pas. Signale-le à un admin.")
            return

        # Étape 4 : Si membre => demande pseudo Dofus
        await dm_channel.send(
            "Super, bienvenue officiellement ! Peux-tu me donner **ton pseudo exact** sur Dofus ?"
        )

        def check_pseudo(msg: discord.Message):
            return msg.author == member and msg.channel == dm_channel

        try:
            pseudo_reponse = await self.bot.wait_for("message", timeout=300.0, check=check_pseudo)
            dofus_pseudo = pseudo_reponse.content.strip()
        except asyncio.TimeoutError:
            dofus_pseudo = "Inconnu"
            try:
                await dm_channel.send("Temps écoulé, on mettra ‘Inconnu’ pour le moment.")
            except discord.Forbidden:
                pass
            return

        # Étape 5 : Demander le recruteur
        question_recruteur_msg = (
            "Qui t’a invité dans la guilde ? (Pseudo Discord ou Dofus)\n"
            "Si tu ne sais plus, réponds `non`."
        )
        await dm_channel.send(question_recruteur_msg)

        def check_recruteur(msg: discord.Message):
            return msg.author == member and msg.channel == dm_channel

        try:
            recruiter_response = await self.bot.wait_for("message", timeout=300.0, check=check_recruteur)
            recruiter_pseudo = recruiter_response.content.strip()
        except asyncio.TimeoutError:
            recruiter_pseudo = "non"
            try:
                await dm_channel.send("Ok, on mettra ‘non’ pour le recruteur.")
            except discord.Forbidden:
                pass

        # Date
        recruitment_date = datetime.now().strftime("%d/%m/%Y")

        # Étape 6 : Renommer + rôle Membre validé
        validated_role = discord.utils.get(member.guild.roles, name="Membre validé d'Evolution")
        try:
            await member.edit(nick=dofus_pseudo)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Impossible de renommer {member}: {e}")

        if validated_role:
            try:
                await member.add_roles(validated_role)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Impossible d'ajouter le rôle Membre validé à {member}: {e}")
        else:
            await dm_channel.send(
                "Le rôle 'Membre validé d'Evolution' est introuvable. Signale-le à un admin."
            )

        # Message de confirmation
        try:
            await dm_channel.send(
                f"Merci, **{dofus_pseudo}** ! Tu es désormais officiellement membre de la guilde Evolution. 🎉"
            )
        except discord.Forbidden:
            pass

        # Inscription auto dans PlayersCog si disponible
        players_cog = self.bot.get_cog("PlayersCog")
        if players_cog:
            players_cog.auto_register_member(
                discord_id=member.id,
                discord_display_name=member.display_name,
                dofus_pseudo=dofus_pseudo
            )
        else:
            print("[WARNING] PlayersCog introuvable, pas d'inscription auto.")

        # Étape 7 : Annonce dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥
        general_channel = discord.utils.get(member.guild.text_channels, name="𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥")
        if general_channel:
            annonce_msg_general = (
                f"🔥 Un nouvel aventurier nous rejoint ! 🔥\n\n"
                f"{member.mention}, alias **{dofus_pseudo}**, débarque dans Evolution. "
                "Faites-lui un triomphe !"
            )
            await general_channel.send(annonce_msg_general)
        else:
            print("Canal '𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥' introuvable.")

        # Étape 8 : Annonce dans #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭
        recruitment_channel = discord.utils.get(member.guild.text_channels, name="𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭")
        if recruitment_channel:
            if recruiter_pseudo.lower() == "non":
                recruiter_info = "n’a pas indiqué de recruteur"
            else:
                recruiter_info = f"a été invité par **{recruiter_pseudo}**"

            await recruitment_channel.send(
                f"Le joueur **{dofus_pseudo}** a rejoint la guilde le **{recruitment_date}** "
                f"et {recruiter_info}."
            )
        else:
            print("Canal '𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭' introuvable.")


# Pour Py‑Cord / Discord.py 2.x, on déclare la fonction setup de manière asynchrone
async def setup(bot: commands.Bot):
    """
    Charger ce cog avec :
        await bot.load_extension("welcome")
    """
    await bot.add_cog(WelcomeCog(bot))
