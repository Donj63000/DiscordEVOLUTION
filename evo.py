"""Entrée Discord native, exclusivement conversationnelle et explicitement activée."""
from __future__ import annotations

import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.command_policy import enabled_flag
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_budget import Budget
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

    async def cog_load(self):
        # Ce travail ne consulte jamais OpenAI : uniquement expiration mémoire.
        self._cleanup_task = asyncio.create_task(self._cleanup(), name="evo-memory-expiry")

    async def _cleanup(self):
        try:
            while not self._closed:
                await asyncio.sleep(60)
                if self.agent:
                    self.agent.sessions.purge()
                before = time.monotonic() - 300
                self._cooldowns = {k: v for k, v in self._cooldowns.items() if v > before}
        except asyncio.CancelledError:
            pass

    def _configuration(self):
        if self._closed or not enabled_flag("EVO_ENABLED"):
            raise EvoError("Evo est désactivé par le Staff.")
        if self.config is None:
            self.config = EvoConfig.from_env()
        return self.config

    def _context(self, guild, channel, member, private=False):
        config = self._configuration()
        # Pas de MP, de forum, de thread ou d'audience calculée approximativement.
        if not isinstance(channel, discord.TextChannel) or guild is None:
            raise EvoError("Utilise /evo dans un salon textuel autorisé du serveur.")
        ctx = ToolContext(self.bot, guild, channel, member, config, allow_private_fm=private)
        ctx.check()
        return ctx

    async def _ready(self):
        async with self._init_lock:
            if self.agent is not None:
                return self.agent
            config = self._configuration()
            if time.monotonic() < self._init_retry_at:
                raise EvoError("Le compteur de budget est indisponible. Evo reste arrêté par sécurité.")
            self.budget = Budget(config)
            try:
                await self.budget.open()
            except BaseException:
                self._init_retry_at = time.monotonic() + 60
                try:
                    await self.budget.close()
                except Exception:
                    pass
                self.budget = None
                raise
            self.agent = EvoAgent(config, MeteredModel(config, self.budget))
            return self.agent

    async def _run(self, ctx, question, trigger_id, send, *, private=False):
        """Pas de file illimitée : deux demandes simultanées, une par membre."""
        user_key = (ctx.guild.id, ctx.member.id)
        if len(question) > 1200 or not question.strip():
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
            # Toutes les attentes réseau, les tools et le comptage sont compris.
            async with asyncio.timeout(110):
                agent = await self._ready()
                normalized = clean(question, 1200).strip().casefold().rstrip(" !?.")
                if normalized in {"salut", "coucou", "hello", "bonjour", "yo", "merci", "merci evo"}:
                    answer = ("Avec plaisir 🙂" if normalized.startswith("merci") else
                              "Salut ! Pose-moi ta question sur Dofus Rétro ou la guilde 🙂 "
                              "Je m'appuie sur les données du bot ; /evo-oublier efface notre contexte.")
                elif normalized in {"confidentialité", "confidentialite", "vie privée", "vie privee"}:
                    answer = (
                        "Je n'écoute pas tout le Discord. Je reçois ta question, notre petit contexte "
                        "et seulement les résultats nécessaires des outils. Ces données sont envoyées "
                        "à OpenAI pour répondre. Le contexte expire après 15 minutes d'inactivité ; "
                        "/evo-oublier le supprime du bot. Une réponse publique reste sur Discord. "
                        "Les tickets, MP et données Staff sont exclus."
                    )
                else:
                    answer = await agent.answer(ctx, question, trigger_id, private=private)
                ctx.check()  # Permissions revérifiées après les attentes réseau.
                message = await send(output_text(answer, ctx.sources))
                if message is not None:
                    key = (ctx.guild.id, ctx.channel.id, ctx.member.id, private)
                    memory = agent.sessions.items.get(key)
                    if memory is not None:
                        memory.last_message_id = message.id
        except EvoError as exc:
            await send(output_text(str(exc), set()))
        except TimeoutError:
            await send("La réponse a pris trop de temps. J'ai arrêté les appels, sans relance automatique.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Jamais la question, les données de membre, la clé, le DSN ou la réponse brute.
            log.warning("evo request failed type=%s", type(exc).__name__)
            await send("Je n'ai pas pu terminer cette recherche. Aucun appel supplémentaire n'est lancé.")
        finally:
            if self._active.get(user_key) is task:
                self._active.pop(user_key, None)

    @app_commands.command(name="evo", description="Discute avec Evo : Dofus Rétro, drops, équipements et guilde.")
    @app_commands.describe(question="Ta question, en langage naturel.", prive="Réponse visible uniquement par toi, nécessaire pour ta session /exo.")
    @app_commands.guild_only()
    async def evo(self, interaction: discord.Interaction, question: str, prive: bool = False):
        try:
            ctx = self._context(interaction.guild, interaction.channel, interaction.user, prive)
        except EvoError as exc:
            await interaction.response.send_message(output_text(str(exc), set()), ephemeral=True, allowed_mentions=NO_MENTIONS)
            return
        await interaction.response.defer(ephemeral=prive, thinking=True)

        async def send(content):
            return await interaction.edit_original_response(content=content, allowed_mentions=NO_MENTIONS)

        await self._run(ctx, question, interaction.id, send, private=prive)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Une réponse explicite au dernier message d'Evo, jamais une écoute du salon.
        if (self._closed or not enabled_flag("EVO_ENABLED") or self.agent is None
                or message.guild is None or message.author.bot or message.webhook_id
                or message.reference is None or not message.content):
            return
        key = (message.guild.id, message.channel.id, message.author.id, False)
        memory = self.agent.sessions.get(key)
        if memory.last_message_id is None or message.reference.message_id != memory.last_message_id:
            return
        try:
            ctx = self._context(message.guild, message.channel, message.author, False)
        except EvoError:
            return

        async def send(content):
            return await message.reply(content, mention_author=False, allowed_mentions=NO_MENTIONS,
                                       suppress_embeds=True)

        async with message.channel.typing():
            await self._run(ctx, message.content, message.id, send)

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
        await interaction.edit_original_response(
            content="C'est oublié côté bot. Les messages déjà publiés sur Discord ne sont pas supprimés. "
            "Une requête déjà envoyée au fournisseur n'est pas annulable et sa réservation reste comptée.",
            allowed_mentions=NO_MENTIONS,
        )

    @app_commands.command(name="evo-budget", description="Staff : consulte le compteur et l'état d'Evo, sans génération IA.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def budget_status(self, interaction: discord.Interaction):
        if not getattr(getattr(interaction.user, "guild_permissions", None), "manage_guild", False):
            await interaction.response.send_message("Réservé au Staff autorisé à gérer le serveur.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            config = self._configuration()
            if interaction.guild_id != config.guild_id:
                raise EvoError("Ce n'est pas le serveur configuré pour Evo.")
            await self._ready()
            data = await self.budget.status()
            wiki = self.bot.get_cog("DofusWikiCog")
            xixou = bool(wiki and wiki.enrichment_client and wiki.enrichment_client.enabled)
            content = (
                f"Evo · {config.model}\n"
                f"Mois UTC {data['month']} : {data['used_nano']/NANO:.4f} / "
                f"{config.monthly_nano/NANO:.2f} USD réservés/comptés\n"
                f"Jour UTC : {data['day_nano']/NANO:.4f} / {config.daily_nano/NANO:.2f} USD\n"
                f"Appels réservés : {data['calls']} · Réservations non confirmées : "
                f"{data['pending_nano']/NANO:.4f} USD\n"
                f"Tokens confirmés : {data['input_tokens']} entrée / {data['output_tokens']} sortie\n"
                f"Blocage anomalie : {'oui' if data['blocked'] else 'non'} · "
                f"Enrichissement Xixou : {'activé' if xixou else 'absent'}\n"
                "Compteur prudent (marge de 15 %), pas facture réelle ni montant en euros. "
                "Il ne couvre que /evo. Aucun compteur ne peut être remis à zéro via Discord."
            )
        except EvoError as exc:
            content = str(exc)
        except Exception as exc:
            log.warning("evo status failed type=%s", type(exc).__name__)
            content = "État indisponible. Les appels payants restent soumis au compteur durable."
        await interaction.edit_original_response(content=output_text(content, set()), allowed_mentions=NO_MENTIONS)

    async def cog_unload(self):
        self._closed = True
        tasks = list(set(self._active.values()))
        if self._cleanup_task:
            tasks.append(self._cleanup_task)
        for task in tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(t for t in tasks if t is not asyncio.current_task()), return_exceptions=True)
        if self.agent:
            self.agent.sessions.clear()
            await self.agent.model.close()
        if self.budget:
            await self.budget.close()
        self._active.clear()
        self._cooldowns.clear()


async def setup(bot):
    if enabled_flag("EVO_ENABLED"):
        await bot.add_cog(EvoCog(bot))
