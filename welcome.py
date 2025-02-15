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

    already_welcomed = set()

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Petit log de debug
        print(f"[DEBUG] on_member_join triggered for user {member} (ID={member.id}).")

        # 1) Ignorer si c'est un bot ou si on l'a déjà accueilli
        if member.bot:
            print("[DEBUG] Member is a bot, ignoring.")
            return
        if member.id in self.already_welcomed:
            print("[DEBUG] Member déjà accueilli, on arrête.")
            return
        self.already_welcomed.add(member.id)
        print("[DEBUG] Ajout de l'ID dans already_welcomed")

        # 2) Tenter d'envoyer un MP de bienvenue
        dm_channel = None
        try:
            dm_channel = await member.create_dm()
            bienvenue_msg = (
                f"🎉 **Bienvenue dans Evolution, {member.mention} !** 🎉\n\n"
                "Nous sommes super contents de t’accueillir parmi nous. "
                "Avant de commencer, prends juste quelques instants pour parcourir notre **règlement** — "
                "on préfère que tout se passe dans la bonne ambiance ! 😇\n\n"
                "D’ailleurs, l’as-tu **lu et accepté** ? \n\n"
                "*(Pour confirmer, réponds simplement par **oui**.)*\n\n"
                "*(Si tu ne réponds pas, je t’enverrai un petit rappel.)*"
            )
            file = discord.File("welcome1.png", filename="welcome1.png")
            await dm_channel.send(content=bienvenue_msg, file=file)
            print("[DEBUG] Message privé de bienvenue envoyé.")
        except discord.Forbidden:
            print("[DEBUG] Impossible d’envoyer un MP (DM bloqués). Fallback canal public.")
            await self.fallback_public_greeting(member)
            return

        # 3) Vérifier l'acceptation du règlement
        def check_reglement(msg: discord.Message):
            return (
                msg.author == member
                and msg.channel == dm_channel
                and msg.content.lower().startswith("oui")
            )

        try:
            await self.bot.wait_for("message", timeout=300.0, check=check_reglement)
            print("[DEBUG] L'utilisateur a accepté le règlement.")
        except asyncio.TimeoutError:
            # Pas de réponse => rappel
            try:
                rappel_msg = (
                    f"⏳ Hé, {member.mention}, je n’ai pas encore reçu ta confirmation !\n\n"
                    "Pour qu’on puisse avancer, réponds simplement **oui** si tu **acceptes** le règlement. 📝"
                )
                await dm_channel.send(rappel_msg)
                print("[DEBUG] Rappel envoyé (pas de réponse).")
            except discord.Forbidden:
                pass
            return

        # 4) Demander s’il est invité ou membre
        invite_or_member_msg = (
            "**Parfait !** Maintenant, dis-moi : tu es **membre** de la guilde ou juste **invité** sur le serveur ?\n\n"
            "*(Réponds par `membre` ou `invité`.)*"
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
            print(f"[DEBUG] L'utilisateur se définit comme {user_status}.")
        except asyncio.TimeoutError:
            user_status = "invité"
            try:
                await dm_channel.send(
                    "Le temps est écoulé. Je vais supposer que tu es **invité** pour l’instant, pas de soucis ! 💁"
                )
            except discord.Forbidden:
                pass

        # 5) Gestion invité => on attribue le rôle Invités et on termine
        if user_status == "invité":
            guests_role = discord.utils.get(member.guild.roles, name="Invités")
            if guests_role:
                try:
                    await member.add_roles(guests_role)
                    await dm_channel.send(
                        "Pas de souci ! Je t’ai attribué le rôle **Invités**. "
                        "Profite du serveur et n’hésite pas à discuter avec nous. "
                        "Et si tu veux rejoindre la guilde plus tard, fais signe au staff ! 😉"
                    )
                    print("[DEBUG] Rôle Invités ajouté.")
                except Exception as e:
                    print(f"[DEBUG] Impossible d'ajouter le rôle Invités à {member}: {e}")
            else:
                await dm_channel.send(
                    "Le rôle 'Invités' n’existe pas encore. Peux-tu prévenir un admin ? 🙏"
                )
            return

        # 6) S'il est membre => on demande le pseudo Dofus
        await dm_channel.send(
            "**Super nouvelle !** J’ai juste besoin d’une petite info : "
            "quel est **ton pseudo exact** sur Dofus ? 🤔"
        )

        def check_pseudo(msg: discord.Message):
            return msg.author == member and msg.channel == dm_channel

        try:
            pseudo_response = await self.bot.wait_for("message", timeout=300.0, check=check_pseudo)
            dofus_pseudo = pseudo_response.content.strip()
            print(f"[DEBUG] Pseudo Dofus : {dofus_pseudo}")
        except asyncio.TimeoutError:
            dofus_pseudo = "Inconnu"
            try:
                await dm_channel.send(
                    "Le temps est écoulé, on notera ‘Inconnu’ pour le moment. "
                    "N’hésite pas à contacter le staff plus tard ! 😅"
                )
            except discord.Forbidden:
                pass
            print("[DEBUG] Timeout pseudo => Inconnu.")
            return

        # 7) Demander le recruteur
        question_recruteur_msg = (
            "Dernière petite étape : **Qui t’a invité** à nous rejoindre ? (Pseudo Discord ou Dofus)\n\n"
            "Si tu ne te souviens plus, réponds simplement `non`."
        )
        await dm_channel.send(question_recruteur_msg)

        def check_recruteur(msg: discord.Message):
            return msg.author == member and msg.channel == dm_channel

        try:
            recruiter_response = await self.bot.wait_for("message", timeout=300.0, check=check_recruteur)
            recruiter_pseudo = recruiter_response.content.strip()
            print(f"[DEBUG] Recruteur : {recruiter_pseudo}")
        except asyncio.TimeoutError:
            recruiter_pseudo = "non"
            try:
                await dm_channel.send("Ok, aucun problème, je mettrai ‘non’ pour le recruteur. 🤷")
            except discord.Forbidden:
                pass
            print("[DEBUG] Timeout recruteur => 'non'.")

        recruitment_date = datetime.now().strftime("%d/%m/%Y")

        # 8) Renommer + Rôle Membre validé
        validated_role = discord.utils.get(member.guild.roles, name="Membre validé d'Evolution")
        try:
            await member.edit(nick=dofus_pseudo)
            print("[DEBUG] Surnom modifié.")
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[DEBUG] Impossible de renommer {member}: {e}")

        if validated_role:
            try:
                await member.add_roles(validated_role)
                print("[DEBUG] Rôle Membre validé ajouté.")
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"[DEBUG] Impossible d'ajouter le rôle Membre validé à {member}: {e}")
        else:
            await dm_channel.send(
                "Le rôle **Membre validé d'Evolution** est introuvable. Signale-le à un admin. 🚧"
            )
            print("[DEBUG] Rôle Membre validé introuvable.")

        # 9) Message de confirmation
        try:
            await dm_channel.send(
                f"**Génial, {dofus_pseudo} !** Te voilà membre officiel de la guilde *Evolution*. "
                "Bienvenue à toi et profite bien du serveur ! Si tu as la moindre question, "
                "n’hésite pas à la poser sur le salon général ou à contacter un membre du staff. 🏆"
            )
            print("[DEBUG] Message final envoyé à l'utilisateur.")
        except discord.Forbidden:
            pass

        # 10) Inscription auto dans PlayersCog si présent
        players_cog = self.bot.get_cog("PlayersCog")
        if players_cog:
            players_cog.auto_register_member(
                discord_id=member.id,
                discord_display_name=member.display_name,
                dofus_pseudo=dofus_pseudo
            )
            print("[DEBUG] Inscription auto dans PlayersCog effectuée.")
        else:
            print("[WARNING] PlayersCog introuvable, pas d'inscription auto.")

        # 11) Annonce dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥
        general_channel = discord.utils.get(member.guild.text_channels, name="𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥")
        if general_channel:
            annonce_msg_general = (
                f"🔥 **Nouvelle recrue en approche** ! 🔥\n\n"
                f"Faites un triomphe à {member.mention}, alias **{dofus_pseudo}** sur Dofus, "
                "qui rejoint officiellement nos rangs ! 🎉\n"
                "Un grand bienvenue de la part de toute la guilde ! 😃"
            )
            await general_channel.send(annonce_msg_general)
            print("[DEBUG] Annonce envoyée dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥.")
        else:
            print("[DEBUG] Canal '𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥' introuvable.")

        # 12) Annonce dans #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭
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
            print("[DEBUG] Annonce envoyée dans #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭.")
        else:
            print("[DEBUG] Canal '𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭' introuvable.")

    async def fallback_public_greeting(self, member: discord.Member):
        """
        Si les DMs sont bloqués, on envoie un message dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥 pour le prévenir qu'on n'a pas pu l'accueillir en MP.
        """
        general_channel = discord.utils.get(member.guild.text_channels, name="𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥")
        if general_channel:
            await general_channel.send(
                f"👋 {member.mention}, je n’ai pas pu t’envoyer de message privé ! "
                "Active tes MP pour finaliser l’accueil. "
                "En attendant, sois le/la bienvenu·e parmi nous ! 🎉"
            )
        else:
            print("[DEBUG] Impossible d'envoyer le fallback public : canal #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥 introuvable.")

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
