"""Commandes slash natives : enquête de modération confidentielle et contrôlée."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Literal
import uuid

import discord
from discord import app_commands
from discord.ext import commands

from utils.enquete_core import (
    Alias, Config, EnqueteError, MARKER, Receipts, ReportMeta, Window, Workspace,
    aliases_from_input, build_report, normalise,
)
from utils.enquete_service import AccessScope, Collector, identity_snapshot, names_of, resolve_target, staff

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent
NO_MENTIONS = discord.AllowedMentions.none()


@dataclass
class Job:
    id: str
    guild_id: int
    requester_id: int
    state: str = "préparation"
    scanned: int = 0
    task: asyncio.Task | None = None
    status_messages: list = field(default_factory=list)


class EnqueteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.directory = BASE_DIR / "data" / "enquete"
        self.temporary = self.directory / "tmp"
        self.receipts: Receipts | None = None
        self.jobs: dict[str, Job] = {}
        self.last_started: dict[int, float] = {}
        self.busy = False
        self.janitor: asyncio.Task | None = None
        self.started_at = time.time()

    async def cog_load(self):
        for path in (self.directory, self.temporary):
            if path.is_symlink():
                raise RuntimeError("Le stockage enquête ne doit pas être un lien symbolique.")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
        self.receipts = Receipts(self.directory / "publications.sqlite3")
        self.janitor = asyncio.create_task(self._housekeeping(), name="enquete-retention")

    async def cog_unload(self):
        tasks = [j.task for j in self.jobs.values() if j.task]
        if self.janitor:
            tasks.append(self.janitor)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Une Task annulée avant son premier tour n'exécute pas le finally de _run.
        for job in list(self.jobs.values()):
            failures = await self._purge(job.id, job.guild_id)
            self._finish(job, f"interrompu ; {failures} publication(s) à purger")
        if self.receipts:
            self.receipts.close()

    async def _private(self, interaction, text):
        if interaction.is_expired():
            return  # Jamais de repli vers un MP ou une réponse publique.
        if interaction.response.is_done():
            await interaction.edit_original_response(content=text, allowed_mentions=NO_MENTIONS)
        else:
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_MENTIONS)

    async def _authorise(self, interaction) -> Config | None:
        try:
            cfg = Config.from_env()
            if interaction.guild is None or not isinstance(interaction.user, discord.Member):
                raise EnqueteError("Cette commande s'utilise uniquement dans le serveur.")
            if not staff(interaction.user, cfg):
                raise EnqueteError("Cette commande est réservée au staff autorisé.")
            return cfg
        except EnqueteError as exc:
            await self._private(interaction, str(exc))
            return None

    async def cog_app_command_error(self, interaction, error):
        original = getattr(error, "original", error)
        log.warning("Enquête : erreur de commande, type=%s", type(original).__name__)
        await self._private(interaction, str(original) if isinstance(original, EnqueteError) else
                            "La commande a échoué. Aucun détail de conversation n'est publié dans les logs.")

    async def _send(self, job: Job, channel, text: str, *, file=None):
        message = await channel.send(
            text, file=file, allowed_mentions=NO_MENTIONS, silent=True, suppress_embeds=True,
        )
        try:
            self.receipts.sent(job.id, channel.id, message.id)
        except Exception:
            # Ne pas laisser un rapport non traçable si l'enregistrement local échoue.
            with suppress(Exception):
                await message.delete()
            raise
        return message

    async def _progress(self, job: Job, stage: str, scanned: int):
        job.state, job.scanned = stage, scanned
        # Pas d'extraits, noms ou liens vers les sources dans le message de progression.
        for message in job.status_messages:
            with suppress(discord.HTTPException):
                await message.edit(
                    content=f"{MARKER}{job.id}] {stage} — {scanned} messages parcourus.\n"
                            f"Demandeur ID {job.requester_id}. `/enquete-statut` / `/enquete-annuler`.",
                    allowed_mentions=NO_MENTIONS,
                )

    async def _purge(self, job_id: str, guild_id: int) -> int:
        failures = 0
        for receipt in self.receipts.deliveries(job_id):
            try:
                channel = self.bot.get_channel(receipt["cid"])
                if channel is None:
                    channel = await self.bot.fetch_channel(receipt["cid"])
                if getattr(getattr(channel, "guild", None), "id", None) != guild_id:
                    failures += 1
                    continue
                message = await channel.fetch_message(receipt["mid"])
                if message.author.id != self.bot.user.id or not message.content.startswith(f"{MARKER}{job_id}]"):
                    # Un journal corrompu ne doit pas autoriser la suppression d'un autre message.
                    failures += 1
                    continue
                await message.delete()
                self.receipts.forget_delivery(receipt["mid"])
            except discord.NotFound:
                self.receipts.forget_delivery(receipt["mid"])
            except Exception:
                failures += 1
        return failures

    def _finish(self, job: Job, result: str):
        try:
            self.receipts.update(job.id, result)
        except Exception as exc:
            log.error("Journal enquête indisponible dossier=%s type=%s", job.id, type(exc).__name__)
        finally:
            self.jobs.pop(job.id, None)
            self.busy = False

    async def _cancel(self, job: Job):
        job.task.cancel()
        with suppress(asyncio.CancelledError):
            await job.task
        if job.id in self.jobs:  # annulée avant l'entrée dans _run
            failures = await self._purge(job.id, job.guild_id)
            self._finish(job, f"annulé ; {failures} publication(s) à purger")

    async def _run(self, job: Job, scope: AccessScope, meta: ReportMeta, cfg: Config):
        result = "échec"
        try:
            # Budget supplémentaire borné pour analyse, export et contrôles avant envoi.
            async with asyncio.timeout(cfg.max_seconds + 1800):
                with tempfile.TemporaryDirectory(prefix=f"enquete-{job.id}-", dir=self.temporary) as directory:
                    work = Workspace(Path(directory) / "index.sqlite3")
                    try:
                        collector = Collector(
                            self.bot, scope, work, meta, cfg,
                            reaction_types=(("normal", discord.ReactionType.normal), ("burst", discord.ReactionType.burst)),
                            progress=lambda stage, count: self._progress(job, stage, count),
                        )
                        await collector.collect()
                        await self._progress(job, "mise en page et vérification des droits", collector.visited_messages)
                        files = await build_report(work, meta, cfg, file_limit=scope.guild.filesize_limit)
                        # Le SDK et les droits sont revalidés sans dépendre du jeton d'interaction.
                        await scope.revalidate(collector.sources)
                        for cid in scope.destination_ids:
                            for index, path in enumerate(files, 1):
                                # Une archive peut prendre du temps à publier. Pas de confiance
                                # accordée à un contrôle de droits datant du début du scan.
                                await scope.revalidate(collector.sources)
                                channel = next(c for c in scope.destinations if c.id == cid)
                                with discord.File(path, filename=path.name) as file:
                                    await self._send(
                                        job, channel,
                                        f"{MARKER}{job.id}] Rapport de modération — fichier {index}/{len(files)}.\n"
                                        f"Demandeur ID {job.requester_id}. Lire la synthèse, ses limites et les pistes non confirmées.",
                                        file=file,
                                    )
                                await asyncio.sleep(0)
                        result = "publié — lire la couverture et les limites dans la synthèse"
                        await self._progress(job, result, collector.visited_messages)
                    finally:
                        work.close()
        except asyncio.CancelledError:
            result = "annulé / interrompu"
            failures = await self._purge(job.id, job.guild_id)
            if failures:
                result += f" ; {failures} publication(s) restent à purger"
            raise
        except Exception as exc:
            # Ni le texte des messages ni les messages d'erreur HTTP ne sont journalisés.
            log.error("Enquête interrompue dossier=%s type=%s", job.id, type(exc).__name__)
            failures = await self._purge(job.id, job.guild_id)
            result = "échec ; publications retirées" if not failures else f"échec ; {failures} publication(s) restent à purger"
        finally:
            self._finish(job, result)

    @app_commands.command(name="enquete", description="Staff : produire un dossier factuel de modération sur un membre.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        pseudo="Pseudo exact, mention ou ID Discord ; l'ID est recommandé en cas d'homonyme.",
        aliases="Variantes connues séparées par des virgules : illun, ancien-pseudo.",
        depuis="Début inclus : JJ/MM/AAAA ou AAAA-MM-JJ. Vide : historique disponible configuré.",
        jusqua="Dernier jour inclus, heure de Paris. Vide : début de la collecte.",
        destination="ici : salon courant (par défaut) ; staff, console ou les-deux : salons configurés.",
        contexte="Nombre de messages voisins avant/après chaque résultat (0 à 10).",
        approximatif="Inclure les abréviations/typos possibles, clairement signalées comme des pistes.",
        reactions="Rechercher aussi ses réactions encore présentes : beaucoup plus lent.",
        motif="Motif de modération, inscrit dans la synthèse confidentielle.",
    )
    async def enquete(
        self, interaction: discord.Interaction,
        pseudo: app_commands.Range[str, 1, 100],
        aliases: app_commands.Range[str, 0, 2000] = "",
        depuis: app_commands.Range[str, 0, 10] = "",
        jusqua: app_commands.Range[str, 0, 10] = "",
        destination: Literal["ici", "staff", "console", "les-deux"] = "ici",
        contexte: app_commands.Range[int, 0, 10] = 3,
        approximatif: bool = True,
        reactions: bool = False,
        motif: app_commands.Range[str, 0, 300] = "Consultation de modération",
    ):
        cfg = await self._authorise(interaction)
        if cfg is None:
            return
        if not self.bot.intents.members or not self.bot.intents.message_content:
            return await self._private(
                interaction, "Active SERVER MEMBERS INTENT et MESSAGE CONTENT INTENT dans le Developer Portal "
                "ainsi que ENABLE_MEMBERS_INTENT=1 et ENABLE_MESSAGE_CONTENT_INTENT=1, puis redémarre.",
            )
        if self.busy:
            return await self._private(interaction, "Une enquête est déjà en préparation ou en cours sur cette instance.")
        wait = cfg.cooldown - (time.monotonic() - self.last_started.get(interaction.guild_id, float("-inf")))
        if wait > 0:
            return await self._private(interaction, f"Attends encore {int(wait) + 1} seconde(s) avant une nouvelle enquête.")
        self.busy = True  # réservé avant le premier appel réseau
        started = False
        job = None
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            parsed_aliases = aliases_from_input(aliases)
            window = Window.parse(depuis, jusqua, default_days=cfg.default_days)
            if not 0 <= contexte <= 10:
                raise EnqueteError("Le contexte doit être compris entre 0 et 10.")
            scope = AccessScope(
                interaction.guild, interaction.guild.me, interaction.user.id, cfg, destination,
                current_channel_id=interaction.channel_id,
            )
            async with asyncio.timeout(180):
                await scope.refresh()
            target_id, member = resolve_target(pseudo, scope.members)
            scope.bind_target(target_id)
            all_aliases = [Alias(n, "nom actuel renvoyé par Discord") for n in names_of(member)] if member else []
            all_aliases.extend(parsed_aliases)
            job = Job(uuid.uuid4().hex[:12], interaction.guild_id, interaction.user.id)
            self.receipts.create(job.id, job.guild_id, job.requester_id, time.time(), cfg.retention_days)
            meta = ReportMeta(
                job_id=job.id, guild_id=job.guild_id, guild_name=interaction.guild.name, target_id=target_id,
                requester_id=job.requester_id, reason=motif.strip() or "Consultation de modération",
                window=window, identity=identity_snapshot(member, target_id), aliases=all_aliases,
                approximate=approximatif, context=contexte, deep_reactions=reactions,
            )
            for channel in scope.destinations:
                message = await self._send(
                    job, channel, f"{MARKER}{job.id}] Préparation d'un rapport confidentiel de modération.\n"
                    f"Demandeur ID {job.requester_id}. Suivi : `/enquete-statut identifiant:{job.id}`.",
                )
                job.status_messages.append(message)
            self.jobs[job.id] = job
            self.last_started[job.guild_id] = time.monotonic()
            job.task = asyncio.create_task(self._run(job, scope, meta, cfg), name=f"enquete-{job.id}")
            started = True
            destination_mentions = ", ".join(f"<#{cid}>" for cid in scope.destination_ids)
            await self._private(
                interaction, f"Dossier `{job.id}` lancé. Le résultat sera envoyé dans {destination_mentions}.\n"
                f"`/enquete-statut identifiant:{job.id}` — `/enquete-annuler identifiant:{job.id}`.\n"
                "Aucun MP ni ping n'est envoyé à la personne. Les limites de collecte seront indiquées dans la synthèse.",
            )
        except Exception as exc:
            if not started and job is not None:
                failures = await self._purge(job.id, job.guild_id)
                self.receipts.update(job.id, f"préparation échouée ; {failures} publication(s) à purger")
            log.warning("Préparation enquête type=%s", type(exc).__name__)
            await self._private(interaction, str(exc) if isinstance(exc, EnqueteError) else
                                "Préparation impossible (droits, disponibilité Discord ou stockage). Aucun scan n'a été lancé."
                                if not started else f"Le dossier {job.id} est lancé ; consulte /enquete-statut.")
        finally:
            if not started:
                self.busy = False

    @enquete.autocomplete("pseudo")
    async def pseudo_autocomplete(self, interaction: discord.Interaction, current: str):
        try:
            cfg = Config.from_env()
            if interaction.guild is None or not staff(interaction.user, cfg):
                return []
            query = normalise(current)
            members = [m for m in interaction.guild.members if any(query in normalise(n) for n in names_of(m))]
            members.sort(key=lambda m: (not any(normalise(n).startswith(query) for n in names_of(m)),
                                        normalise(m.display_name), m.id))
            return [app_commands.Choice(name=f"{m.display_name} · {m.name} · {m.id}"[:100], value=str(m.id))
                    for m in members[:25]]
        except EnqueteError:
            return []

    def _receipt(self, interaction, identifiant: str, *, owner: bool = False):
        if not re.fullmatch(r"[0-9a-f]{12}", identifiant):
            raise EnqueteError("Identifiant de dossier invalide (12 caractères hexadécimaux).")
        row = self.receipts.get(identifiant, interaction.guild_id)
        if row is None:
            raise EnqueteError("Dossier inconnu, purgé ou appartenant à un autre serveur.")
        if owner and row["uid"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            raise EnqueteError("Seuls le demandeur et un administrateur peuvent annuler ou purger ce dossier.")
        return row

    @app_commands.command(name="enquete-statut", description="Staff : consulter l'état d'une enquête, même après 15 minutes.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(manage_guild=True)
    async def enquete_statut(self, interaction: discord.Interaction, identifiant: app_commands.Range[str, 12, 12]):
        if await self._authorise(interaction) is None:
            return
        row = self._receipt(interaction, identifiant)
        job = self.jobs.get(identifiant)
        state = f"{job.state} ; {job.scanned} messages parcourus" if job else row["status"]
        await self._private(interaction, f"Dossier `{identifiant}` : {state}.\n"
                            f"Expiration prévue : <t:{int(row['expires'])}:F>. Suppression automatique lorsque le bot est en ligne.")

    @app_commands.command(name="enquete-annuler", description="Staff : arrêter son enquête en cours et retirer ses publications.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(manage_guild=True)
    async def enquete_annuler(self, interaction: discord.Interaction, identifiant: app_commands.Range[str, 12, 12]):
        if await self._authorise(interaction) is None:
            return
        self._receipt(interaction, identifiant, owner=True)
        job = self.jobs.get(identifiant)
        if job is None or job.task is None:
            return await self._private(interaction, "Ce dossier n'est plus en cours. Utilise /enquete-purger pour le retirer.")
        await interaction.response.defer(ephemeral=True)
        await self._cancel(job)
        row = self.receipts.get(identifiant, interaction.guild_id)
        await self._private(interaction, f"Dossier `{identifiant}` : {row['status']}.")

    @app_commands.command(name="enquete-purger", description="Staff : supprimer les publications d'un dossier et son journal local.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(manage_guild=True)
    async def enquete_purger(self, interaction: discord.Interaction, identifiant: app_commands.Range[str, 12, 12]):
        if await self._authorise(interaction) is None:
            return
        self._receipt(interaction, identifiant, owner=True)
        if identifiant in self.jobs:
            return await self._private(interaction, "Annule d'abord la tâche avec /enquete-annuler.")
        await interaction.response.defer(ephemeral=True)
        failures = await self._purge(identifiant, interaction.guild_id)
        if failures:
            self.receipts.update(identifiant, f"purge incomplète : {failures} publication(s)")
            await self._private(interaction, f"{failures} publication(s) n'ont pas pu être retirées. Vérifie les droits et réessaie.")
        else:
            self.receipts.forget_job(identifiant)
            await self._private(interaction, "Publications et journal local supprimés. Les copies déjà téléchargées restent hors contrôle du bot.")

    async def _housekeeping(self):
        await self.bot.wait_until_ready()
        self.receipts.db.execute(
            "UPDATE jobs SET status='interrompu par redémarrage ; relancer une nouvelle enquête' "
            "WHERE status='en cours' AND created<?", (self.started_at,),
        )
        self.receipts.db.commit()
        while True:
            try:
                for row in self.receipts.due(time.time()):
                    if row["jid"] not in self.jobs:
                        failures = await self._purge(row["jid"], row["gid"])
                        if not failures:
                            self.receipts.forget_job(row["jid"])
                # Restes d'un arrêt brutal seulement ; jamais un répertoire actif.
                active = tuple(f"enquete-{jid}-" for jid in self.jobs)
                threshold = time.time() - (Config.from_env().max_seconds + 3600)
                for path in self.temporary.iterdir():
                    if (path.name.startswith("enquete-") and not path.is_symlink() and path.is_dir()
                            and not (active and path.name.startswith(active)) and path.stat().st_mtime < threshold):
                        shutil.rmtree(path)
            except Exception as exc:
                log.warning("Nettoyage enquête incomplet type=%s ; nouvelle tentative ultérieure", type(exc).__name__)
            await asyncio.sleep(900)


async def setup(bot: commands.Bot):
    await bot.add_cog(EnqueteCog(bot))
