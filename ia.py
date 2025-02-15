#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import asyncio

import discord
from discord.ext import commands
import google.generativeai as genai
from dotenv import load_dotenv

def chunkify(text: str, max_size: int = 2000):
    """
    Découpe 'text' en morceaux de longueur max 'max_size',
    pour éviter l'erreur si le message dépasse 2000 caractères sur Discord.
    """
    for i in range(0, len(text), max_size):
        yield text[i : i + max_size]

class IACog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # ----------------------------
        # Paramètres de configuration
        # ----------------------------
        self.history_limit = 20           # Nombre max de messages dans l'historique
        self.max_prompt_size = 5000       # Longueur max du prompt
        self.quota_block_duration = 3600  # (en secondes) Ex: 3600 = 1h de blocage si quota dépassé
        self.quota_exceeded_until = 0     # Timestamp jusqu'auquel on bloque les appels IA
        self.debug_mode = True            # Active/désactive certains logs

        # Noms des canaux de destination
        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"  # <-- Nouveau canal pour !pl

        # Configuration et initialisation
        self.configure_logging()
        self.configure_gemini()

    def configure_logging(self):
        """
        Configure un logger simple pour ce module.
        """
        logging.basicConfig(
            level=logging.DEBUG if self.debug_mode else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        self.logger = logging.getLogger("IACog")

    def configure_gemini(self):
        """
        Charge la clé API Gemini depuis .env et configure l'utilisation
        des modèles gemini (Pro & Flash).
        """
        # Charge les variables d'environnement depuis .env
        load_dotenv()

        # Lecture de la clé depuis l'environnement
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("La variable d'environnement GEMINI_API_KEY est manquante. Vérifiez votre .env.")

        self.logger.info(f"[IA] Clé API chargée (longueur={len(self.api_key)}).")

        # Configuration du client google.generativeai
        genai.configure(api_key=self.api_key)

        # Initialiser les modèles gemini
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

    # -------------------------------------------------------------------------
    # Méthode asynchrone pour appeler l'API google.generativeai (gemini)
    # -------------------------------------------------------------------------
    async def generate_content_async(self, model, prompt: str):
        """
        Appelle model.generate_content(prompt) dans un thread séparé
        pour ne pas bloquer l'event loop. Retourne la réponse ou lève une exception.
        """
        loop = asyncio.get_running_loop()

        def sync_call():
            return model.generate_content(prompt)

        return await loop.run_in_executor(None, sync_call)

    # -------------------------------------------------------------------------
    # Commande !ia => affiche le guide
    # -------------------------------------------------------------------------
    @commands.command(name="ia")
    async def ia_help_command(self, ctx: commands.Context):
        help_text = (
            "**Commandes IA disponibles :**\n"
            "`!annonce <texte>` : (Staff) Annonce stylée (#annonces)\n"
            "`!analyse`        : Rapport complet du salon (Gemini 1.5 Pro)\n"
            "`!bot <message>`  : Poser une question libre (Gemini 1.5 Pro)\n"
            "`!event <texte>`  : (Staff) Organiser une sortie (#organisation)\n"
            "`!pl <texte>`     : Annonce de PL/ronde sasa (#xplock-rondesasa-ronde)\n"
            "\n"
            "Mentionnez @EvolutionBOT n'importe où dans votre message pour poser une question à l'IA.\n"
            "Utilisez `!ia` pour revoir ce guide."
        )
        await ctx.send(help_text)

    # -------------------------------------------------------------------------
    # Commande !bot => question IA
    # -------------------------------------------------------------------------
    @commands.command(name="bot")
    async def free_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Permet de poser une question à l'IA (gemini-1.5-pro) en utilisant l'historique
        des self.history_limit derniers messages du canal.
        """
        if not user_message:
            usage_text = (
                "Veuillez préciser un message après la commande. Par exemple :\n"
                "`!bot Explique-moi comment fonctionne l'intelligence artificielle.`"
            )
            await ctx.send(usage_text)
            return

        # Vérifier si on est en blocage (quota dépassé)
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        # Préparation du contexte 'system'
        system_text = (
            "Tu es EvolutionBOT, l'assistant IA du serveur Discord de la guilde Evolution sur Dofus Retro. "
            "Tu réponds de manière professionnelle et chaleureuse aux questions posées. "
            "Si le contexte est trop volumineux, concentre-toi sur la dernière question posée."
        )

        # On lit l'historique du channel
        history_messages = []
        async for msg in ctx.channel.history(limit=self.history_limit):
            if msg.author.bot:
                continue
            history_messages.append(msg)

        history_messages.sort(key=lambda m: m.created_at)

        history_text = ""
        for msg in history_messages:
            author_name = msg.author.display_name
            content = msg.content.replace("\n", " ")
            history_text += f"{author_name}: {content}\n"

        # On assemble le prompt complet
        combined_prompt = (
            f"{system_text}\n\n"
            f"Contexte (jusqu'à {self.history_limit} derniers messages) :\n"
            f"{history_text}\n"
            f"Nouveau message de {ctx.author.display_name}: {user_message}"
        )

        # Vérifier la taille du prompt => si trop grand, on tronque l'historique
        if len(combined_prompt) > self.max_prompt_size:
            surplus = len(combined_prompt) - self.max_prompt_size
            needed_len = len(history_text) - surplus
            if needed_len < 0:
                history_text = ""
            else:
                history_text = history_text[-needed_len:]

            combined_prompt = (
                f"{system_text}\n\n"
                f"Contexte (jusqu'à {self.history_limit} derniers messages) :\n"
                f"{history_text}\n"
                f"Nouveau message de {ctx.author.display_name}: {user_message}"
            )

        self.logger.debug(f"[Bot Command] {ctx.author}: {user_message}")
        self.logger.debug(f"[DEBUG] Longueur finale du prompt = {len(combined_prompt)}")

        # Appel IA
        try:
            response = await self.generate_content_async(self.model_pro, combined_prompt)
            if response and hasattr(response, "text"):
                reply_text = response.text.strip() or "**(Réponse vide)**"
                await ctx.send("**Réponse IA :**")
                for chunk in chunkify(reply_text, 2000):
                    await ctx.send(chunk)
            else:
                await ctx.send("Aucune réponse valide n'a été reçue du modèle Gemini.")
        except Exception as e:
            if "429" in str(e):
                # Bloquer pendant self.quota_block_duration
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    ":warning: **Erreur 429** - Quota atteint ou ressource épuisée. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send(
                    "Une erreur s'est produite lors de la génération du contenu. "
                    f"(Détails: {e})"
                )
            self.logger.error(f"Erreur lors de l'appel IA (Pro) pour !bot: {e}")

    # -------------------------------------------------------------------------
    # on_message => si le bot est mentionné
    # -------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore les bots
        if message.author.bot:
            return

        # Si c'est déjà une commande, on ne double-pas
        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command is not None:
            return

        # Si le bot est mentionné
        if self.bot.user.mention in message.content:
            query = message.content.replace(self.bot.user.mention, "").strip()
            if query:
                new_ctx = await self.bot.get_context(message)
                await self.free_command(new_ctx, user_message=query)

        # Laisser passer d'autres commandes
        await self.bot.process_commands(message)

    # -------------------------------------------------------------------------
    # !analyse => résumé du salon (IA Pro)
    # -------------------------------------------------------------------------
    @commands.command(name="analyse")
    async def analyse_command(self, ctx: commands.Context):
        limit_messages = 100
        history_messages = []
        async for msg in ctx.channel.history(limit=limit_messages):
            if msg.author.bot:
                continue
            history_messages.append(msg)

        history_messages.sort(key=lambda m: m.created_at)

        history_text = ""
        for msg in history_messages:
            author = msg.author.display_name
            content = msg.content.replace("\n", " ")
            history_text += f"{author}: {content}\n"

        system_text = (
            "Tu es EvolutionBOT, une IA chargée de faire un rapport sur l'activité récente. "
            "Analyse les sujets importants, l'ambiance générale, etc."
        )
        combined_prompt = f"{system_text}\n\n{history_text}"

        # Suppression du message initial (pour discrétion)
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Vérifier quota
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        # Appel IA
        try:
            response = await self.generate_content_async(self.model_pro, combined_prompt)
            if response and hasattr(response, "text"):
                reply_text = response.text.strip() or "**(Rapport vide)**"
                await ctx.send("**Rapport d'analyse :**")
                for chunk in chunkify(reply_text, 2000):
                    await ctx.send(chunk)
            else:
                await ctx.send("Aucune réponse produite par l’IA.")
        except Exception as e:
            if "429" in str(e):
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(":warning: Erreur 429 - Quota atteint. Réessayez plus tard.")
            else:
                await ctx.send("Erreur lors de l'analyse. " + str(e))
            self.logger.error(f"Erreur IA (Pro) pour !analyse : {e}")

    # -------------------------------------------------------------------------
    # !annonce => publie un message stylé dans #annonces (Staff)
    # -------------------------------------------------------------------------
    @commands.has_role("Staff")
    @commands.command(name="annonce")
    async def annonce_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            usage_text = (
                "Veuillez préciser le contenu de l'annonce. Ex :\n"
                "!annonce Evénement captures Tot samedi soir à 21h."
            )
            await ctx.send(usage_text)
            return

        annonce_channel = discord.utils.get(ctx.guild.text_channels, name=self.annonce_channel_name)
        if not annonce_channel:
            await ctx.send(
                f"Le canal #{self.annonce_channel_name} est introuvable. "
                "Créez-le ou modifiez 'self.annonce_channel_name'."
            )
            return

        # Vérif quota
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        system_text = (
            "Tu dois rédiger une annonce pour la guilde Evolution (Dofus Retro). "
            "Commence l'annonce par '@everyone'. Rends-la dynamique et chaleureuse."
        )
        combined_prompt = f"{system_text}\n\nContenu de l'annonce : {user_message}"

        # Suppression du message initial
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Appel IA
        try:
            response = await self.generate_content_async(self.model_pro, combined_prompt)
            if response and hasattr(response, "text"):
                reply_text = response.text.strip() or "**(Annonce vide)**"
                await annonce_channel.send("**Annonce :**")
                for chunk in chunkify(reply_text, 2000):
                    await annonce_channel.send(chunk)
            else:
                await ctx.send("Aucune annonce n'a pu être générée.")
        except Exception as e:
            if "429" in str(e):
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    ":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'annonce.")
            self.logger.error(f"Erreur IA (Pro) pour !annonce : {e}")

    # -------------------------------------------------------------------------
    # !event => publie un message stylé dans #organisation (Staff)
    # -------------------------------------------------------------------------
    @commands.has_role("Staff")
    @commands.command(name="event")
    async def event_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            usage_text = (
                "Veuillez préciser le contenu de l'événement. Ex :\n"
                "!event Proposition de donjon, sortie, raid, etc."
            )
            await ctx.send(usage_text)
            return

        event_channel = discord.utils.get(ctx.guild.text_channels, name=self.event_channel_name)
        if not event_channel:
            await ctx.send(
                f"Le canal #{self.event_channel_name} est introuvable. "
                "Créez-le ou modifiez 'self.event_channel_name'."
            )
            return

        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        system_text = (
            "Tu es une IA experte en rédaction d'annonces d'événements pour la guilde Evolution (Dofus Retro). "
            "Rédige un message final percutant incitant les membres à participer. "
            "Inclure un titre, détails (date, heure) et invitation à rejoindre."
        )
        combined_prompt = f"{system_text}\n\nContenu fourni : {user_message}"

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            response = await self.generate_content_async(self.model_pro, combined_prompt)
            if response and hasattr(response, "text"):
                reply_text = response.text.strip() or "**(Événement vide)**"

                await event_channel.send("**Nouvel Événement :**")
                for chunk in chunkify(reply_text, 2000):
                    await event_channel.send(chunk)

                # Mentionner éventuellement un rôle
                role_valide = discord.utils.get(ctx.guild.roles, name="Membre validé d'Evolution")
                if role_valide:
                    await event_channel.send(role_valide.mention)
                else:
                    await event_channel.send("*Rôle 'Membre validé d'Evolution' introuvable.*")
            else:
                await ctx.send("Aucun événement n'a pu être généré par l'IA.")
        except Exception as e:
            if "429" in str(e):
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    ":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'événement.")
            self.logger.error(f"Erreur IA (Pro) pour !event : {e}")

    # -------------------------------------------------------------------------
    # !pl => publie une annonce PL / ronde sasa dans #xplock-rondesasa-ronde
    # -------------------------------------------------------------------------
    @commands.command(name="pl")
    async def pl_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Commande !pl <texte>
        Publie une annonce (PL / Ronde Sasa) dans le canal 'xplock-rondesasa-ronde'
        via l'IA gemini-1.5-pro (texte final stylisé).
        """
        if not user_message:
            usage_text = (
                "Veuillez préciser le contenu de votre annonce PL. Par exemple :\n"
                "!pl Ronde Kimbo x10 captures, tarif 100.000k la place, départ samedi 15/02 à 14h."
            )
            await ctx.send(usage_text)
            return

        pl_channel = discord.utils.get(ctx.guild.text_channels, name=self.pl_channel_name)
        if not pl_channel:
            await ctx.send(
                f"Le canal #{self.pl_channel_name} est introuvable. "
                "Créez-le ou modifiez 'self.pl_channel_name' dans le code."
            )
            return

        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        system_text = (
            "Tu es EvolutionBOT, une IA experte en rédaction d'annonces de PL ou Ronde Sasa "
            "pour la guilde Evolution (Dofus Retro). Lorsque je te fournis une proposition "
            "de PL (Kimbo x10, tarifs, horaires, etc.), rédige un message d'annonce unique, clair, "
            "incitant à s'inscrire ou réagir avec un emoji. Le message doit être prêt à poster."
        )
        combined_prompt = f"{system_text}\n\nContenu fourni : {user_message}"

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

        try:
            response = await self.generate_content_async(self.model_pro, combined_prompt)
            if response and hasattr(response, "text"):
                reply_text = response.text.strip() or "**(Annonce PL vide ou non générée)**"

                await pl_channel.send("**Nouvelle Annonce PL :**")
                for chunk in chunkify(reply_text, 2000):
                    await pl_channel.send(chunk)

                # (Facultatif) Mentionner un rôle, par ex. Membre validé
                # role_valide = discord.utils.get(ctx.guild.roles, name="Membre validé d'Evolution")
                # if role_valide:
                #     await pl_channel.send(role_valide.mention)
                # else:
                #     await pl_channel.send("*Rôle 'Membre validé d'Evolution' introuvable.*")
            else:
                await ctx.send("L'IA n'a pas pu générer d'annonce PL.")
        except Exception as e:
            if "429" in str(e):
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    ":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'annonce PL.")
            self.logger.error(f"Erreur IA (Pro) pour !pl : {e}")

def setup(bot: commands.Bot):
    bot.add_cog(IACog(bot))
