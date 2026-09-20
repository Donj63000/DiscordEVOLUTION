"""Evolution Build : extension native /build, indépendante du budget et du modèle Luna."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from contextlib import suppress
from io import BytesIO
import logging
import time
from typing import Literal
import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError
from utils.build.config import Config, flag
from utils.build.models import Actor, Profile, Build, BuildError, Conflict, SLOTS, CLASSES, STAT_LABELS, values, canonical, revised
from utils.build.rules import load_rules
from utils.build.console_repository import ConsoleRepository
from utils.build.catalog import CatalogService, search
from utils.build.set_source import PublicSetLoader
from utils.build.panels import BuildListView, ProfileEditorView
from utils.build.sharing import SharedBuildAction, SharedBuildView
from utils.build.diagnostics import coverage_text
from utils.build.service import BuildService
from utils.build.embeds import card, safe
from utils.build.renderer import render, text_report
from utils.build.views import BuildView, ConfirmView, ActionConfirm, OptimizationView, ProfileModal
from utils.build.import_export import export_build, parse_json, MAX_IMPORT
from utils.build.optimizer import Constraints, Limits
from utils.build.optimizer_worker import OptimizerWorker
from utils.build.comparison import compare, comparison_text
from utils.build.spells import SpellCatalogService

log = logging.getLogger(__name__)


class BuildCog(commands.Cog):
    group = app_commands.Group(name="build", description="Construire, calculer et partager un personnage Dofus Retro.", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.from_env()
        self.repository = ConsoleRepository(bot, self.config.quota)
        self.rules = load_rules(self.config.rules_path)
        self.set_loader = PublicSetLoader() if flag("BUILD_SET_ENRICHMENT", True) else None
        self.catalogs = CatalogService(self.repository, self.wiki, self.config.overrides_path, self.set_loader)
        self.service = BuildService(self.repository, self.catalogs, self.rules)
        self.spells = SpellCatalogService(self.repository, lambda: getattr(self.wiki(), "enrichment_client", None))
        self.worker = OptimizerWorker()
        self.render_lock = asyncio.Lock()
        self.views = OrderedDict()
        self.list_cache = OrderedDict()
        self.ready = False
        self.closed = False
        self.start_error = "Initialisation du module en cours."
        self.task = None
        self.last_refresh_request = 0.0
        self.init_lock = asyncio.Lock()
        self.storage_open = False

    def wiki(self):
        return self.bot.get_cog("DofusWikiCog")

    async def cog_load(self):
        self.bot.add_dynamic_items(SharedBuildAction)
        self.task = asyncio.create_task(self.start_when_ready(), name="evolution-build-start")

    async def start_when_ready(self):
        """J'attends Discord et je retente une restauration refusée au démarrage."""
        await self.bot.wait_until_ready()
        while not self.closed:
            await self.initialize()
            if self.ready:
                return
            log.debug("build startup pending reason=%s", self.start_error)
            await asyncio.sleep(30)

    async def initialize(self, *, refresh_equipment=True):
        """Je restaure la console avant d'ouvrir les parcours des membres."""
        async with self.init_lock:
            if self.closed:
                return
            try:
                if not self.ready:
                    if not self.storage_open:
                        await self.repository.open()
                        self.storage_open = True
                    await self.service.start()
                    await self.catalogs.restore()
                    self.ready = True
                self.start_error = ""
                if refresh_equipment:
                    await self.catalogs.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.start_error = str(exc) if isinstance(exc, BuildError) else "Initialisation impossible. Vérifier le salon #console, ses permissions et le verrou du bot."
                log.warning("build initialize failed type=%s", type(exc).__name__)

    async def cog_unload(self):
        self.closed, self.ready = True, False
        self.bot.remove_dynamic_items(SharedBuildAction)
        for view in list(self.views):
            view.stop()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.worker.close()
        if self.set_loader is not None:
            await self.set_loader.close()
        await self.repository.close()
        self.list_cache.clear()
        self.service.cache.clear()

    def actor(self, interaction, bound_guild=None):
        guild_id = interaction.guild_id or bound_guild
        if not guild_id or (bound_guild is not None and interaction.guild_id not in {None, bound_guild}):
            raise BuildError("Cette action appartient à un autre serveur.")
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(interaction.user.id) if guild else None
        if member is None or member.bot:
            raise BuildError("Tu dois être membre du serveur pour utiliser ce build.")
        return Actor(guild_id=guild_id, user_id=member.id)

    def ensure_ready(self):
        if self.closed or not flag("BUILD_ENABLED", True):
            raise BuildError("Evolution Build est désactivé.")
        if not self.ready:
            raise BuildError(self.start_error or "Stockage Build indisponible.")

    async def begin(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.ensure_ready()
        return self.actor(interaction)

    async def error(self, interaction, error):
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original
        if isinstance(error, BuildError):
            message = str(error)
        elif isinstance(error, (ValidationError, ValueError, TypeError)):
            message = "Paramètres incohérents. Vérifie le profil, les références et les jets saisis."
        else:
            log.warning("build operation failed type=%s", type(error).__name__)
            message = "Opération interrompue ou affichage indisponible. Consulte /build mes avant de réessayer : une écriture déjà confirmée reste sauvegardée."
        if interaction.response.is_done():
            await interaction.followup.send(message[:1800], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(message[:1800], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def cog_app_command_error(self, interaction, error):
        await self.error(interaction, error)

    def track(self, view):
        owned = [v for v in self.views if v.actor == view.actor]
        while len(owned) >= 2:
            owned.pop(0).stop()
        while len(self.views) >= self.config.max_views:
            next(iter(self.views)).stop()
        self.views[view] = time.monotonic()

    def untrack(self, view):
        self.views.pop(view, None)

    def remember(self, build):
        key = build.guild_id, build.owner_id
        cache = self.list_cache.setdefault(key, {"at": time.monotonic(), "builds": {}})
        cache["at"] = time.monotonic()
        cache["builds"][build.id] = build
        self.list_cache.move_to_end(key)
        while len(self.list_cache) > 128:
            self.list_cache.popitem(last=False)

    async def show(self, interaction, build, report, catalog):
        self.remember(build)
        actor = self.actor(interaction, build.guild_id)
        view = BuildView(self, actor, build) if actor.user_id == build.owner_id else None
        await interaction.edit_original_response(content=None, embed=card(build, report, catalog, self.repository.durable), view=view, attachments=[])
        if view:
            view.message = await interaction.original_response()

    async def send_view(self, interaction, *, view, **kwargs):
        try:
            message = await interaction.followup.send(view=view, wait=True, **kwargs)
            if view is not None:
                view.message = message
            return message
        except Exception:
            if view is not None:
                view.stop()
            raise

    async def preview_card(self, actor, preview):
        catalog, _ = await self.service.dependencies(preview.candidate)
        embed = card(preview.candidate, preview.report, catalog, self.repository.durable, preview=True)
        if preview.expected is not None:
            current, previous, _ = await self.service.inspect(actor, preview.candidate.id)
            if current.revision != preview.expected:
                raise Conflict("Le build a changé pendant la préparation : rouvre-le avant de confirmer.")
            comparison = compare(current, previous, preview.candidate, preview.report)
            lines = [f"{STAT_LABELS[row['stat']]} : {row['premier']} → {row['second']} ({row['delta']:+d})"
                     + (" * partiel" if not row["complet"] else "") for row in comparison["ecarts"]]
            embed.add_field(name="Changements après confirmation", value=("\n".join(lines[:14]) or "Aucun écart de caractéristiques connues.")[:1024], inline=False)
            old_sets = {(c.origin, c.stat): c.value for c in previous.contributions if c.origin.startswith("panoplie:")}
            new_sets = {(c.origin, c.stat): c.value for c in preview.report.contributions if c.origin.startswith("panoplie:")}
            if old_sets != new_sets:
                embed.add_field(name="Panoplies", value="Les bonus de panoplie ont été recalculés dans les nouveaux totaux. Les bonus non couverts restent signalés dans les réserves.", inline=False)
        return embed

    async def show_preview(self, interaction, actor, preview):
        embed = await self.preview_card(actor, preview)
        view = ConfirmView(self, actor, preview)
        await self.send_view(interaction, embed=embed, view=view, ephemeral=True)

    async def dm_preview(self, member, actor, preview):
        embed = await self.preview_card(actor, preview)
        view = ConfirmView(self, actor, preview)
        try:
            view.message = await member.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            view.stop()
            raise BuildError("Tes MP sont fermés. Utilise /build ouvrir ou /build creer : aucune modification effectuée.") from None

    async def icons(self, build, catalog):
        wiki = self.wiki()
        if wiki is None or getattr(wiki, "_closed", False):
            return {}
        try:
            from utils.dofus_wiki import INDEX_PATHS
            entries = {e.token: e for e in (wiki.client.peek(INDEX_PATHS[0]) or ())}
        except (AttributeError, TypeError):
            return {}
        slots = asyncio.Semaphore(2)
        async def get(ref):
            entry = entries.get(ref)
            if not entry:
                return ref, None
            async with slots:
                try:
                    async with asyncio.timeout(4):
                        detail = await wiki.client.detail(entry)
                        image = await wiki.resolve_item_image(detail, None)
                    return ref, image.data if image else None
                except Exception:
                    return ref, None
        try:
            async with asyncio.timeout(7):
                return {ref: raw for ref, raw in await asyncio.gather(*(get(r.item.template_ref) for r in build.slots)) if raw}
        except TimeoutError:
            return {}

    async def image_bytes(self, build, report, catalog):
        if self.render_lock.locked():
            raise BuildError("Un rendu est déjà en cours. Le build est conservé ; le rapport texte reste disponible.")
        async with self.render_lock:
            icons = await self.icons(build, catalog)
            return await asyncio.to_thread(render, build, report, catalog, self.repository.durable, icons)

    async def send_image(self, interaction, actor, build_id):
        build, report, catalog = await self.service.inspect(actor, build_id)
        raw = await self.image_bytes(build, report, catalog)
        await interaction.followup.send(files=[discord.File(BytesIO(raw), filename="evolution-build.png"),
            discord.File(BytesIO(text_report(build, report, catalog, self.repository.durable).encode()), filename="evolution-build-details.txt")], ephemeral=True)

    async def confirm_share(self, interaction, actor, build_id):
        from utils.build.repository import PublicationPending

        if interaction.guild_id != actor.guild_id:
            raise BuildError("Pour partager publiquement, utilise /build partager dans le salon du serveur choisi.")
        build, report, catalog = await self.service.inspect(actor, build_id)
        channel = interaction.channel
        async def publish(click, operation):
            guild = self.bot.get_guild(actor.guild_id)
            member = guild.get_member(actor.user_id) if guild else None
            if member is None or guild.me is None:
                raise BuildError("Membre/bot absent du serveur.")
            for who in (member, guild.me):
                permissions = channel.permissions_for(who)
                can_send = permissions.send_messages_in_threads if isinstance(channel, discord.Thread) else permissions.send_messages
                if not permissions.view_channel or not can_send:
                    raise BuildError("Publication non autorisée dans ce salon.")
            if not channel.permissions_for(guild.me).embed_links:
                raise BuildError("Le bot ne peut pas publier de fiche dans ce salon.")
            receipt = await self.service.share(actor, build, operation)
            try:
                claimed = await self.repository.claim_publication(actor, receipt["code"])
            except PublicationPending:
                found = await self.reconcile_share_publication(actor, receipt["code"], channel)
                if found is None:
                    raise BuildError(
                        "Publication toujours incertaine. Aucun nouvel envoi effectué ; "
                        "vérifie le salon puis réessaie cette confirmation après rétablissement de l'accès."
                    ) from None
                await click.edit_original_response(
                    content="La publication existante a été retrouvée et confirmée ; aucun doublon envoyé.",
                    embed=None, view=None,
                )
                return
            if not claimed:
                await click.edit_original_response(content="Cette publication a déjà été envoyée ; aucun doublon créé.", embed=None, view=None)
                return
            try:
                async with asyncio.timeout(30):
                    message = await channel.send(content=f"Build partagé pendant 7 jours. Code : `{receipt['code']}`\nVoir : `/build ouvrir` · Copier : `/build copier`.", embed=card(build, report, catalog, self.repository.durable), view=SharedBuildView(receipt["code"]), allowed_mentions=discord.AllowedMentions.none())
            except (discord.HTTPException, OSError) as exc:
                message = await self.reconcile_share_publication(actor, receipt["code"], channel)
                if message is None:
                    raise BuildError(
                        "Partage réservé mais publication incertaine. Aucun nouvel envoi automatique ; "
                        "vérifie le salon puis réessaie cette confirmation après rétablissement de l'accès."
                    ) from exc
                await click.edit_original_response(
                    content="La publication a été retrouvée après une réponse réseau perdue. Aucun doublon envoyé.",
                    embed=None, view=None,
                )
                return
            await self.repository.publication(actor, receipt["code"], "sent", message.id, channel.id)
            await click.edit_original_response(content="Cette révision a été partagée. Les modifications futures resteront privées.", embed=None, view=None)
        view = ActionConfirm(self, actor, publish, "Publier dans ce salon")
        await self.send_view(interaction, content="**Confirmation : le nom, les équipements et les statistiques seront visibles dans ce salon.** Le partage est une copie figée, révocable, valable 7 jours.", view=view, ephemeral=True)

    async def reconcile_share_publication(self, actor, code, channel):
        """Je retrouve un unique envoi du bot avant de confirmer un accusé perdu."""
        from datetime import datetime, timedelta, timezone

        prefix = f"Build partagé pendant 7 jours. Code : `{code}`\n"
        matches = []
        try:
            async with asyncio.timeout(30):
                async for message in channel.history(
                    limit=None, after=datetime.now(timezone.utc) - timedelta(days=7),
                ):
                    if message.author == self.bot.user and (message.content or "").startswith(prefix):
                        matches.append(message)
                        if len(matches) > 1:
                            break
        except (discord.HTTPException, OSError) as exc:
            log.debug("build publication reconciliation unreadable channel_id=%s type=%s",
                      channel.id, type(exc).__name__)
            return None
        if len(matches) != 1:
            log.debug("build publication reconciliation ambiguous channel_id=%s matches=%s",
                      channel.id, len(matches))
            return None
        message = matches[0]
        await self.repository.publication(actor, code, "sent", message.id, channel.id)
        log.debug("build publication reconciled channel_id=%s message_id=%s", channel.id, message.id)
        return message

    async def build_choices(self, interaction, current):
        try:
            actor = self.actor(interaction)
            cached = self.list_cache.get((actor.guild_id, actor.user_id))
            if not cached or time.monotonic() - cached["at"] > 300:
                return []
            q = current.casefold()
            return [app_commands.Choice(name=f"{b.name} · {b.profile.classe} {b.profile.level}"[:100], value=b.id)
                    for b in cached["builds"].values() if q in b.name.casefold() or q in b.id][:25]
        except BuildError:
            return []

    async def item_choices(self, interaction, current):
        try:
            actor = self.actor(interaction)
            bid = getattr(interaction.namespace, "build", None)
            cache = self.list_cache.get((actor.guild_id, actor.user_id), {}).get("builds", {})
            build = cache.get(bid)
            catalog = self.catalogs.latest
            if build and (build.catalog_id, build.rules_id) in self.service.cache:
                catalog = self.service.cache[(build.catalog_id, build.rules_id)][0]
            if catalog is None or (build and catalog.id != build.catalog_id):
                return []
            slot = getattr(interaction.namespace, "emplacement", None)
            results = search(catalog, current, slot if slot in SLOTS else None, build.profile.level if build else 200)
            return [app_commands.Choice(name=f"{i.name} · niv. {i.level}"[:100], value=i.ref) for i in results]
        except (BuildError, ValueError):
            return []

    @group.command(name="creer", description="Créer un build privé. Aucun appel IA.")
    @app_commands.choices(classe=[app_commands.Choice(name=c.capitalize(), value=c) for c in CLASSES])
    async def create(self, interaction: discord.Interaction, nom: app_commands.Range[str, 1, 80], classe: str, niveau: app_commands.Range[int, 1, 200]):
        actor = await self.begin(interaction)
        result = await self.service.new(actor, nom, Profile(classe=classe, level=niveau), operation=f"slash:{interaction.id}")
        await self.show(interaction, *result)

    @group.command(name="mes", description="Lister mes builds privés et préparer leur autocomplétion.")
    async def listing(self, interaction: discord.Interaction):
        actor = await self.begin(interaction)
        builds = await self.repository.list(actor)
        self.list_cache[(actor.guild_id, actor.user_id)] = {"at": time.monotonic(), "builds": {}}
        self.list_cache.move_to_end((actor.guild_id, actor.user_id))
        while len(self.list_cache) > 128:
            self.list_cache.popitem(last=False)
        for b in builds:
            self.remember(b)
        view = BuildListView(self, actor, builds)
        await interaction.edit_original_response(content=view.content(), view=view)
        view.message = await interaction.original_response()

    @group.command(name="ouvrir", description="Ouvrir un de mes builds, ou consulter un code de partage du serveur.")
    @app_commands.autocomplete(build=build_choices)
    async def opening(self, interaction: discord.Interaction, build: str):
        from utils.build.calculator import calculate
        actor = await self.begin(interaction)
        if len(build) == 32:
            shared = await self.repository.shared(actor, build)
            catalog, rules = await self.service.dependencies(shared)
            await interaction.edit_original_response(content="Révision partagée, lecture seule. /build copier crée ton propre exemplaire.", embed=card(shared, calculate(shared, catalog, rules), catalog, self.repository.durable))
        else:
            await self.show(interaction, *(await self.service.inspect(actor, build)))

    @group.command(name="equiper", description="Prévisualiser le remplacement d'un équipement et ses bonus de panoplie.")
    @app_commands.choices(emplacement=[app_commands.Choice(name=s.replace('_', ' '), value=s) for s in SLOTS])
    @app_commands.autocomplete(build=build_choices, objet=item_choices)
    async def equip(self, interaction: discord.Interaction, build: str, emplacement: str, objet: str):
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        await self.show_preview(interaction, actor, await self.service.equipment(actor, build, current.revision, emplacement, objet))

    @group.command(name="retirer", description="Prévisualiser le retrait d'un équipement.")
    @app_commands.choices(emplacement=[app_commands.Choice(name=s.replace('_', ' '), value=s) for s in SLOTS])
    @app_commands.autocomplete(build=build_choices)
    async def remove(self, interaction: discord.Interaction, build: str, emplacement: str):
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        await self.show_preview(interaction, actor, await self.service.remove(actor, build, current.revision, emplacement))

    @group.command(name="profil", description="Éditer classe, niveau, points dépensés, parchottage ou stats nues déclarées.")
    @app_commands.autocomplete(build=build_choices)
    async def profile(self, interaction: discord.Interaction, build: str,
                      alignement: app_commands.Range[int, 0, 3] | None = None,
                      grade: app_commands.Range[int, 0, 10] | None = None):
        # Une modale doit être la réponse initiale ; lecture DB préchargée sinon bouton après defer.
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        if alignement is not None or grade is not None:
            profile = revised(current.profile, alignment=alignement if alignement is not None else current.profile.alignment,
                              grade=grade if grade is not None else current.profile.grade)
            await self.show_preview(interaction, actor, await self.service.profile(actor, build, current.revision, profile))
            return
        view = ProfileEditorView(self, actor, current)
        await self.send_view(interaction, content=view.content(), view=view, ephemeral=True)

    @group.command(name="jets", description="Jets effet par effet : les valeurs remplacent les jets naturels, les exos s'ajoutent.")
    @app_commands.choices(emplacement=[app_commands.Choice(name=s.replace('_', ' '), value=s) for s in SLOTS],
                          mode=[app_commands.Choice(name=n, value=v) for n, v in (("Meilleurs naturels", "natural_best"), ("Naturels personnalisés", "natural_custom"), ("FM déclarée", "declared_fm"))])
    @app_commands.autocomplete(build=build_choices)
    async def jets(self, interaction: discord.Interaction, build: str, emplacement: str, mode: str,
                   valeurs_json: str = "[]", exos_json: str = "[]"):
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        preview = await self.service.jets(actor, build, current.revision, emplacement, mode,
                                         parse_json(valeurs_json.encode()), parse_json(exos_json.encode()))
        await self.show_preview(interaction, actor, preview)

    @group.command(name="comparer", description="Comparer deux de mes personnages recalculés, avec leurs réserves.")
    @app_commands.autocomplete(premier=build_choices, second=build_choices)
    async def comparison(self, interaction: discord.Interaction, premier: str, second: str):
        actor = await self.begin(interaction)
        a, ra, _ = await self.service.inspect(actor, premier)
        b, rb, _ = await self.service.inspect(actor, second)
        result = compare(a, ra, b, rb)
        summary = comparison_text(a, ra, b, rb)
        embed = discord.Embed(title="Comparaison de mes builds", description=safe(summary, 3900), colour=0x2A79BB)
        embed.set_footer(text="* Sommes partielles · Le rapport TXT contient toutes les réserves.")
        await interaction.followup.send(embed=embed, files=[
            discord.File(BytesIO(summary.encode()), filename="comparaison-builds.txt"),
            discord.File(BytesIO(canonical(result).encode()), filename="comparaison-builds.json")],
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @group.command(name="partager", description="Confirmer la publication d'une révision figée dans le salon actuel.")
    @app_commands.autocomplete(build=build_choices)
    async def sharing(self, interaction: discord.Interaction, build: str):
        actor = await self.begin(interaction)
        await self.confirm_share(interaction, actor, build)

    @group.command(name="copier", description="Copier une révision partagée vers mon espace privé.")
    async def copying(self, interaction: discord.Interaction, code: str):
        actor = await self.begin(interaction)
        await self.show_preview(interaction, actor, await self.service.copy(actor, code))

    @group.command(name="exporter", description="Exporter mon build en JSON sans publier mes identifiants Discord.")
    @app_commands.autocomplete(build=build_choices)
    async def exporting(self, interaction: discord.Interaction, build: str):
        actor = await self.begin(interaction)
        b = await self.repository.get(actor, build)
        await interaction.followup.send(file=discord.File(BytesIO(export_build(b)), filename="evolution-build.json"), ephemeral=True)

    @group.command(name="importer", description="Importer un export Evolution JSON (256 Kio maximum). Versions conservées.")
    async def importing(self, interaction: discord.Interaction, fichier: discord.Attachment, catalogue_actuel: bool = False):
        actor = await self.begin(interaction)
        if fichier.size > MAX_IMPORT or not fichier.filename.lower().endswith(".json"):
            raise BuildError("Un fichier .json de 256 Kio maximum est requis.")
        async with asyncio.timeout(10):
            raw = await fichier.read()
        await self.show_preview(interaction, actor, await self.service.importing(actor, raw, use_current=catalogue_actuel))

    @group.command(name="supprimer", description="Supprimer mon build et révoquer ses partages après confirmation.")
    @app_commands.autocomplete(build=build_choices)
    async def deleting(self, interaction: discord.Interaction, build: str):
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        async def remove(i, operation):
            await self.service.delete(actor, build, current.revision, operation)
            self.list_cache.pop((actor.guild_id, actor.user_id), None)
            await i.edit_original_response(content="Build supprimé et partages révoqués. Les copies déjà lues/téléchargées ne peuvent pas être rappelées.", embed=None, view=None)
        view = ActionConfirm(self, actor, remove, "Supprimer définitivement")
        await self.send_view(interaction, content=f"Supprimer **{safe(current.name)}** ? Exporte d'abord son JSON pour conserver une copie.", view=view, ephemeral=True)

    @group.command(name="optimiser", description="Rechercher des combinaisons bornées ; objectifs PA/PM conservés, aucun optimum garanti.")
    @app_commands.choices(objectif=[app_commands.Choice(name=s, value=s) for s in ("fo", "ine", "cha", "age", "pp", "vi", "sa", "do", "so")])
    @app_commands.autocomplete(build=build_choices)
    async def optimizing(self, interaction: discord.Interaction, build: str, objectif: str = "pp",
                         pa_min: app_commands.Range[int, 0, 30] = 0, pm_min: app_commands.Range[int, 0, 30] = 0,
                         po_min: app_commands.Range[int, 0, 30] = 0,
                         verrouilles: str = "", exos_autorises: bool = False):
        actor = await self.begin(interaction)
        if not flag("BUILD_OPTIMIZER_ENABLED"):
            raise BuildError("Optimiseur désactivé par le Staff. Le builder manuel reste disponible.")
        current, _, catalog = await self.service.inspect(actor, build)
        _, rules = await self.service.dependencies(current)
        request = Constraints(objective=objectif, minimums=values({"pa": pa_min, "pm": pm_min, "po": po_min}),
                              locked=tuple(s.strip() for s in verrouilles.split(",") if s.strip()), allow_exos=exos_autorises)
        result = await self.worker.run(current, catalog, rules, request)
        choices = result.get("solutions", []) + result.get("tentative", [])
        view = OptimizationView(self, actor, result, current) if choices else None
        await self.send_view(interaction, content=f"Résultat : **{result['status']}**. {len(choices)} proposition(s).\n{result.get('message', '')}", view=view, ephemeral=True)

    @group.command(name="image", description="Générer la fiche PNG et son rapport texte accessible.")
    @app_commands.autocomplete(build=build_choices)
    async def imaging(self, interaction: discord.Interaction, build: str):
        actor = await self.begin(interaction)
        await self.send_image(interaction, actor, build)

    @group.command(name="recettes", description="Agréger les recettes connues du build ; aucun prix HDV inventé.")
    @app_commands.autocomplete(build=build_choices)
    async def crafting(self, interaction: discord.Interaction, build: str):
        from utils.build.crafting import shopping_list
        actor = await self.begin(interaction)
        current, _, catalog = await self.service.inspect(actor, build)
        wiki = self.wiki()
        if wiki is None:
            raise BuildError("Wiki indisponible.")
        async with asyncio.timeout(30):
            result = await shopping_list(current, catalog, wiki)
        await interaction.followup.send(file=discord.File(BytesIO(canonical(result).encode()), filename="recettes-build.json"), ephemeral=True)

    @group.command(name="depuis-exo", description="Copier mes jets de la session /exo sans la modifier.")
    @app_commands.autocomplete(build=build_choices)
    @app_commands.choices(emplacement=[app_commands.Choice(name=s.replace('_', ' '), value=s) for s in SLOTS])
    async def from_exo(self, interaction: discord.Interaction, build: str, emplacement: str):
        from utils.build.fm_adapter import from_exo
        actor = await self.begin(interaction)
        current, _, catalog = await self.service.inspect(actor, build)
        instance = current.in_slot(emplacement)
        cog = self.bot.get_cog("ExoCog")
        view = cog.views.get((actor.guild_id, actor.user_id)) if cog else None
        if instance is None or view is None or view.retired:
            raise BuildError("Équipe l'objet et ouvre ta propre session /exo correspondante avant l'import.")
        async with view.lock:
            copied = from_exo(instance, catalog.resolve(instance), view.session)
        await self.show_preview(interaction, actor, await self.service.instance(actor, current, emplacement, copied, "from_exo"))

    @group.command(name="vers-exo", description="Ouvrir /exo avec ces jets après saisie du puits et confirmation.")
    @app_commands.autocomplete(build=build_choices)
    @app_commands.choices(emplacement=[app_commands.Choice(name=s.replace('_', ' '), value=s) for s in SLOTS])
    async def to_exo(self, interaction: discord.Interaction, build: str, emplacement: str):
        from utils.build.fm_adapter import to_exo_values
        actor = await self.begin(interaction)
        current, _, catalog = await self.service.inspect(actor, build)
        item = current.in_slot(emplacement)
        if item is None:
            raise BuildError("Emplacement vide.")
        output = to_exo_values(item, catalog.resolve(item))
        from utils.build.advanced_views import ExoTransferView
        await self.send_view(interaction, content="Renseigne le puits séparément, puis confirme l'ouverture de l'atelier avec ces jets.",
            view=ExoTransferView(self, actor, output), ephemeral=True)

    @group.command(name="degats", description="Simulateur sort/arme et cible ; anciens paramètres manuels toujours disponibles.")
    @app_commands.autocomplete(build=build_choices)
    @app_commands.choices(element=[app_commands.Choice(name=n, value=v) for n, v in (("Neutre", "ne"), ("Terre", "te"), ("Feu", "fe"), ("Eau", "ea"), ("Air", "ai"))])
    async def damage(self, interaction: discord.Interaction, build: str, element: str | None = None,
                     minimum: app_commands.Range[int, 0, 10000] | None = None, maximum: app_commands.Range[int, 0, 10000] | None = None,
                     resistance_fixe: app_commands.Range[int, 0, 10000] = 0,
                     resistance_pourcent: app_commands.Range[int, -100, 100] = 0,
                     coefficient: app_commands.Range[int, 0, 1000] = 100):
        from utils.build.damage import simulate, DamageLine, Scenario
        actor = await self.begin(interaction)
        if element is None and minimum is None and maximum is None:
            from utils.build.advanced_views import send_simulator
            await send_simulator(self, interaction, actor, build)
            return
        if element is None or minimum is None or maximum is None:
            raise BuildError("Le mode manuel requiert élément, minimum et maximum ensemble.")
        _, report, _ = await self.service.inspect(actor, build)
        result = simulate(report, (DamageLine(element=element, minimum=minimum, maximum=maximum),),
                          Scenario(coefficient_percent=coefficient, flat_resistance=resistance_fixe, percent_resistance=resistance_pourcent))
        await interaction.followup.send(f"**Estimation expérimentale : {result['minimum']} à {result['maximum']}**\n{result['warning']}", ephemeral=True)

    def require_staff(self, interaction):
        self.actor(interaction)
        if not interaction.guild_id or not interaction.user.guild_permissions.manage_guild:
            raise BuildError("Cette commande est réservée à la gestion du serveur.")

    @group.command(name="actualiser", description="Staff : actualiser le catalogue sans migrer les builds existants.")
    async def refreshing(self, interaction: discord.Interaction,
                         catalogue: Literal["equipements", "sorts", "tous"] = "equipements"):
        await interaction.response.defer(ephemeral=True, thinking=True)
        self.require_staff(interaction)
        if self.closed or not flag("BUILD_ENABLED", True):
            raise BuildError("Evolution Build est désactivé.")
        if (time.monotonic() - self.last_refresh_request < 60 or self.catalogs.lock.locked()
                or self.spells.lock.locked() or self.init_lock.locked()):
            raise BuildError("Actualisation déjà en cours ou trop récente.")
        self.last_refresh_request = time.monotonic()
        if catalogue in {"equipements", "tous"}:
            await self.initialize()
        elif not self.ready:
            await self.initialize(refresh_equipment=False)
        self.ensure_ready()
        messages = []
        if catalogue in {"equipements", "tous"}:
            result = self.catalogs.latest
            status = self.catalogs.last_error or self.start_error
            messages.append("**Équipements** : " + (status or "Catalogue enregistré.")
                + ("\n" + coverage_text(result)[:1000] if result else "\nAucun catalogue disponible."))
        if catalogue in {"sorts", "tous"}:
            try:
                result = await self.spells.refresh()
                messages.append("**Sorts** : " + (self.spells.last_error or
                    f"Catalogue enregistré ({len(result.attacks)} niveaux)."))
            except BuildError as exc:
                messages.append("**Sorts** : " + str(exc))
        log.debug("build explicit refresh catalogue=%s", catalogue)
        text = "\n".join(messages) + "\nLes builds et attaques déjà sélectionnés restent figés dans leur version."
        await interaction.edit_original_response(content=text[:1900])

    @group.command(name="renommer", description="Renommer mon build après prévisualisation.")
    @app_commands.autocomplete(build=build_choices)
    async def rename(self, interaction: discord.Interaction, build: str, nom: app_commands.Range[str, 1, 80]):
        actor = await self.begin(interaction)
        current = await self.repository.get(actor, build)
        await self.show_preview(interaction, actor, await self.service.rename(actor, build, current.revision, nom))

    @group.command(name="depublier", description="Révoquer mon code de partage. Les messages déjà copiés ne sont pas rappelables.")
    async def revoking(self, interaction: discord.Interaction, code: str):
        actor = await self.begin(interaction)
        await self.repository.revoke(actor, code)
        await interaction.edit_original_response(content="Partage révoqué. Les anciennes copies visibles ou téléchargées ne sont pas effacées.")

    @group.command(name="diagnostic", description="Staff : état du stockage, catalogue, couverture et indicateurs sans données privées.")
    async def diagnostic(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.require_staff(interaction)
        catalog = self.catalogs.latest
        text = [f"Module initialisé : {self.ready}", f"Backend : {self.config.backend} · durable : {self.repository.durable}",
                f"Règles : {self.rules.version}", f"Vues : {len(self.views)}", f"Optimiseur actif : {self.worker.lock.locked()}",
                self.start_error, self.catalogs.last_error]
        if catalog:
            text.append(coverage_text(catalog))
            text.extend(catalog.diagnostics)
        await interaction.edit_original_response(content="\n".join(s for s in text if s)[:1900])

    @group.command(name="migrer", description="Prévisualiser une migration explicite vers le catalogue et les règles actuels.")
    @app_commands.autocomplete(build=build_choices)
    async def migrating(self, interaction: discord.Interaction, build: str):
        actor = await self.begin(interaction)
        await self.show_preview(interaction, actor, await self.service.migrate(actor, build))

    @group.command(name="aide", description="Démarrer le builder guidé : personnage, équipements, jets et partage.")
    async def help_build(self, interaction: discord.Interaction):
        text = ("**Evolution Build — démarrage guidé**\n"
                "**1.** `/build mes` → Nouveau personnage.\n"
                "**2.** Personnage → choisir une caractéristique → saisir capital et parchottage.\n"
                "**3.** Équipement → emplacement → rechercher (nom facultatif) → aperçu → confirmer.\n"
                "**4.** Modifier les jets → champs numériques → aperçu → confirmer. Aucun JSON obligatoire.\n"
                "Détails affiche toutes les statistiques, résistances, bonus de panoplie et réserves.\n"
                "Historique permet de restaurer une version sans perdre les modifications suivantes.\n"
                "La saisie groupée accepte `Chance: 300`, une ligne par caractéristique ; l'ancien JSON reste accepté.\n"
                "**Les totaux partiels sont signalés par *.** Les règles non validées ne sont pas certifiées.\n"
                "Les builds sont privés ; partager demande confirmation et crée une copie figée valable 7 jours.\n"
                "Les builds et leur historique sont conservés dans le salon #console du bot.\n"
                "**Simulateur** : choisis un sort ou ton arme, puis la cible ; compare ou optimise la même attaque.\n"
                "**Mes prix / Optimisation** : prix personnels par serveur et jets, budget et objets possédés explicites.\n"
                "`/build vers-exo` ouvre l'atelier après saisie séparée du puits et confirmation.\n"
                "`/build recettes` décompose les recettes connues jusqu'aux matières premières.\n"
                "Le manuel fonctionne sans Luna. Données : https://xixou.io/ · https://wiki.moon-bot.io/")
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    if flag("BUILD_ENABLED", True):
        await bot.add_cog(BuildCog(bot))
