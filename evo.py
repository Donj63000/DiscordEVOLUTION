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
from utils.evo_exo import share_session, revoke_session, clear_member_shares, clear_shares

log = logging.getLogger(__name__)
NO_MENTIONS = discord.AllowedMentions.none()
MAX_ACTIVE_REQUESTS = 2
MAX_WAITING_REQUESTS = 2
REQUEST_WAIT_SECONDS = 120


class EvoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = None
        self.agent = None
        self.budget = None
        self._init_lock = asyncio.Lock()
        self._active = {}
        self._active_questions = {}
        self._waiting = {}
        self._slot_changed = asyncio.Event()
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
        if not isinstance(channel, (
            discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel,
        )) or guild is None:
            raise EvoError("Utilise Evo dans un salon de discussion du serveur.")
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
        return None

    async def _admit_request(self, ctx, question, send, deepen):
        """Je garde une seule question en attente par membre, pendant deux minutes au plus."""
        user_key = (ctx.guild.id, ctx.member.id)
        task = asyncio.current_task()
        signature = (ctx.channel.id, " ".join(question.casefold().split()), deepen)
        waiting = self._waiting.get(user_key)
        if self._active_questions.get(user_key) == signature:
            await send("Je traite déjà cette même question dans ce salon 🙂")
            return False
        if waiting:
            content = ("Cette question est déjà en attente ; je te répondrai ici."
                       if waiting[1] == signature else
                       "Tu as déjà une question en attente. Attends sa réponse avant d'en ajouter une autre 🙂")
            log.debug("evo queue refused member_id=%s reason=already_waiting", ctx.member.id)
            await send(content)
            return False
        if user_key not in self._active:
            if len(self._active) >= MAX_ACTIVE_REQUESTS:
                await send("Je réponds déjà à deux camarades. Réessaie après leurs réponses 🙂")
                return False
            if time.monotonic() - self._cooldowns.get(user_key, -1000) < ctx.config.cooldown:
                await send("Laisse quelques secondes entre deux questions pour partager le budget.")
                return False
        else:
            if len(self._waiting) >= MAX_WAITING_REQUESTS:
                log.debug("evo queue refused member_id=%s reason=full", ctx.member.id)
                await send("La courte file d'attente est pleine. Réessaie après ma réponse 🙂")
                return False
            version = self._state_version
            self._waiting[user_key] = (task, signature)
            log.debug("evo request queued member_id=%s channel_id=%s", ctx.member.id, ctx.channel.id)
            try:
                async with asyncio.timeout(REQUEST_WAIT_SECONDS):
                    await send(
                        "Ta question est en attente. Je la traiterai ici après ma réponse en cours "
                        "et le délai entre questions. L'attente est limitée à deux minutes. "
                        "/evo-oublier annule aussi cette attente."
                    )
                    while True:
                        self._slot_changed.clear()
                        if version != self._state_version:
                            raise EvoError("Evo s'est réinitialisé : ta question en attente est annulée.")
                        remaining_cooldown = (
                            ctx.config.cooldown + self._cooldowns.get(user_key, -1000) - time.monotonic()
                        )
                        available = user_key not in self._active and len(self._active) < MAX_ACTIVE_REQUESTS
                        if available and remaining_cooldown <= 0:
                            break
                        if available:
                            try:
                                async with asyncio.timeout(remaining_cooldown):
                                    await self._slot_changed.wait()
                            except TimeoutError:
                                pass
                        else:
                            await self._slot_changed.wait()
            except TimeoutError:
                log.debug("evo queue expired member_id=%s", ctx.member.id)
                await send("L'attente a dépassé deux minutes. Ta question n'a pas été lancée ; tu peux la renvoyer.")
                return False
            except asyncio.CancelledError:
                log.debug("evo queue cancelled member_id=%s", ctx.member.id)
                try:
                    async with asyncio.timeout(5):
                        await send("Ta question en attente a été annulée.")
                except Exception as exc:
                    log.debug("evo queue cancellation notice failed type=%s", type(exc).__name__)
                raise
            finally:
                if self._waiting.get(user_key, (None,))[0] is task:
                    self._waiting.pop(user_key, None)
            self._configuration()
            log.debug("evo queued request starting member_id=%s channel_id=%s", ctx.member.id, ctx.channel.id)
        self._active[user_key] = task
        self._active_questions[user_key] = signature
        self._cooldowns[user_key] = time.monotonic()
        return True

    async def _run(self, ctx, question, trigger_id, send, *, prompt_if_empty=False, deepen=False):
        """Je conserve les demandes et leurs salons sans dépasser les limites communes."""
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
        task = asyncio.current_task()
        try:
            if not await self._admit_request(ctx, question, send, deepen):
                return
            async with asyncio.timeout(110):
                await ctx.ensure_access()
                answer = self._local_answer(question, prompt_if_empty)
                if answer is not None:
                    log.debug("evo local reply trigger_id=%s", trigger_id)
                else:
                    agent = await self._ready()
                    options = {"deepen": True} if deepen else {}
                    answer = await agent.answer(ctx, question, trigger_id, **options)
                await self.bot.ensure_evo_leadership()
                await ctx.ensure_access()
                if ctx.before_publish:
                    ctx.before_publish()
                message = await send(output_text(answer, ctx.sources))
                self._remember_reply(ctx, message)
        except EvoError as exc:
            if ctx.action_receipt and ctx.action_receipt.get("action_effectuee"):
                await send("Ton action a été enregistrée. Son détail n'est plus partagé ici ; ne relance pas l'action.")
            else:
                await send(output_text(str(exc), set()))
        except TimeoutError:
            if ctx.action_receipt and ctx.action_receipt.get("action_effectuee"):
                await send("Ton action a été enregistrée. La rédaction a expiré ; ne relance pas l'action.")
            else:
                await send("La réponse a pris trop de temps. J'ai arrêté les appels, sans relance automatique.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("evo request failed type=%s", type(exc).__name__)
            if ctx.action_receipt and ctx.action_receipt.get("action_effectuee"):
                await send("Ton action a été enregistrée, mais sa réponse n'a pas pu être publiée complètement.")
            else:
                await send("Je n'ai pas pu terminer cette recherche. Aucun appel supplémentaire n'est lancé.")
        finally:
            if self._active.get(user_key) is task:
                self._active.pop(user_key, None)
                self._active_questions.pop(user_key, None)
                self._slot_changed.set()

    @app_commands.command(name="evo", description="Discute avec Evo : Dofus Rétro, drops, équipements et guilde.")
    @app_commands.describe(question="Ta question dans ce salon, en langage naturel.",
                           approfondir="Autorise un spécialiste supplémentaire dans le même budget.")
    @app_commands.guild_only()
    async def evo(self, interaction: discord.Interaction, question: str, approfondir: bool = False):
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

        await self._run(ctx, question, interaction.id, send, deepen=approfondir)

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

    @app_commands.command(name="evo-exo", description="Partage ou révoque l'état courant de ton atelier dans ce salon.")
    @app_commands.describe(partager="Autorise Evo à utiliser l'état courant de ton atelier dans ce salon.")
    @app_commands.guild_only()
    async def exo_share(self, interaction: discord.Interaction, partager: bool):
        await interaction.response.defer(ephemeral=True)
        try:
            ctx = self._context(interaction.guild, interaction.channel, interaction.user)
            await self.bot.ensure_evo_leadership()
            await ctx.ensure_access()
            if partager:
                await share_session(ctx)
                content = (
                    "L'état courant de ton atelier est partagé avec Evo dans ce salon pour 15 minutes "
                    "au maximum. Une modification privée demande un nouveau partage. "
                    "/evo-exo partager:false révoque cet accès."
                )
            else:
                await revoke_session(ctx)
                content = "Le partage de ton atelier avec Evo est révoqué dans ce salon."
            if self.agent:
                self.agent.sessions.forget(ctx.guild.id, ctx.member.id)
            log.debug("evo exo sharing guild_id=%s member_id=%s enabled=%s",
                      ctx.guild.id, ctx.member.id, partager)
        except EvoError as exc:
            content = str(exc)
        await interaction.edit_original_response(content=output_text(content, set()), allowed_mentions=NO_MENTIONS)

    @app_commands.command(name="evo-oublier", description="Efface ton contexte Evo du bot, sans appel IA.")
    @app_commands.guild_only()
    async def forget(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        user_key = (interaction.guild.id, interaction.user.id)
        waiting = self._waiting.get(user_key, (None,))[0]
        tasks = {waiting, self._active.get(user_key)} - {None, asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.agent:
            self.agent.sessions.forget(interaction.guild.id, interaction.user.id)
        clear_member_shares(self.bot, interaction.guild.id, interaction.user.id)
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
                f"Générations : {config.max_calls} normales / {config.deep_max_calls} approfondies ; "
                f"réponse limitée à {config.max_output} tokens\n"
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
        clear_shares(self.bot)
        current = asyncio.current_task()
        tasks = list((set(self._active.values()) | {item[0] for item in self._waiting.values()}) - {current})
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
