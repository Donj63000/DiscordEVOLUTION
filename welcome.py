#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
import asyncio
from discord.ext import commands
from datetime import datetime

class WelcomeCog(commands.Cog):
    """
    Cog de gestion de l'accueil des nouveaux membres sur un serveur Discord
    dédié à la guilde Evolution. À l'arrivée d'un nouveau membre, le bot :

        1. Envoie un MP de bienvenue (avec image) et demande s'il a lu le règlement.
        2. Attend la confirmation du règlement (réponse 'oui').
           - En cas de non-réponse : rappel automatisé.
        3. Demande s'il est membre de la guilde ou simple invité.
           - Si invité, lui attribue le rôle "Invités" et stoppe la procédure.
           - Si membre, poursuite : pseudo Dofus, recruteur, etc.
        4. Renomme le membre sur le serveur et lui attribue un rôle dédié (pour les membres).
        5. Annonce publiquement l’arrivée du nouveau membre dans le canal "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥".
        6. Publie dans le canal "𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭" les détails du recrutement (date, recruteur).
        7. Enregistre automatiquement le joueur via le Cog "PlayersCog" (si présent).
    """
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Se déclenche lorsqu'un membre rejoint le serveur (la guilde).
        On lui envoie un MP avec une image, on lui demande s'il a lu le règlement,
        puis s'il est invité ou membre de la guilde. Si invité, on lui donne
        le rôle "Invités" et on arrête. Sinon, on récupère son pseudo Dofus
        et le pseudo de la personne qui l'a invité dans la guilde. Enfin,
        on renomme le membre, on lui attribue le rôle de membre validé et on
        publie une annonce dans #𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥 et #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭.
        """

        # ÉTAPE 1 : Envoi d’un message privé de bienvenue + image
        try:
            dm_channel = await member.create_dm()

            bienvenue_msg = (
                f"🎉 Bienvenue dans Evolution, {member.mention} ! 🎉\n\n"
                "Nous sommes ravis de t’accueillir dans notre communauté ! Avant de te lancer, "
                "prends un moment pour consulter le règlement du serveur. Il est essentiel pour "
                "que nous puissions tous évoluer ensemble dans une bonne ambiance.\n\n"
                "📜 As-tu lu et accepté le règlement ? (Réponds simplement par oui.)\n\n"
                "⚠️ Si tu as des questions ou des doutes, n’hésite pas à contacter un membre du staff. "
                "Nous sommes là pour t’aider !"
            )

            # Envoi du fichier (image) + texte
            file = discord.File("welcome1.png", filename="welcome1.png")
            await dm_channel.send(file=file, content=bienvenue_msg)

        except discord.Forbidden:
            print(f"Impossible d’envoyer un MP à {member}.")
            return

        # ÉTAPE 2 : Attendre la confirmation du règlement (réponse "oui")
        def check_reglement(m: discord.Message):
            return (
                m.author == member
                and m.channel == dm_channel
                and m.content.lower().startswith("oui")
            )

        try:
            await self.bot.wait_for(
                "message",
                timeout=300.0,  # 5 minutes
                check=check_reglement
            )
        except asyncio.TimeoutError:
            # Message en cas de non-réponse
            try:
                rappel_msg = (
                    f"⏳ Hé, {member.mention}, tout va bien ?\n\n"
                    "Je n’ai pas encore reçu ta confirmation concernant le règlement. "
                    "Pour avancer, il te suffit de répondre **oui**.\n\n"
                    "Si tu as des questions, je suis là pour t’aider ! 😊"
                )
                await dm_channel.send(rappel_msg)
            except:
                pass
            return

        # ÉTAPE 3 : Demander s'il est "membre" ou "invité"
        try:
            invite_or_member_msg = (
                "Parfait ! Es-tu **membre** de la guilde ou simplement **invité** sur le serveur ?\n\n"
                "*(Réponds par `membre` ou `invité`.)*"
            )
            await dm_channel.send(invite_or_member_msg)

            def check_status(m: discord.Message):
                return (
                    m.author == member
                    and m.channel == dm_channel
                    and m.content.lower() in ["membre", "invité"]
                )

            status_response = await self.bot.wait_for(
                "message",
                timeout=300.0,
                check=check_status
            )
            user_status = status_response.content.lower()

        except asyncio.TimeoutError:
            # Si pas de réponse, on considère par défaut que c'est un invité
            user_status = "invité"
            try:
                await dm_channel.send(
                    "Temps écoulé. Je considérerai que tu es **invité** pour le moment."
                )
            except:
                pass

        # Gestion du cas "invité"
        if user_status == "invité":
            # On lui attribue le rôle "Invités" et on arrête la procédure
            guests_role_name = "Invités"
            guests_role = discord.utils.get(member.guild.roles, name=guests_role_name)
            if guests_role:
                try:
                    await member.add_roles(guests_role)
                    await dm_channel.send(
                        "Tu as reçu le rôle **Invités**. Si tu souhaites rejoindre la guilde plus tard, "
                        "n’hésite pas à contacter un membre du staff !"
                    )
                except Exception as e:
                    print(f"Impossible d'ajouter le rôle {guests_role_name} à {member}: {e}")
            else:
                await dm_channel.send(
                    f"Le rôle '{guests_role_name}' est introuvable. Signale-le à un administrateur."
                )

            # Fin de la procédure, on ne va pas plus loin
            return

        # Si c'est un membre, on poursuit la procédure normale :

        # ÉTAPE 4 : Demander le pseudo exact sur Dofus
        try:
            pseudo_msg = (
                "Super, bienvenue officiellement ! 🎊\n"
                "Pour finaliser ton inscription en tant que **membre** de la guilde, "
                "peux-tu me donner ton **pseudo exact** sur Dofus ?\n\n"
                "(Exemple : MonSuperPerso)"
            )
            await dm_channel.send(pseudo_msg)

            def check_pseudo(m: discord.Message):
                return m.author == member and m.channel == dm_channel

            pseudo_reponse = await self.bot.wait_for(
                "message",
                timeout=300.0,
                check=check_pseudo
            )
            dofus_pseudo = pseudo_reponse.content.strip()

        except asyncio.TimeoutError:
            try:
                await dm_channel.send("Temps écoulé. Relance la procédure plus tard si besoin.")
            except:
                pass
            return

        # ÉTAPE 5 : Demander qui l’a invité dans la guilde
        try:
            question_recruteur_msg = (
                "Parfait ! Maintenant, peux-tu m’indiquer **le pseudo Discord** ou **le pseudo Dofus** "
                "de la personne qui t’a invité dans la guilde ?\n\n"
                "Si tu ne t’en souviens pas ou n’as pas été invité par un membre en particulier, "
                "réponds simplement par `non`."
            )
            await dm_channel.send(question_recruteur_msg)

            def check_recruteur(m: discord.Message):
                return m.author == member and m.channel == dm_channel

            recruiter_response = await self.bot.wait_for(
                "message",
                timeout=300.0,
                check=check_recruteur
            )
            recruiter_pseudo = recruiter_response.content.strip()

        except asyncio.TimeoutError:
            recruiter_pseudo = "non"
            try:
                await dm_channel.send(
                    "Temps écoulé. Je considérerai que tu ne connais pas le pseudo de ton recruteur."
                )
            except:
                pass

        # Stocke la date du recrutement (format JJ/MM/AAAA)
        recruitment_date = datetime.now().strftime("%d/%m/%Y")

        # ÉTAPE 6 : Renommer le membre et lui attribuer le rôle de "Membre validé d'Evolution"
        validated_role_name = "Membre validé d'Evolution"
        validated_role = discord.utils.get(member.guild.roles, name=validated_role_name)

        # Renommage (si le bot a la permission "Manage Nicknames")
        try:
            await member.edit(nick=dofus_pseudo)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Impossible de renommer {member}. Erreur : {e}")

        # Attribution du rôle (si trouvé)
        if validated_role:
            try:
                await member.add_roles(validated_role)
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Impossible d'ajouter le rôle {validated_role_name} à {member}: {e}")
        else:
            await dm_channel.send(
                f"Le rôle '{validated_role_name}' est introuvable. Signale-le à un administrateur."
            )

        # Message de confirmation après réception des infos
        try:
            confirmation_msg = (
                f"Merci {member.mention} ! Ton pseudo Dofus **{dofus_pseudo}** est bien enregistré.\n\n"
                "🎖️ Tu es maintenant officiellement membre de la guilde Evolution. "
                "Si tu souhaites modifier ton pseudo ou rôle plus tard, contacte le staff.\n\n"
                "Bienvenue parmi nous ! 🎉"
            )
            await dm_channel.send(confirmation_msg)
        except discord.Forbidden:
            print(f"Impossible d’envoyer le message de confirmation à {member}.")

        # Inscription automatique dans la base via PlayersCog (si présent)
        players_cog = self.bot.get_cog("PlayersCog")
        if players_cog:
            players_cog.auto_register_member(
                discord_id=member.id,
                discord_display_name=member.display_name,
                dofus_pseudo=dofus_pseudo
            )
        else:
            print("[WARNING] Le Cog PlayersCog n'a pas été trouvé. L'inscription auto n'a pas été faite.")

        # ÉTAPE 7 : Annonce de bienvenue dans le canal "𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥" (sans infos de recrutement)
        general_channel = discord.utils.get(member.guild.text_channels, name="𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥")
        if general_channel:
            annonce_msg_general = (
                "🔥 Un nouvel aventurier rejoint Evolution ! 🔥\n\n"
                f"{member.mention}, alias **{dofus_pseudo}**, vient de rejoindre nos rangs.\n"
                "Faites-lui un accueil digne d’un héros ! 🏆✨\n\n"
                "N’hésite pas à lui donner quelques conseils et à l’inviter dans tes aventures. "
                "Ensemble, nous allons écrire un nouveau chapitre d’Evolution ! 🚀"
            )
            await general_channel.send(annonce_msg_general)
        else:
            print("Le canal 𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥 est introuvable. Vérifie que le nom est exact.")

        # ÉTAPE 8 : Annonce des détails du recrutement dans le canal "𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭"
        recruitment_channel = discord.utils.get(member.guild.text_channels, name="𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭")
        if recruitment_channel:
            if recruiter_pseudo.lower() == "non":
                recruiter_info = "n’a pas indiqué de recruteur"
            else:
                recruiter_info = f"a été invité par **{recruiter_pseudo}**"

            annonce_msg_recrutement = (
                f"Le joueur **{dofus_pseudo}** a rejoint la guilde le **{recruitment_date}** "
                f"et {recruiter_info}."
            )
            await recruitment_channel.send(annonce_msg_recrutement)
        else:
            print("Le canal 𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭 est introuvable. Vérifie que le nom est exact.")

def setup(bot: commands.Bot):
    """
    Méthode obligatoire pour charger ce cog dans le bot.
    Exemple d'utilisation :
        bot.load_extension('welcome')
    """
    bot.add_cog(WelcomeCog(bot))
