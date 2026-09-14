"""Entrée Discord native, exclusivement conversationnelle et explicitement activée."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.command_policy import enabled_flag
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_budget import Budget
from utils.evo_budget_store import ConsoleBudgetStore
from utils.evo_config import EvoConfig, EvoError, NANO
from utils.evo_safety import ToolContext, clean, output_text

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()


class EvoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = None
        self.agent = None
        self.budget = None
        self._init_lock = asyncio.Lock()
        self._active = {}
        self._cooldowns = {}
        self._cleanup_task = None
        self._closed = False
        self._init_retry_at = 0.0
        self._state_version = 0
        self._seen_triggers = OrderedDict()
        self._last_messages = OrderedDict()

    async def cog_load(self):
        self._cleanup_task = asyncio.create_task(self._cleanup(), name="evo-memory-expiry")

    async def _cleanup(self):
        try:
            while not self._closed:
                await asyncio.sleep(60)
                if self.agent:
                    self.agent.sessions.purge()
                before = time.monotonic() - 300
                self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > before}
                self._purge_references()
        except asyncio.CancelledError:
            pass

    def _configuration(self):
        if self._closed or not enabled_flag("EVO_ENABLED"):
            raise EvoError("Evo est désactivé par le Staff.")
        if self.config is None:
            self.config = EvoConfig.from_env(guilds=self.bot.guilds)
        return self.config

    def _context(self, guild, channel, member):
        config = self._configuration()
        if not isinstance(channel, discord.TextChannel) or guild is None:
            raise EvoError("Utilise Evo dans un salon textuel public du serveur.")
        ctx = ToolContext(self.bot, guild, channel, member, config)
        ctx.check()
        return ctx

    async def _ready(self):
        await self.bot.ensure_evo_leadership()
        version = self._state_version
        async with self._init_lock:
            if version != self._state_version:
                raise EvoError("Evo se réinitialise. Réessaie dans quelques instants.")
            if self.agent is not None:
                await self.budget.check_ready()
                if version != self._state_version:
                    raise EvoError("Evo se réinitialise. Réessaie dans quelques instants.")
                return self.agent
            config = self._configuration()
            if time.monotonic() < self._init_retry_at:
                raise EvoError("Le compteur de budget est indisponible. Evo reste arrêté par sécurité.")
            budget = self.budget
            if budget is None:
                budget = Budget(config, ConsoleBudgetStore(self.bot, config.guild_id))
                self.budget = budget
            try:
                await budget.open()
                if version != self._state_version:
                    raise EvoError("Evo se réinitialise. Réessaie dans quelques instants.")
            except BaseException:
                if version == self._state_version:
                    self._init_retry_at = time.monotonic() + 60
                budget.invalidate()
                raise
            self.agent = EvoAgent(config, MeteredModel(config, budget))
            log.debug("evo ready guild_id=%s", config.guild_id)
            return self.agent

    def _purge_references(self):
        now = time.monotonic()
        lifetime = self.config.session_seconds if self.config else 900
        for key, (_, updated) in list(self._last_messages.items()):
            if now - updated >= lifetime:
                self._last_messages.pop(key, None)

    def _remember_reply(self, ctx, message):
        if message is None:
            return
        key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
        self._last_messages[key] = (message.id, time.monotonic())
        self._last_messages.move_to_end(key)
        while len(self._last_messages) > ctx.config.max_sessions:
            self._last_messages.popitem(last=False)
        if self.agent:
            memory = self.agent.sessions.items.get(key)
            if memory is not None:
                memory.last_message_id = message.id

    def _local_answer(self, question, prompt_if_empty):
        normalized = clean(question, 1200).strip().casefold().rstrip(" !?.")
        if not normalized and prompt_if_empty:
            return "Je suis là 🙂 Pose-moi ta question sur Dofus Rétro ou la guilde."
        if normalized in {"salut", "coucou", "hello", "bonjour", "yo", "merci", "merci evo"}:
            if normalized.startswith("merci"):
                return "Avec plaisir 🙂"
            return (
                "Salut ! Pose-moi ta question sur Dofus Rétro ou la guilde 🙂 "
                "Je m'appuie sur les données du bot ; /evo-oublier efface notre contexte."
            )
        if normalized in {"confidentialité", "confidentialite", "vie privée", "vie privee"}:
            return (
                "Je réponds publiquement quand tu me mentionnes, utilises /evo ou réponds "
                "à mon dernier message pour toi. Je reçois ta question, notre petit contexte "
                "et les résultats utiles des outils ; ces données sont envoyées à OpenAI. "
                "Le contexte expire après 15 minutes d'inactivité ; /evo-oublier le supprime "
                "du bot. Les messages publiés restent sur Discord. Les tickets, MP, salons "
                "privés et données Staff sont exclus."
            )
        return None

    async def _run(self, ctx, question, trigger_id, send, *, prompt_if_empty=False):
        """Pas de file illimitée : deux demandes simultanées, une par membre."""
        if trigger_id in self._seen_triggers:
            log.debug("evo duplicate trigger_id=%s", trigger_id)
            return
        self._seen_triggers[trigger_id] = None
        while len(self._seen_triggers) > 2048:
            self._seen_triggers.popitem(last=False)
        user_key = (ctx.guild.id, ctx.member.id)
        if len(question) > 1200 or (not question.strip() and not prompt_if_empty):
            await send("Écris une question de 1 à 1 200 caractères.")
            return
        if user_key in self._active:
            await send("Je termine déjà une réponse pour toi. Une demande à la fois 🙂")
            return
        if len(self._active) >= 2:
            await send("Je réponds déjà à deux camarades. Réessaie après leurs réponses 🙂")
            return
        now = time.monotonic()
        if now - self._cooldowns.get(user_key, -1000) < ctx.config.cooldown:
            await send("Laisse quelques secondes entre deux questions pour partager le budget.")
            return
        task = asyncio.current_task()
        self._active[user_key] = task
        self._cooldowns[user_key] = now
        try:
            async with asyncio.timeout(110):
                answer = self._local_answer(question, prompt_if_empty)
                if answer is not None:
                    log.debug("evo local reply trigger_id=%s", trigger_id)
                else:
                    agent = await self._ready()
                    answer = await agent.answer(ctx, question, trigger_id)
                await self.bot.ensure_evo_leadership()
                ctx.check()
                message = await send(output_text(answer, ctx.sources))
                self._remember_reply(ctx, message)
        except EvoError as exc:
            await send(output_text(str(exc), set()))
        except TimeoutError:
            await send("La réponse a pris trop de temps. J'ai arrêté les appels, sans relance automatique.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("evo request failed type=%s", type(exc).__name__)
            await send("Je n'ai pas pu terminer cette recherche. Aucun appel supplémentaire n'est lancé.")
        finally:
            if self._active.get(user_key) is task:
                self._active.pop(user_key, None)

    @app_commands.command(name="evo", description="Discute avec Evo : Dofus Rétro, drops, équipements et guilde.")
    @app_commands.describe(question="Ta question publique, en langage naturel.")
    @app_commands.guild_only()
    async def evo(self, interaction: discord.Interaction, question: str):
        try:
            ctx = self._context(interaction.guild, interaction.channel, interaction.user)
        except EvoError as exc:
            await interaction.response.send_message(
                output_text(str(exc), set()), allowed_mentions=NO_MENTIONS,
            )
            return
        await interaction.response.defer(thinking=True)

        async def send(content):
            return await interaction.edit_original_response(content=content, allowed_mentions=NO_MENTIONS)

        await self._run(ctx, question, interaction.id, send)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (self._closed or not enabled_flag("EVO_ENABLED")
                or message.guild is None or message.author.bot or message.webhook_id
                or not message.content or self.bot.user is None):
            return
        mention = re.compile(rf"<@!?{self.bot.user.id}>")
        mentioned = mention.search(message.content) is not None
        self._purge_references()
        key = (message.guild.id, message.channel.id, message.author.id)
        previous = self._last_messages.get(key)
        replied = (
            previous is not None and message.reference is not None
            and message.reference.message_id == previous[0]
        )
        if not mentioned and not replied:
            return
        try:
            ctx = self._context(message.guild, message.channel, message.author)
        except EvoError:
            return
        log.debug("evo message trigger_id=%s mentioned=%s replied=%s",
                  message.id, mentioned, replied)

        async def send(content):
            return await message.reply(content, mention_author=False, allowed_mentions=NO_MENTIONS,
                                       suppress_embeds=True)

        async with message.channel.typing():
            question = mention.sub("", message.content).strip() if mentioned else message.content
            await self._run(ctx, question, message.id, send, prompt_if_empty=mentioned)

    @app_commands.command(name="evo-oublier", description="Efface ton contexte Evo du bot, sans appel IA.")
    @app_commands.guild_only()
    async def forget(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        task = self._active.get((interaction.guild.id, interaction.user.id))
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.agent:
            self.agent.sessions.forget(interaction.guild.id, interaction.user.id)
        for key in list(self._last_messages):
            if key[0] == interaction.guild.id and key[2] == interaction.user.id:
                self._last_messages.pop(key, None)
        await interaction.edit_original_response(
            content="C'est oublié côté bot. Les messages déjà publiés sur Discord ne sont pas supprimés. "
            "Une requête déjà envoyée au fournisseur n'est pas annulable et sa réservation reste comptée.",
            allowed_mentions=NO_MENTIONS,
        )

    @app_commands.command(name="evo-budget", description="Staff : consulte le compteur et l'état d'Evo, sans génération IA.")
    @app_commands.describe(initialiser="Crée le premier compteur dans #console, sans réinitialiser un compteur existant.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def budget_status(self, interaction: discord.Interaction, initialiser: bool = False):
        if not getattr(getattr(interaction.user, "guild_permissions", None), "manage_guild", False):
            await interaction.response.send_message("Réservé au Staff autorisé à gérer le serveur.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            config = self._configuration()
            if interaction.guild_id != config.guild_id:
                raise EvoError("Ce n'est pas le serveur configuré pour Evo.")
            if initialiser:
                await self.bot.ensure_evo_leadership()
                async with self._init_lock:
                    if self.budget is None:
                        self.budget = Budget(
                            config, ConsoleBudgetStore(self.bot, config.guild_id),
                        )
                    await self.budget.initialize()
                    self._init_retry_at = 0.0
                await interaction.edit_original_response(
                    content="Le compteur Evo est initialisé dans #console. Aucun appel IA effectué.",
                    allowed_mentions=NO_MENTIONS,
                )
                log.debug("evo budget initialized guild_id=%s", config.guild_id)
                return
            await self._ready()
            data = await self.budget.status()
            wiki = self.bot.get_cog("DofusWikiCog")
            xixou = bool(wiki and wiki.enrichment_client and wiki.enrichment_client.enabled)
            content = (
                f"Evo · {config.model}\n"
                f"Mois UTC {data['month']} : {data['used_nano']/NANO:.4f} / "
                f"{config.monthly_nano/NANO:.2f} USD réservés/comptés\n"
                f"Jour UTC : {data['day_nano']/NANO:.4f} / {config.daily_nano/NANO:.2f} USD\n"
                f"Appels réservés : {data['calls']} · Réservations non réglées : "
                f"{data['pending_nano']/NANO:.4f} USD\n"
                f"Tokens confirmés : {data['input_tokens']} entrée / {data['output_tokens']} sortie\n"
                f"Blocage anomalie : {'oui' if data['blocked'] else 'non'} · "
                f"Enrichissement Xixou : {'activé' if xixou else 'absent'}\n"
                "Compteur prudent (marge de 15 %), pas facture réelle ni montant en euros. "
                "Il couvre /evo et les échanges avec Evo. "
                "Aucun compteur ne peut être remis à zéro via Discord."
            )
        except EvoError as exc:
            content = str(exc)
        except Exception as exc:
            log.warning("evo status failed type=%s", type(exc).__name__)
            content = "État indisponible. Les appels payants restent soumis au compteur durable."
        await interaction.edit_original_response(content=output_text(content, set()), allowed_mentions=NO_MENTIONS)

    async def suspend(self):
        """J'arrête les demandes avant de relire le registre avec le prochain leader."""
        self._state_version += 1
        self._init_retry_at = 0.0
        agent, self.agent = self.agent, None
        budget = self.budget
        if budget:
            budget.invalidate()
        self._last_messages.clear()
        current = asyncio.current_task()
        tasks = list({task for task in self._active.values() if task is not current})
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if agent:
            agent.sessions.clear()
            try:
                await agent.model.close()
            except Exception as exc:
                log.debug("evo transport close failed type=%s", type(exc).__name__)
        log.debug("evo suspended active_cancelled=%s", len(tasks))

    async def cog_unload(self):
        self._closed = True
        if self._cleanup_task:
            self._cleanup_task.cancel()
            await asyncio.gather(self._cleanup_task, return_exceptions=True)
        await self.suspend()
        if self.budget:
            await self.budget.close()
            self.budget = None
        self._cooldowns.clear()
        self._seen_triggers.clear()


async def setup(bot):
    if enabled_flag("EVO_ENABLED"):
        await bot.add_cog(EvoCog(bot))
