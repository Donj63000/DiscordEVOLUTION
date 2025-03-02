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
    for i in range(0, len(text), max_size):
        yield text[i : i + max_size]

def check_quota(func):
    async def wrapper(self, ctx, *args, **kwargs):
        if time.time() < self.quota_exceeded_until:
            wait_secs = int(self.quota_exceeded_until - time.time())
            await ctx.send(f"**Quota IA dépassé**. Réessayez dans ~{wait_secs} secondes, svp.")
            return
        return await func(self, ctx, *args, **kwargs)
    return wrapper

class IACog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.history_limit = 20
        self.max_prompt_size = 5000
        self.quota_block_duration = 3600
        self.quota_exceeded_until = 0
        self.debug_mode = True
        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"
        self.configure_logging()
        self.configure_gemini()

    def configure_logging(self):
        logging.basicConfig(
            level=logging.DEBUG if self.debug_mode else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        self.logger = logging.getLogger("IACog")

    def configure_gemini(self):
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("La variable d'environnement GEMINI_API_KEY est manquante. Vérifiez votre .env.")
        self.logger.info(f"[IA] Clé API chargée (longueur={len(self.api_key)}).")
        genai.configure(api_key=self.api_key)
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

    async def generate_content_async(self, model, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        def sync_call():
            return model.generate_content(prompt)
        try:
            response = await loop.run_in_executor(None, sync_call)
            if response and hasattr(response, "text"):
                return response.text.strip() or "**(Réponse vide)**"
            return "**(Réponse vide)**"
        except Exception as e:
            raise e

    async def get_ia_response(self, model, prompt: str, ctx: commands.Context) -> str:
        self.logger.debug(f"[DEBUG] Longueur du prompt = {len(prompt)}")
        try:
            reply_text = await self.generate_content_async(model, prompt)
            return reply_text
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                self.quota_exceeded_until = time.time() + self.quota_block_duration
                await ctx.send(f":warning: **Erreur 429** - Quota atteint ou ressource épuisée. Réessayez dans ~{self.quota_block_duration // 60} minutes.")
            else:
                await ctx.send(f"Une erreur s'est produite lors de la génération du contenu. (Détails: {e})")
            self.logger.error(f"Erreur lors de l'appel IA : {e}")
            return ""

    def build_system_prompt(self, context_label: str) -> str:
        base_prompt = (
            "Tu es EvolutionBOT, l'assistant IA de la guilde Evolution sur Dofus Retro. "
            "Tu réponds de manière claire et amicale, tout en restant précis, rigoureux et utile. "
            "Adapte toujours ton ton et ta formulation au contexte, et reste concis si le texte est trop long. "
            "Si tu manques d'informations, indique-le poliment. "
        )
        if context_label == "general":
            base_prompt += "Réponds aux questions libres en intégrant le contexte récent du salon."
        elif context_label == "analysis":
            base_prompt += "Analyse les derniers messages pour en faire un résumé pertinent et expliquer l'ambiance."
        elif context_label == "annonce":
            base_prompt += "Rédige une annonce pour la guilde, commence par @everyone, mets de l'enthousiasme et incite les membres à participer."
        elif context_label == "event":
            base_prompt += "Rédige une annonce d'événement avec titre, date, heure et motivation pour la guilde."
        elif context_label == "pl":
            base_prompt += "Propose une annonce pour un PL ou une ronde sasa, en décrivant clairement les détails."
        else:
            base_prompt += "Réponds simplement de façon informative."
        return base_prompt

    @commands.command(name="ia")
    async def ia_help_command(self, ctx: commands.Context):
        help_text = (
            "**Commandes IA disponibles :**\n"
            "`!annonce <texte>` : (Staff) Annonce stylée (#annonces)\n"
            "`!analyse`        : Rapport complet du salon (Gemini 1.5 Pro)\n"
            "`!bot <message>`  : Poser une question libre (Gemini 1.5 Pro)\n"
            "`!botflash <msg>` : Version flash du bot (Gemini 1.5 Flash)\n"
            "`!event <texte>`  : (Staff) Organiser une sortie (#organisation)\n"
            "`!pl <texte>`     : Annonce de PL/ronde sasa (#xplock-rondesasa-ronde)\n"
            "\n"
            "Mentionnez @EvolutionBOT n'importe où dans votre message pour poser une question à l'IA.\n"
            "Utilisez `!ia` pour revoir ce guide."
        )
        await ctx.send(help_text)

    @commands.command(name="bot")
    @check_quota
    async def free_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            await ctx.send("Veuillez préciser un message après la commande. Par exemple : `!bot Explique-moi comment fonctionne l'intelligence artificielle.`")
            return
        system_text = self.build_system_prompt("general")
        history_messages = []
        async for msg in ctx.channel.history(limit=self.history_limit):
            if msg.author.bot:
                continue
            history_messages.append(msg)
        history_messages.sort(key=lambda m: m.created_at)
        history_text = "".join(f"{msg.author.display_name}: {msg.content.replace(chr(10), ' ')}\n" for msg in history_messages)
        combined_prompt = f"{system_text}\nVoici l'historique (jusqu'à {self.history_limit} derniers messages) :\n{history_text}\nNouveau message de {ctx.author.display_name}: {user_message}\n"
        if len(combined_prompt) > self.max_prompt_size:
            surplus = len(combined_prompt) - self.max_prompt_size
            needed_len = len(history_text) - surplus
            history_text = history_text[-needed_len:] if needed_len >= 0 else ""
            combined_prompt = f"{system_text}\nVoici l'historique (trunc) :\n{history_text}\nNouveau message de {ctx.author.display_name}: {user_message}\n"
        reply_text = await self.get_ia_response(self.model_pro, combined_prompt, ctx)
        if reply_text:
            await ctx.send("**Réponse IA :**")
            for chunk in chunkify(reply_text, 2000):
                await ctx.send(chunk)

    @commands.command(name="botflash")
    @check_quota
    async def flash_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            await ctx.send("Veuillez préciser un message après la commande. Par exemple : `!botflash Combien coûtent les potions de vitalité ?`")
            return
        system_text = self.build_system_prompt("general")
        combined_prompt = f"{system_text}\nQuestion de l'utilisateur : {user_message}"
        if len(combined_prompt) > self.max_prompt_size:
            combined_prompt = combined_prompt[:self.max_prompt_size]
        reply_text = await self.get_ia_response(self.model_flash, combined_prompt, ctx)
        if reply_text:
            await ctx.send("**Réponse IA (flash) :**")
            for chunk in chunkify(reply_text, 2000):
                await ctx.send(chunk)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command is not None:
            return
        if self.bot.user.mention in message.content:
            query = message.content.replace(self.bot.user.mention, "").strip()
            if query:
                new_ctx = await self.bot.get_context(message)
                await self.free_command(new_ctx, user_message=query)

    @commands.command(name="analyse")
    @check_quota
    async def analyse_command(self, ctx: commands.Context):
        limit_messages = 100
        history_messages = []
        async for msg in ctx.channel.history(limit=limit_messages):
            if msg.author.bot:
                continue
            history_messages.append(msg)
        history_messages.sort(key=lambda m: m.created_at)
        history_text = "".join(f"{msg.author.display_name}: {msg.content.replace(chr(10), ' ')}\n" for msg in history_messages)
        system_text = self.build_system_prompt("analysis")
        combined_prompt = f"{system_text}\n\n{history_text}"
        try:
            await ctx.message.delete()
        except Exception:
            pass
        reply_text = await self.get_ia_response(self.model_pro, combined_prompt, ctx)
        if reply_text:
            await ctx.send("**Rapport d'analyse :**")
            for chunk in chunkify(reply_text, 2000):
                await ctx.send(chunk)

    @commands.has_role("Staff")
    @commands.command(name="annonce")
    @check_quota
    async def annonce_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            await ctx.send("Veuillez préciser le contenu de l'annonce. Ex : `!annonce Evénement captures Tot samedi soir à 21h.`")
            return
        annonce_channel = discord.utils.get(ctx.guild.text_channels, name=self.annonce_channel_name)
        if not annonce_channel:
            await ctx.send(f"Le canal #{self.annonce_channel_name} est introuvable.")
            return
        system_text = self.build_system_prompt("annonce")
        combined_prompt = f"{system_text}\nContenu de l'annonce : {user_message}"
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        reply_text = await self.get_ia_response(self.model_pro, combined_prompt, ctx)
        if reply_text:
            await annonce_channel.send("**Annonce :**")
            for chunk in chunkify(reply_text, 2000):
                await annonce_channel.send(chunk)

    @commands.has_role("Staff")
    @commands.command(name="event")
    @check_quota
    async def event_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            await ctx.send("Veuillez préciser le contenu de l'événement. Ex : `!event Proposition de donjon, sortie, raid, etc.`")
            return
        event_channel = discord.utils.get(ctx.guild.text_channels, name=self.event_channel_name)
        if not event_channel:
            await ctx.send(f"Le canal #{self.event_channel_name} est introuvable.")
            return
        system_text = self.build_system_prompt("event")
        combined_prompt = f"{system_text}\nContenu fourni : {user_message}"
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        reply_text = await self.get_ia_response(self.model_pro, combined_prompt, ctx)
        if reply_text:
            await event_channel.send("**Nouvel Événement :**")
            for chunk in chunkify(reply_text, 2000):
                await event_channel.send(chunk)
            role_valide = discord.utils.get(ctx.guild.roles, name="Membre validé d'Evolution")
            if role_valide:
                await event_channel.send(role_valide.mention)
            else:
                await event_channel.send("*Rôle 'Membre validé d'Evolution' introuvable.*")

    @commands.command(name="pl")
    @check_quota
    async def pl_command(self, ctx: commands.Context, *, user_message: str = None):
        if not user_message:
            await ctx.send("Veuillez préciser le contenu de votre annonce PL. Par exemple : `!pl Ronde Kimbo x10 captures, tarif 100.000k la place, départ samedi 15/02 à 14h.`")
            return
        pl_channel = discord.utils.get(ctx.guild.text_channels, name=self.pl_channel_name)
        if not pl_channel:
            await ctx.send(f"Le canal #{self.pl_channel_name} est introuvable.")
            return
        system_text = self.build_system_prompt("pl")
        combined_prompt = f"{system_text}\nContenu fourni : {user_message}"
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        reply_text = await self.get_ia_response(self.model_pro, combined_prompt, ctx)
        if reply_text:
            await pl_channel.send("**Nouvelle Annonce PL :**")
            for chunk in chunkify(reply_text, 2000):
                await pl_channel.send(chunk)

async def setup(bot: commands.Bot):
    await bot.add_cog(IACog(bot))
