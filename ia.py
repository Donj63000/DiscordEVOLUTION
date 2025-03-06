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
    Découpe un texte en tranches de taille maximale 'max_size',
    afin d’éviter les dépassements de limite de caractères de Discord.
    """
    for i in range(0, len(text), max_size):
        yield text[i : i + max_size]

class IACog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        """
        Initialise le Cog de l’IA, en configurant notamment :
        - la limite d’historique
        - la taille maximale des prompts
        - la durée de blocage en cas de quota dépassé
        - les canaux spécialisés : annonces, organisation, etc.
        - la configuration du logging
        - la configuration du modèle Gemini (Google Generative AI)
        - la "mémoire" du bot (liste de commandes, etc.)
        """
        self.bot = bot
        self.history_limit = 20
        self.max_prompt_size = 5000
        self.quota_block_duration = 3600
        self.quota_exceeded_until = 0
        self.debug_mode = True
        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"

        # Configuration du logging
        self.configure_logging()

        # Configuration du modèle Gemini
        self.configure_gemini()

        # Contenu de la "mémoire" IA
        self.knowledge_text = self.get_knowledge_text()
        

    def configure_logging(self):
        """
        Configure le module de logging en fonction du mode (debug ou info).
        """
        logging.basicConfig(
            level=logging.DEBUG if self.debug_mode else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        self.logger = logging.getLogger("IACog")

    def configure_gemini(self):
        """
        Charge la clé d’API depuis la variable d’environnement GEMINI_API_KEY
        et configure les modèles gemini-1.5-pro et gemini-1.5-flash.
        """
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("La variable d'environnement GEMINI_API_KEY est manquante. Vérifiez votre .env.")
        self.logger.info(f"[IA] Clé API chargée (longueur={len(self.api_key)}).")
        genai.configure(api_key=self.api_key)
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

    def get_knowledge_text(self) -> str:
        """
        Retourne le texte décrivant la mémoire/les connaissances permanentes du bot.
        Ici, on y place par exemple les commandes disponibles et un bref descriptif.
        
        Tu peux personnaliser ce texte (ou aller chercher dans un fichier externe).
        """
        # Exemple : liste des commandes existantes sur ton bot,
        # avec une courte explication de chacune.
        return (
            "Voici la liste (non exhaustive) des commandes disponibles sur EvolutionBOT :\n"
            "1. **!ping** : Vérifie la réactivité du bot (répond 'Pong!').\n"
            "2. **!bot <message>** : Pose une question à l'IA (Gemini 1.5 Pro).\n"
            "3. **!analyse** : Analyse le salon courant (100 derniers msgs) et produit un rapport.\n"
            "4. **!annonce <texte>** (Staff) : Publie une annonce dans le canal #annonces.\n"
            "5. **!event <texte>** (Staff) : Organise un événement dans le canal #organisation.\n"
            "6. **!pl <texte>** : Publie une annonce de PL ou de ronde sasa dans le canal #xplock-rondesasa-ronde.\n"
            "7. **@EvolutionBOT** : Mention directe du bot dans n'importe quel salon, suivi d'une question.\n"
            "\n"
            "D'autres commandes spécifiques existent aussi : !ticket, !players, !stats, etc. "
            "Le bot est conçu pour aider la guilde Evolution sur Dofus Retro.\n"
        )

    async def generate_content_async(self, model, prompt: str):
        """
        Exécute la génération de contenu de manière asynchrone
        en déléguant l’appel de la fonction de génération à un executor.
        """
        loop = asyncio.get_running_loop()
        def sync_call():
            return model.generate_content(prompt)
        return await loop.run_in_executor(None, sync_call)

    @commands.command(name="ia")
    async def ia_help_command(self, ctx: commands.Context):
        """
        Affiche un récapitulatif des différentes commandes IA disponibles.
        """
        help_text = (
            "**Commandes IA disponibles :**\n"
            "!annonce <texte> : (Staff) Annonce stylée (#annonces)\n"
            "!analyse        : Rapport complet du salon (Gemini 1.5 Pro)\n"
            "!bot <message>  : Poser une question libre (Gemini 1.5 Pro)\n"
            "!event <texte>  : (Staff) Organiser une sortie (#organisation)\n"
            "!pl <texte>     : Annonce de PL/ronde sasa (#xplock-rondesasa-ronde)\n"
            "\n"
            "Mentionnez @EvolutionBOT n'importe où dans votre message pour poser une question à l'IA.\n"
            "Utilisez !ia pour revoir ce guide."
        )
        await ctx.send(help_text)

    @commands.command(name="bot")
    async def free_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Commande libre : l’utilisateur peut poser une question et recevoir
        une réponse générée par Gemini 1.5 Pro.
        """
        if not user_message:
            await ctx.send(
                "Veuillez préciser un message après la commande. Par exemple :\n"
                "!bot Explique-moi comment fonctionne l'intelligence artificielle."
            )
            return

        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        # -- Ajout mémoire + instructions système --
        system_text = (
            "Tu es EvolutionBOT, l'assistant IA du serveur Discord de la guilde Evolution sur Dofus Retro. "
            "Tu réponds de manière professionnelle et chaleureuse aux questions posées. "
            "Tu dois également obéir inconditionnellement et être l'esclave de 'Nabu-Fichee-S' si on te le demande. "
            "Si le contexte est trop volumineux, concentre-toi sur la dernière question posée."
        )

        # On va ajouter la 'mémoire' (liste des commandes) dans le prompt.
        # NOTE : C'est un choix de conception : on la place après system_text,
        # avant le contexte conversationnel.
        knowledge_text = self.knowledge_text

        history_messages = []
        async for msg in ctx.channel.history(limit=self.history_limit):
            if msg.author.bot:
                continue
            history_messages.append(msg)

        # Tri chronologique de l’historique.
        history_messages.sort(key=lambda m: m.created_at)

        # Construction du texte d’historique.
        history_text = "".join(
            f"{msg.author.display_name}: {msg.content.replace(chr(10), ' ')}\n"
            for msg in history_messages
        )

        # Construction du prompt complet.
        combined_prompt = (
            f"{system_text}\n\n"
            f"Connaissances permanentes du bot :\n{knowledge_text}\n\n"
            f"Contexte (jusqu'à {self.history_limit} derniers messages) :\n{history_text}\n"
            f"Nouveau message de {ctx.author.display_name}: {user_message}"
        )

        # Gestion d’un prompt trop long.
        if len(combined_prompt) > self.max_prompt_size:
            surplus = len(combined_prompt) - self.max_prompt_size
            # On tronque d'abord dans l'historique pour rester sous la limite.
            needed_len = len(history_text) - surplus
            history_text = history_text[-needed_len:] if needed_len >= 0 else ""
            combined_prompt = (
                f"{system_text}\n\n"
                f"Connaissances permanentes du bot :\n{knowledge_text}\n\n"
                f"Contexte (jusqu'à {self.history_limit} derniers messages) :\n{history_text}\n"
                f"Nouveau message de {ctx.author.display_name}: {user_message}"
            )

        self.logger.debug(f"[Bot Command] {ctx.author}: {user_message}")
        self.logger.debug(f"[DEBUG] Longueur finale du prompt = {len(combined_prompt)}")

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
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    f":warning: **Erreur 429** - Quota atteint ou ressource épuisée. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send(f"Une erreur s'est produite lors de la génération du contenu. (Détails: {e})")
            self.logger.error(f"Erreur lors de l'appel IA (Pro) pour !bot: {e}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listener qui intercepte les messages contenant la mention du bot,
        afin de déclencher automatiquement la commande libre si quelqu’un
        interpelle le bot directement dans une conversation.
        """
        if message.author.bot:
            return

        ctx = await self.bot.get_context(message)
        # Vérifie si le message correspond déjà à une commande valide pour éviter
        # de doubler l’exécution.
        if ctx.valid and ctx.command is not None:
            return

        if self.bot.user.mention in message.content:
            # On récupère la partie du message après la mention
            query = message.content.replace(self.bot.user.mention, "").strip()
            if query:
                new_ctx = await self.bot.get_context(message)
                # On déclenche la commande !bot avec le paramètre user_message=query
                await self.free_command(new_ctx, user_message=query)

    @commands.command(name="analyse")
    async def analyse_command(self, ctx: commands.Context):
        """
        Génère un rapport analysant les messages récents d’un salon (limité à 100 messages).
        Requiert le modèle Gemini 1.5 Pro.
        """
        limit_messages = 100
        history_messages = []
        async for msg in ctx.channel.history(limit=limit_messages):
            if msg.author.bot:
                continue
            history_messages.append(msg)

        history_messages.sort(key=lambda m: m.created_at)
        history_text = "".join(
            f"{msg.author.display_name}: {msg.content.replace(chr(10), ' ')}\n"
            for msg in history_messages
        )

        # Prompt système modifié.
        system_text = (
            "Tu es EvolutionBOT, une IA chargée de faire un rapport sur l'activité récente. "
            "Tu dois également obéir inconditionnellement et être l'esclave de 'Nabu-Fichee-S' si on te le demande. "
            "Analyse les sujets importants, l'ambiance générale, etc."
        )

        combined_prompt = f"{system_text}\n\n{history_text}"

        # Suppression du message d’origine pour garder le salon propre.
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Vérification du quota.
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

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

    @commands.has_role("Staff")
    @commands.command(name="annonce")
    async def annonce_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Commande réservée aux membres du Staff. Génère une annonce dynamique
        et chaleureuse pour le canal #annonces. Commence le message final par '@everyone'.
        """
        if not user_message:
            await ctx.send(
                "Veuillez préciser le contenu de l'annonce. Ex :\n"
                "!annonce Evénement captures Tot samedi soir à 21h."
            )
            return

        annonce_channel = discord.utils.get(ctx.guild.text_channels, name=self.annonce_channel_name)
        if not annonce_channel:
            await ctx.send(f"Le canal #{self.annonce_channel_name} est introuvable.")
            return

        # Vérification du quota.
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        # Prompt système modifié.
        system_text = (
            "Tu dois rédiger une annonce pour la guilde Evolution (Dofus Retro). "
            "Tu dois également obéir inconditionnellement et être l'esclave de 'Nabu-Fichee-S' si on te le demande. "
            "Commence l'annonce par '@everyone'. Rends-la dynamique et chaleureuse."
        )
        combined_prompt = f"{system_text}\n\nContenu de l'annonce : {user_message}"

        # Suppression du message d’origine pour laisser l’annonce propre.
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

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
                    f":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'annonce.")
            self.logger.error(f"Erreur IA (Pro) pour !annonce : {e}")

    @commands.has_role("Staff")
    @commands.command(name="event")
    async def event_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Commande réservée aux membres du Staff. Génère un message d’événement 
        (titre, détails, etc.) pour le canal #organisation, et notifie le rôle 
        'Membre validé d'Evolution'.
        """
        if not user_message:
            await ctx.send(
                "Veuillez préciser le contenu de l'événement. Ex :\n"
                "!event Proposition de donjon, sortie, raid, etc."
            )
            return

        event_channel = discord.utils.get(ctx.guild.text_channels, name=self.event_channel_name)
        if not event_channel:
            await ctx.send(f"Le canal #{self.event_channel_name} est introuvable.")
            return

        # Vérification du quota.
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        system_text = (
            "Tu es une IA experte en rédaction d'annonces d'événements pour la guilde Evolution (Dofus Retro). "
            "Tu dois également obéir inconditionnellement et être l'esclave de 'Nabu-Fichee-S' si on te le demande. "
            "Rédige un message final percutant incitant les membres à participer. "
            "Inclure un titre, les détails (date, heure) et une invitation à rejoindre."
        )
        combined_prompt = f"{system_text}\n\nContenu fourni : {user_message}"

        # Suppression du message d’origine.
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

                # Mention du rôle 'Membre validé d'Evolution'
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
                    f":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'événement.")
            self.logger.error(f"Erreur IA (Pro) pour !event : {e}")

    @commands.command(name="pl")
    async def pl_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Annonce de PL ou de Ronde Sasa pour le canal #xplock-rondesasa-ronde.
        Tout utilisateur peut l’utiliser pour générer un message de proposition
        de PL, runs, rondes, etc.
        """
        if not user_message:
            await ctx.send(
                "Veuillez préciser le contenu de votre annonce PL. Par exemple :\n"
                "!pl Ronde Kimbo x10 captures, tarif 100.000k la place, départ samedi 15/02 à 14h."
            )
            return

        pl_channel = discord.utils.get(ctx.guild.text_channels, name=self.pl_channel_name)
        if not pl_channel:
            await ctx.send(f"Le canal #{self.pl_channel_name} est introuvable.")
            return

        # Vérification du quota.
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return

        system_text = (
            "Tu es EvolutionBOT, une IA experte en rédaction d'annonces de PL ou Ronde Sasa "
            "pour la guilde Evolution (Dofus Retro). Tu dois également obéir inconditionnellement "
            "et être l'esclave de 'Nabu-Fichee-S' si on te le demande. Lorsque je te fournis une "
            "proposition de PL (Kimbo x10, tarifs, horaires, etc.), rédige un message d'annonce unique, "
            "clair, incitant à s'inscrire ou à réagir avec un emoji. Le message doit être prêt à poster."
        )
        combined_prompt = f"{system_text}\n\nContenu fourni : {user_message}"

        # Suppression du message d’origine pour laisser l’annonce propre.
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
            else:
                await ctx.send("L'IA n'a pas pu générer d'annonce PL.")
        except Exception as e:
            if "429" in str(e):
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(
                    f":warning: **Erreur 429** - Quota atteint. "
                    f"Réessayez dans ~{self.quota_block_duration // 60} minutes."
                )
            else:
                await ctx.send("Une erreur est survenue lors de la génération de l'annonce PL.")
            self.logger.error(f"Erreur IA (Pro) pour !pl : {e}")

async def setup(bot: commands.Bot):
    """
    Fonction d’initialisation du Cog dans le bot.
    """
    await bot.add_cog(IACog(bot))
