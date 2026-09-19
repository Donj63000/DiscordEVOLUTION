"""Commande native /exo : atelier personnel, suivi declaratif et probabilites explicites."""

from __future__ import annotations

import asyncio
import copy
from io import BytesIO
import logging
import secrets
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.dofus_wiki import INDEX_PATHS, WikiError, find_entries
from utils.exo_data import demo_item, from_detail, is_mageable
from utils.exo_embeds import build_embed, number, percent
from utils.exo_engine import (
    D, PROFILE, Rates, Rune, STATS, State, attempt, decimal_value, observe,
    parse_jets, recommended_rune, risk, simulation_blocker, stat_key, validate_item_jets,
)
from utils.exo_feedback import batch_text, result_lines
from utils.exo_workshop import reset_simulation, set_goals, simulate_batch
from utils.exo_math import Budget, integer, run_campaigns, success_within
from utils.exo_session import EXPORT_LIMIT, Session, export_session, import_session
from utils.fm_retro_limits import HistoryBudget, history_size
from utils.wiki_embeds import display_text, truncate_text
from utils.xixou_api import XixouError

log = logging.getLogger(__name__)
MAX_SESSIONS = 120
MODAL_LOCK_TIMEOUT = 0.1
TABS = (
    ("atelier", "Atelier de forgemagie", "🔨"),
    ("maths", "Probabilités et budget", "📊"),
    ("journal", "Historique du mode courant", "📜"),
    ("aide", "Guide, limites et sources", "📖"),
    ("settings", "Réglages et nouveau jet", "⚙️"),
    ("search", "Rechercher un autre objet", "🔎"),
)


def whole(text: str, low: int, high: int, label: str) -> int:
    cleaned = str(text).replace(" ", "").replace("\u202f", "").strip()
    if not cleaned.isascii() or not cleaned.isdecimal():
        raise ValueError(f"{label} : un entier positif ou nul est attendu.")
    return integer(int(cleaned), low, high, label)


def percentage(text: str) -> float:
    value = decimal_value(str(text).strip().removesuffix("%").strip(), "100")
    return float(value / 100)


async def private_message(interaction, text: str) -> None:
    kwargs = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(text, **kwargs)
    else:
        await interaction.response.send_message(text, **kwargs)


class ActionButton(discord.ui.Button):
    def __init__(self, label: str, action: str, row: int, style=discord.ButtonStyle.secondary, disabled=False):
        super().__init__(
            label=label, custom_id=f"exo:{action}", row=row, style=style, disabled=disabled,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.dispatch(interaction, self.action, revision=self.revision)


class ChoiceSelect(discord.ui.Select):
    def __init__(self, action: str, options: list[discord.SelectOption], placeholder: str, row: int):
        super().__init__(
            custom_id=f"exo:{action}", options=options, placeholder=placeholder, row=row,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.dispatch(interaction, self.action, self.values[0], revision=self.revision)


class ExoModal(discord.ui.Modal):
    """Les soumissions retardees sont refusees si le panneau a change entre-temps."""

    def __init__(self, view, kind: str):
        titles = {
            "jets": "Modifier le jet de départ", "rates": "Taux et prix de cette rune",
            "goals": "Objectifs et qualité de l'objet",
            "observe": "Consigner un résultat en jeu", "math": "Calcul probabiliste",
            "budget": "Budget exo (kamas)", "search": "Rechercher un objet mageable",
        }
        super().__init__(title=titles[kind], timeout=300)
        self.panel, self.kind, self.revision = view, kind, view.session.revision
        self.inputs: dict[str, discord.ui.TextInput] = {}
        s = view.session
        self.rune_key = s.rune_key
        if kind == "goals":
            self.add_input(
                "goals", "Minimums (objectif principal en premier)",
                "; ".join(f"{key}={value}" for key, value in s.requirements.items()),
                paragraph=True, maximum=1800,
            )
        elif kind == "jets":
            self.add_input(
                "jets", "Jet complet (clés du panneau, ex. pa=1)",
                "; ".join(f"{key}={value}" for key, value in s.state.jets.items()),
                paragraph=True, maximum=1800,
            )
            self.add_input("sink", "Puits nominal de départ (? = inconnu)", "?" if s.state.sink is None else str(s.state.sink), maximum=20)
            self.add_input("goal", "Objectifs minimums (ex. pm=1 ; pa=1)",
                           "; ".join(f"{key}={value}" for key, value in s.requirements.items()),
                           maximum=1800, paragraph=True)
            self.add_input("seed", "Graine de simulation (entier)", str(s.seed), maximum=20)
        elif kind == "rates":
            self.add_input("sc", "SC % forcé (vide = automatique)", "" if s.rates is None else format(s.rates.sc * 100, ".2f"), required=False, maximum=8)
            self.add_input("sn", "SN % forcé (vide = automatique)", "" if s.rates is None else format(s.rates.sn * 100, ".2f"), required=False, maximum=8)
            self.add_input("price", f"Prix unitaire : {s.rune.name}"[:45], str(s.price), maximum=20)
        elif kind == "observe":
            self.add_input("outcome", "Résultat : SC / SN / EC", "EC", maximum=2)
            self.add_input(
                "losses", "Pertes observées (ex. pa=1; vi=3), ou vide", "",
                paragraph=True, required=False, maximum=1800,
            )
        elif kind == "math":
            self.add_input("p", "Chance de réussite par essai (%)", format(s.p * 100, ".2f"), maximum=8)
            self.add_input("cap", "Plafond d'essais par campagne (0–1 000 000)", str(s.cap), maximum=10)
            self.add_input("experiments", "Campagnes Monte-Carlo (1–50 000)", str(s.experiments), maximum=8)
            self.add_input("confidence", "Probabilité cible (%)", format(s.confidence * 100, ".2f"), maximum=8)
            self.add_input("seed", "Graine (résultats reproductibles)", str(s.seed), maximum=20)
        elif kind == "budget":
            labels = {
                "rune": "Prix d'une rune exo", "rebuild": "Remontage ENTRE deux essais",
                "item": "Prix de l'objet", "preparation": "Préparation initiale",
                "limit": "Budget total disponible",
            }
            for key, label in labels.items():
                self.add_input(key, label, str(getattr(s.budget, key)), maximum=20)
        else:
            self.add_input("query", "Nom de l'objet (exemple : Gelano)", "", maximum=100)
        view.modals.add(self)

    def add_input(self, key, label, default, *, paragraph=False, required=True, maximum=100):
        control = discord.ui.TextInput(
            label=label, default=default, required=required, max_length=maximum,
            style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
        )
        self.inputs[key] = control
        self.add_item(control)

    async def interaction_check(self, interaction):
        return await self.panel.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction):
        self.panel.modals.discard(self)
        if not await self.panel.interaction_check(interaction):
            return
        await interaction.response.defer()
        async with self.panel.lock:
            if not await self.panel.ensure_active(interaction):
                return
            if self.revision != self.panel.session.revision:
                await private_message(interaction, "Le panneau a changé. Rouvrez le formulaire pour éviter d'écraser une action récente.")
                return
            values = {key: str(control.value).strip() for key, control in self.inputs.items()}
            try:
                if self.kind == "search":
                    await self.panel.search(interaction, values["query"])
                else:
                    candidate = copy.deepcopy(self.panel.session)
                    self.apply(candidate, values)
                    await self.panel.commit(interaction, candidate, undo=True)
            except (ValueError, WikiError, TimeoutError) as exc:
                await private_message(interaction, str(exc))
                log.debug("exo: modal_rejected kind=%s reason=%s", self.kind, exc)

    def apply(self, session: Session, values: dict[str, str]) -> None:
        if self.kind == "goals":
            set_goals(session, values["goals"])
            session.notice = (
                "Objectifs enregistrés sans changer les jets ni les compteurs. "
                "L'objet est terminé seulement lorsque tous ces minimums sont atteints."
            )
        elif self.kind == "jets":
            jets = parse_jets(values["jets"])
            if not jets:
                raise ValueError("Indiquez au moins une ligne ; les lignes omises seront à zéro.")
            state = State(
                {**{key: 0 for key in session.item.bounds}, **jets},
                None if values["sink"] == "?" else decimal_value(values["sink"]),
            )
            state.validate()
            if session.mode == "simulation":
                validate_item_jets(session.item, state.jets)
            seed = whole(values["seed"], 0, 2**64 - 1, "Graine")
            set_goals(session, values["goal"])
            changed = session.state.profile != PROFILE or state.sink != session.state.sink or any(
                state.jets.get(stat, 0) != session.state.jets.get(stat, 0) for stat in STATS
            )
            if changed:
                if session.mode == "simulation":
                    session.sim = state
                else:
                    session.observed = state
            if session.mode == "observation":
                session.observation_ready = True
            if changed:
                session.last_changes = {}
                session.journal_page = 0
            session.seed = seed
            session.notice = (
                "Jet ou puits modifié. Compteurs et historique du mode courant remis à zéro ; "
                "l'autre mode est inchangé."
                if changed else
                "Paramètres enregistrés. Jet, puits, compteurs et historique conservés."
            )
            if session.mode == "observation":
                try:
                    validate_item_jets(session.item, state.jets)
                except ValueError:
                    session.notice += " Jet hors profil local, conservé uniquement comme déclaration."
            log.debug("exo: jet_form_applied mode=%s state_reset=%s", session.mode, changed)
        elif self.kind == "rates":
            if self.rune_key != session.rune_key:
                raise ValueError("La rune sélectionnée a changé.")
            if not values["sc"] and not values["sn"]:
                session.custom.pop(session.rune_key, None)
            elif not values["sc"] or not values["sn"]:
                raise ValueError("Saisissez SC et SN ensemble, ou laissez les deux vides.")
            else:
                session.custom[session.rune_key] = Rates(percentage(values["sc"]), percentage(values["sn"]))
            session.prices[session.rune_key] = whole(values["price"], 0, 10**12, "Prix")
            session.notice = (
                "Prix et taux enregistrés pour cette rune uniquement. Champs vides : modèle automatique "
                "estimatif. Le preset exo PA/PM/PO reste à 1 % ; les taux forcés ne le remplacent pas."
            )
        elif self.kind == "observe":
            if session.mode != "observation" or not session.observation_ready:
                raise ValueError("Passez en suivi et déclarez d'abord le jet réel.")
            losses = parse_jets(values["losses"])
            if any(value < 0 for value in losses.values()):
                raise ValueError("Les pertes sont des quantités positives.")
            result = observe(
                session.item, session.observed, session.rune, values["outcome"], losses, session.price,
            )
            session.last_changes = result["changes"]
            session.journal_page = 0
            session.notice = self.panel.result_text(result, "Observation enregistrée")
        elif self.kind == "math":
            session.p = percentage(values["p"])
            session.cap = whole(values["cap"], 0, 1000000, "Plafond")
            session.experiments = whole(values["experiments"], 1, 50000, "Campagnes")
            session.confidence = percentage(values["confidence"])
            session.seed = whole(values["seed"], 0, 2**64 - 1, "Graine")
            session.notice = "Calculs mis à jour. Ces paramètres ne changent pas les taux de l'atelier."
        elif self.kind == "budget":
            session.budget = Budget(**{
                key: whole(value, 0, 10**12, key) for key, value in values.items()
            })
            session.notice = "Budget de campagne mis à jour. Le prix de chaque rune d'atelier est réglé séparément."

    async def on_timeout(self):
        self.panel.modals.discard(self)

    async def on_error(self, interaction, error):
        log.exception("exo: modal_error kind=%s", self.kind, exc_info=error)
        await private_message(interaction, "Le formulaire n'a pas pu être appliqué. Le dernier état validé a été conservé.")


class ExoView(discord.ui.View):
    def __init__(self, cog, owner_id: int, guild_id: int, session: Session, image=None,
                 *, search_objective: str | None = None):
        super().__init__(timeout=600)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id
        self.session, self.image = session, image
        self.search_objective = search_objective
        self.lock = asyncio.Lock()
        self.message = None
        self.published_image = None
        self.modals = set()
        self.undo_session = None
        self.retired = False
        self.evo_requests: set[str] = set()
        self.evo_uncertain = False
        self.page = 0
        self.search_entries = []
        self.search_page = 0
        self.search_label = ""
        self.created = time.monotonic()
        self.hard_timeout = None
        self.rebuild()

    @property
    def key(self):
        return self.guild_id, self.owner_id

    def stop(self):
        self.retired = True
        self.cog.history_budget.release(id(self))
        for key, grant in list(self.cog.evo_shares.items()):
            if grant.view is self:
                self.cog.evo_shares.pop(key, None)
        for modal in list(self.modals):
            modal.stop()
        self.modals.clear()
        if self.hard_timeout is not None and self.hard_timeout is not asyncio.current_task():
            self.hard_timeout.cancel()
        if self.cog.views.get(self.key) is self:
            self.cog.views.pop(self.key, None)
        super().stop()

    async def hard_expire(self):
        await asyncio.sleep(840)
        await self.on_timeout()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await private_message(interaction, "Cet atelier est personnel. Lancez /exo pour ouvrir le vôtre.")
            return False
        return await self.ensure_active(interaction)

    async def ensure_active(self, interaction):
        try:
            self.require_active()
        except WikiError as exc:
            await private_message(interaction, str(exc))
            return False
        return True

    def require_active(self):
        if not self.active:
            log.debug("exo: inactive_panel owner=%s", self.owner_id)
            raise WikiError("Atelier expiré. Ouvrez /exo ; un export JSON permet de reprendre.")

    @property
    def active(self):
        return not (self.retired or self.is_finished() or self.cog.closed
                    or time.monotonic() - self.created >= 840)

    async def finish(self, reason: str, interaction=None):
        self.stop()
        for child in self.children:
            child.disabled = True
        editor = interaction.edit_original_response if interaction is not None else (
            self.message.edit if self.message is not None else None
        )
        if editor is None:
            return
        embed = discord.Embed(
            title=reason,
            description=(
                "La sauvegarde JSON jointe permet de reprendre avec **/exo reprise:**. "
                "Les jets, les objectifs, les prix et les deux journaux sont conservés."
            ),
            color=0x607D8B,
        )
        stream = None
        file = None
        try:
            stream = BytesIO(export_session(self.session))
            file = discord.File(stream, filename="exo-retro-session.json")
            await editor(
                embed=embed, attachments=[file], view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            log.debug("exo: archived owner=%s reason=%s", self.owner_id, reason)
        except (ValueError, discord.HTTPException):
            log.debug("exo: archive_unavailable owner=%s", self.owner_id, exc_info=True)
            embed.description = (
                "Atelier fermé. La sauvegarde n'a pas pu être jointe à ce message. "
                "Seuls les exports déjà récupérés permettent une reprise."
            )
            try:
                await editor(embed=embed, view=self)
            except discord.HTTPException:
                log.debug("exo: closed_message_unavailable", exc_info=True)
        finally:
            if file is not None:
                file.close()
            if stream is not None:
                stream.close()

    async def on_timeout(self, reason="Atelier expiré · sauvegarde de session"):
        async with self.lock:
            if not self.retired:
                await self.finish(reason)

    def add_item(self, item):
        if isinstance(item, (ActionButton, ChoiceSelect)):
            item.revision = self.session.revision
            item.custom_id = f"exo:{item.action}:{item.revision}"
        return super().add_item(item)

    def add_button(self, label, action, row=3, style=discord.ButtonStyle.secondary, disabled=False):
        self.add_item(ActionButton(label, action, row, style, disabled))

    def stat_keys(self):
        s = self.session
        return list(dict.fromkeys([*s.item.bounds, s.goal_stat, *s.state.jets, *STATS]))

    def rebuild(self):
        self._rebuild()
        if not self.active:
            for child in self.children:
                child.disabled = True

    def _rebuild(self):
        self.clear_items()
        s = self.session
        self.add_item(ChoiceSelect(
            "tab", [discord.SelectOption(label=label, value=key, emoji=emoji, default=key == s.tab)
                    for key, label, emoji in TABS],
            "Atelier, historique, réglages ou aide", 0,
        ))
        if self.search_entries:
            page = self.search_entries[self.search_page * 25:(self.search_page + 1) * 25]
            self.add_item(ChoiceSelect(
                "item", [discord.SelectOption(label=truncate_text(entry.label, 100), value=str(index))
                         for index, entry in enumerate(page, self.search_page * 25)],
                "Choisir un objet", 1,
            ))
            self.add_button("Précédent", "previous", disabled=self.search_page == 0)
            self.add_button("Suivant", "next", disabled=(self.search_page + 1) * 25 >= len(self.search_entries))
            self.add_button("Retour atelier", "cancel_search")
            self.add_button("Exporter l'atelier", "export", 4)
            return
        if s.tab == "maths":
            self.add_button("Paramètres / taux", "math", style=discord.ButtonStyle.primary)
            self.add_button("Prix / budget", "budget")
            self.add_button("Simuler les campagnes", "campaign", style=discord.ButtonStyle.success)
        elif s.tab == "settings":
            if s.mode == "simulation":
                self.add_item(ChoiceSelect(
                    "preset", [
                        discord.SelectOption(label="Nouveau jet aléatoire", value="random"),
                        discord.SelectOption(label="Nouveau jet minimum", value="minimum"),
                        discord.SelectOption(label="Nouveau jet parfait", value="perfect"),
                    ], "Recommencer la simulation (annulable)", 1,
                ))
            self.add_button("Taux / prix rune", "rates")
            self.add_button("Modifier le jet", "jets")
            self.add_button("Objectifs", "goals")
            self.add_button("Risque estimatif", "risk", disabled=s.mode != "simulation")
            self.add_button("Simulation ↔ suivi", "mode")
        elif s.tab == "journal":
            self.add_button(
                "← Plus ancien", "older",
                disabled=s.journal_page >= max(0, len(s.state.journal) - 1),
            )
            self.add_button("Plus récent →", "newer", disabled=s.journal_page == 0)
            self.add_button("Dernier essai", "latest", disabled=s.journal_page == 0)
            self.add_button("Simulation ↔ suivi", "mode")
        elif s.tab == "aide":
            self.add_button("Retour atelier", "workshop", style=discord.ButtonStyle.primary)
            self.add_button("Simulation ↔ suivi", "mode")
        else:
            keys = self.stat_keys()
            chunk = keys[self.page * 24:(self.page + 1) * 24]
            options = []
            for key in chunk:
                current = s.state.jets.get(key, 0)
                native = key in s.item.bounds
                label = f"{STATS[key].name} · {current}"
                description = (
                    f"Naturel : {s.item.bounds[key][0]} à {s.item.maximum(key)}"
                    if native else "Caractéristique exotique, absente de l'objet naturel"
                )
                options.append(discord.SelectOption(
                    label=label, value=key, description=description, default=key == s.rune.stat,
                ))
            options.append(discord.SelectOption(label="Autres caractéristiques →", value="__page"))
            self.add_item(ChoiceSelect(
                "stat", options, f"Travailler : {STATS[s.rune.stat].name}", 1,
            ))
            recommended = recommended_rune(s.item, s.state, s.rune.stat, s.rune_target)
            self.add_item(ChoiceSelect(
                "rune", [
                    discord.SelectOption(
                        label=f"{Rune(s.rune.stat, tier).name} · +{gain} · poids {Rune(s.rune.stat, tier).weight}",
                        description="Taille conseillée pour ce jet" if tier == recommended.tier else "Autre taille",
                        value=str(tier), default=tier == s.rune.tier,
                    )
                    for tier, gain in enumerate(STATS[s.rune.stat].gains)
                ],
                "Choisir la rune à poser", 2,
            ))
            active = s.mode == "simulation"
            blocked = bool(simulation_blocker(s.item, s.state, s.rune, s.rates)) if active else True
            unsafe_batch = s.state.jets.get(s.rune.stat, 0) + s.rune.gain > s.rune_target
            if active:
                self.add_button("Poser ×1", "one", style=discord.ButtonStyle.success, disabled=blocked)
                self.add_button("×10 sans dépasser", "ten", disabled=blocked or unsafe_batch)
                self.add_button("×100 sans dépasser", "hundred", disabled=blocked or unsafe_batch)
                self.add_button("Rune conseillée", "recommend")
            else:
                self.add_button(
                    "Noter un résultat", "observe", style=discord.ButtonStyle.success,
                    disabled=not s.observation_ready,
                )
                self.add_button("Passer en simulation", "mode")
            self.add_button("Objectifs", "goals")
            self.add_button("Modifier le jet", "jets", 4)
            self.add_button("Réglages", "settings", 4)
        self.add_button("Annuler", "undo", 4, disabled=self.undo_session is None)
        self.add_button("Exporter", "export", 4)
        self.add_button("Fermer et sauvegarder", "close", 4)

    def embed(self):
        if self.search_entries:
            return discord.Embed(
                title="Choisir un objet mageable",
                description=(
                    f"{display_text(self.search_label, 300)}\n"
                    f"{len(self.search_entries)} résultats · page {self.search_page + 1}/"
                    f"{(len(self.search_entries) + 24) // 25}\n"
                    "L'objet choisi ouvrira un atelier neuf ; exportez l'ancien avant de le remplacer."
                ),
                color=0x306E83,
            )
        return build_embed(self.session)

    async def publish(self, interaction=None, *, initial=False):
        """Actualise le panneau existant ; Evo utilise uniquement son message ephemere."""
        self.require_active()
        if interaction is None:
            if not getattr(getattr(self.message, "flags", None), "ephemeral", False):
                raise WikiError("Le panneau privé de l'atelier n'est plus disponible.")
            editor = self.message.edit
        else:
            editor = interaction.edit_original_response
        if self.session.tab == "maths" and not self.search_entries:
            snapshot = copy.deepcopy(self.session)
            async with self.cog.compute_slots:
                self.require_active()
                log.debug("exo: probability_render_started owner=%s", self.owner_id)
                worker = asyncio.create_task(asyncio.to_thread(build_embed, snapshot))
                try:
                    embed = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await asyncio.gather(worker, return_exceptions=True)
                    raise
            log.debug("exo: probability_render_finished owner=%s", self.owner_id)
        else:
            embed = self.embed()
        self.require_active()
        image = None if self.search_entries else self.image
        kwargs = {"embed": embed, "view": self, "allowed_mentions": discord.AllowedMentions.none()}
        retained = [
            attachment for attachment in getattr(self.message, "attachments", ())
            if attachment.filename == "exo-objet.png"
        ]
        if image is not None and self.published_image is image and retained:
            embed.set_thumbnail(url="attachment://exo-objet.png")
            kwargs["attachments"] = retained
            message = await editor(**kwargs)
        elif image is not None:
            stream = BytesIO(image.data)
            file = discord.File(stream, filename="exo-objet.png")
            embed.set_thumbnail(url="attachment://exo-objet.png")
            kwargs["attachments"] = [file]
            try:
                message = await editor(**kwargs)
            except discord.HTTPException:
                self.require_active()
                embed.set_thumbnail(url=image.source_url)
                kwargs["attachments"] = []
                message = await editor(**kwargs)
            finally:
                file.close()
                stream.close()
        else:
            kwargs["attachments"] = []
            message = await editor(**kwargs)
        if interaction is None and message is None:
            raise WikiError("La mise à jour du panneau privé n'a pas été confirmée.")
        if message is not None:
            self.message = message
            self.published_image = image
        return message

    async def commit(self, interaction, candidate: Session, *, undo=False):
        if not await self.ensure_active(interaction):
            return
        await self._commit(interaction, candidate, undo=undo)

    async def commit_private(self, candidate: Session, *, revision: int):
        """Valide une rune Evo sous le verrou de la vue et confirme le panneau prive."""
        self.require_active()
        if not self.lock.locked() or self.session.revision != revision:
            raise WikiError("Le panneau a changé. Partagez à nouveau son état actuel avec Evo.")
        try:
            await self._commit(None, candidate, undo=True)
        except (Exception, asyncio.CancelledError):
            self.evo_uncertain = True
            raise

    async def _commit(self, interaction, candidate: Session, *, undo=False):
        previous, old_undo = self.session, self.undo_session
        old_size = history_size(previous) + (history_size(old_undo) if old_undo else 0)
        next_undo = previous if undo else old_undo
        self.cog.history_budget.reserve(
            id(self), max(old_size, history_size(candidate) + (history_size(next_undo) if next_undo else 0)),
        )
        candidate.revision = previous.revision + 1
        self.session = candidate
        try:
            if undo:
                self.undo_session = copy.deepcopy(previous)
            self.rebuild()
            await self.publish(interaction)
        except (Exception, asyncio.CancelledError):
            self.session, self.undo_session = previous, old_undo
            if not self.retired:
                self.cog.history_budget.reserve(id(self), old_size)
            self.rebuild()
            raise
        if not self.retired:
            self.cog.history_budget.reserve(
                id(self), history_size(self.session) + (history_size(self.undo_session) if self.undo_session else 0),
            )
        self.evo_uncertain = False
        log.debug("exo: committed owner=%s revision=%s mode=%s", self.owner_id, candidate.revision, candidate.mode)

    @staticmethod
    def result_text(row, prefix="Tentative"):
        return prefix + "\n" + "\n".join(result_lines(row, detailed=False))

    async def send_export(self, interaction, caption="Sauvegarde personnelle : /exo reprise:<ce fichier>."):
        stream = BytesIO(export_session(self.session))
        file = discord.File(stream, filename="exo-retro-session.json")
        try:
            await interaction.followup.send(
                caption, file=file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            file.close()
            stream.close()
        log.debug("exo: exported owner=%s", self.owner_id)

    async def check_revision(self, interaction, revision):
        if revision is not None and revision != self.session.revision:
            await private_message(
                interaction,
                "Le panneau a été actualisé. Aucun essai supplémentaire n'a été lancé ; "
                "utilisez les boutons du dernier état affiché.",
            )
            return False
        return True

    async def search(self, interaction, query):
        if not query.strip():
            raise ValueError("Saisissez un nom d'objet.")
        entries = await self.cog.entries()
        found, fuzzy = find_entries(entries, query, limit=200)
        if not found:
            raise ValueError("Aucun équipement mageable trouvé. Essayez un autre nom ou /exo sans objet.")
        old = (self.search_entries, self.search_page, self.search_label)
        self.search_entries, self.search_page = found, 0
        self.search_label = ("Suggestions approchantes — à vérifier" if fuzzy else "Résultats") + f" pour « {query} »"
        self.session.revision += 1
        try:
            self.rebuild()
            await self.publish(interaction)
        except (Exception, asyncio.CancelledError):
            self.search_entries, self.search_page, self.search_label = old
            self.session.revision -= 1
            self.rebuild()
            raise

    async def dispatch(self, interaction, action, value=None, *, revision=None):
        if not await self.interaction_check(interaction):
            return
        modal_action = "search" if action == "tab" and value == "search" else action
        if modal_action in {"jets", "goals", "rates", "observe", "math", "budget", "search"}:
            if self.lock.locked():
                await self.report_busy(interaction, modal_action)
                return
            try:
                async with asyncio.timeout(MODAL_LOCK_TIMEOUT):
                    await self.lock.acquire()
            except TimeoutError:
                await self.report_busy(interaction, modal_action)
                return
            try:
                if not await self.ensure_active(interaction) or not await self.check_revision(interaction, revision):
                    return
                for previous_modal in list(self.modals):
                    previous_modal.stop()
                self.modals.clear()
                modal = ExoModal(self, modal_action)
                try:
                    await interaction.response.send_modal(modal)
                except Exception:
                    self.modals.discard(modal)
                    modal.stop()
                    raise
            finally:
                self.lock.release()
            return
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction) or not await self.check_revision(interaction, revision):
                return
            try:
                await self.handle(interaction, action, value)
            except (ValueError, WikiError, TimeoutError) as exc:
                log.debug("exo: action_rejected action=%s reason=%s", action, exc)
                await private_message(interaction, str(exc))

    async def report_busy(self, interaction, action):
        log.debug("exo: modal_busy action=%s owner=%s", action, self.owner_id)
        await private_message(interaction, "Une action est en cours. Réessaie dans un instant.")

    async def handle(self, interaction, action, value):
        s = copy.deepcopy(self.session)
        if action == "export":
            await self.send_export(interaction)
            return
        if action == "close":
            await self.finish("Atelier fermé · sauvegarde de session", interaction)
            return
        if action == "item":
            index = whole(value, 0, max(0, len(self.search_entries) - 1), "Objet")
            item, image = await self.cog.load_item(self.search_entries[index])
            objective = self.search_objective
            if objective is None and self.session.goal_stat not in item.bounds:
                objective = self.session.goal_stat
            candidate = Session.create(item, objective)
            if s.sim.attempts or s.observed.attempts or s.observation_ready or s.sim.jets != State.initial(s.item).jets:
                await self.send_export(interaction, "Ancien atelier sauvegardé avant le changement d'objet.")
            old_image, old_entries = self.image, self.search_entries
            old_page = self.page
            self.image, self.search_entries, self.page = image, [], 0
            previous_undo = self.undo_session
            self.undo_session = None
            try:
                await self.commit(interaction, candidate)
                self.search_objective = None
            except (Exception, asyncio.CancelledError):
                self.image, self.search_entries, self.page = old_image, old_entries, old_page
                self.undo_session = previous_undo
                self.rebuild()
                raise
            return
        if action in {"previous", "next", "cancel_search"}:
            old_page, old_entries = self.search_page, self.search_entries
            if action == "cancel_search":
                self.search_entries = []
            else:
                self.search_page = max(0, min(
                    (len(self.search_entries) - 1) // 25,
                    self.search_page + (1 if action == "next" else -1),
                ))
            try:
                await self.commit(interaction, s)
                if action == "cancel_search":
                    self.search_objective = None
            except (Exception, asyncio.CancelledError):
                self.search_page, self.search_entries = old_page, old_entries
                self.rebuild()
                raise
            return
        if action == "tab":
            if value not in {key for key, _, _ in TABS if key != "search"}:
                raise ValueError("Onglet inconnu.")
            old_entries = self.search_entries
            self.search_entries = []
            s.tab, s.notice = value, ""
            try:
                await self.commit(interaction, s)
                self.search_objective = None
            except (Exception, asyncio.CancelledError):
                self.search_entries = old_entries
                self.rebuild()
                raise
            return
        if action == "stat":
            if value == "__page":
                old_page = self.page
                self.page = (self.page + 1) % ((len(self.stat_keys()) + 23) // 24)
                try:
                    await self.commit(interaction, s)
                except (Exception, asyncio.CancelledError):
                    self.page = old_page
                    self.rebuild()
                    raise
                return
            s.rune = Rune(stat_key(value))
            s.rune = recommended_rune(s.item, s.state, s.rune.stat, s.rune_target)
            s.notice = "Rune proposée selon le jet ; vous pouvez choisir une autre taille."
        elif action == "recommend":
            s.rune = recommended_rune(s.item, s.state, s.rune.stat, s.rune_target)
            s.notice = "Taille conseillée sélectionnée. Ce conseil ne garantit pas le rendement en jeu."
        elif action in {"settings", "workshop"}:
            s.tab = "settings" if action == "settings" else "atelier"
            s.notice = ""
        elif action in {"older", "newer", "latest"}:
            offset = 1 if action == "older" else -1
            s.journal_page = 0 if action == "latest" else max(
                0, min(max(0, len(s.state.journal) - 1), s.journal_page + offset),
            )
            s.notice = ""
        elif action == "preset":
            reset_simulation(s, value, secrets.randbits(64))
            s.tab = "atelier"
        elif action == "rune":
            s.rune = Rune(s.rune.stat, whole(value, 0, 2, "Taille"))
            s.notice = ""
        elif action == "mode":
            s.mode = "observation" if s.mode == "simulation" else "simulation"
            s.journal_page = 0
            s.last_changes = {}
            s.notice = "Les jets, tentatives et dépenses de chaque mode sont conservés séparément."
        elif action == "undo":
            if self.undo_session is None:
                raise ValueError("Aucune action à annuler.")
            previous_undo = self.undo_session
            self.undo_session = None
            s = copy.deepcopy(previous_undo)
            s.notice = "Dernière action annulée, y compris sa séquence aléatoire. Une seule étape d'annulation."
            try:
                await self.commit(interaction, s)
            except (Exception, asyncio.CancelledError):
                self.undo_session = previous_undo
                self.rebuild()
                raise
            return
        elif action in {"one", "ten", "hundred"}:
            result = simulate_batch(s, {"one": 1, "ten": 10, "hundred": 100}[action])
            s.notice = batch_text(result)
            log.debug(
                "exo: batch owner=%s attempts=%s stop=%s",
                self.owner_id, len(result.rows), result.stop_reason,
            )
        elif action == "risk":
            if s.mode != "simulation":
                raise ValueError("Risque simulé indisponible dans le suivi d'observations.")
            async with self.cog.compute_slots:
                result = await asyncio.to_thread(risk, s.item, s.sim, s.rune, s.rates, s.seed ^ 0xEAC0)
            if not await self.ensure_active(interaction):
                return
            lines = [
                f"{STATS[key].name} : perte dans **{percent(p)}** des tirages"
                for key, p in sorted(result["loss_probability"].items(), key=lambda pair: -pair[1]) if p
            ]
            s.notice = (
                f"**RISQUE DU MODÈLE SEULEMENT** · {result['samples']} tirages depuis le même jet.\n"
                + ("\n".join(lines[:9]) or "Aucune perte dans cet échantillon.")
                + f"\nPoids moyen perdu : {number(float(result['mean_loss_weight']))}."
                + f" Situations non compensées : {result['unexplained']}.\n"
                "Ce ne sont pas les probabilités de perte du serveur ; le jet n'a pas été modifié."
            )
        elif action == "campaign":
            async with self.cog.compute_slots:
                result = await asyncio.to_thread(
                    run_campaigns, s.p, s.cap, s.experiments, s.budget, s.seed,
                )
            if not await self.ensure_active(interaction):
                return
            lo, hi = result.success_interval
            s.notice = (
                f"**{number(result.experiments)} campagnes** · graine {result.seed}\n"
                f"Avec réussite : **{percent(result.successes / result.experiments)}** "
                f"(théorie {percent(success_within(s.p, s.cap))}).\n"
                f"Intervalle Wilson 95 % : {percent(lo)} – {percent(hi)}.\n"
                f"Essais consommés : moyenne {number(result.mean_used)}, "
                f"médiane {result.used_p50}, P95 {result.used_p95}.\n"
                f"Coût moyen : **{number(result.mean_cost)} kamas**.\n"
                "Les échecs au plafond sont inclus : ce n'est pas la distribution du délai de réussite sans plafond."
            )
        else:
            raise ValueError("Action inconnue.")
        await self.commit(
            interaction, s,
            undo=action in {"one", "ten", "hundred", "preset"},
        )

    async def on_error(self, interaction, error, item):
        log.exception("exo: view_error owner=%s", self.owner_id, exc_info=error)
        await private_message(interaction, "L'action n'a pas pu être validée. Réessayez depuis le dernier état affiché.")


class ExoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.views: dict[tuple[int, int], ExoView] = {}
        self.evo_shares: dict[tuple[int, int, int], object] = {}
        self.open_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.compute_slots = asyncio.Semaphore(2)
        self.history_budget = HistoryBudget()
        self.closed = False

    def wiki(self):
        cog = self.bot.get_cog("DofusWikiCog")
        if cog is None or getattr(cog, "_closed", False):
            raise WikiError("Le catalogue est momentanément indisponible ; /exo sans objet ouvre la démo.")
        return cog

    async def entries(self):
        async with asyncio.timeout(20):
            entries = await self.wiki().client.items()
        return tuple(entry for entry in entries if is_mageable(entry))

    async def load_item(self, entry):
        wiki = self.wiki()
        async with asyncio.timeout(20):
            detail = await wiki.client.detail(entry)
        try:
            async with asyncio.timeout(8):
                enrichment = await wiki.enrichment_client.enrich(detail) if wiki.enrichment_client else None
        except (TimeoutError, WikiError, XixouError):
            enrichment = None
            log.debug("exo: enrichment_fallback item=%s", entry.token, exc_info=True)
        item = from_detail(detail, enrichment)
        try:
            async with asyncio.timeout(5):
                image = await wiki.resolve_item_image(detail, enrichment)
        except (TimeoutError, WikiError, XixouError):
            image = None
            log.debug("exo: image_unavailable item=%s", entry.token, exc_info=True)
        return item, image

    async def cog_unload(self):
        self.closed = True
        self.evo_shares.clear()
        views = list(self.views.values())
        for view in views:
            await view.on_timeout("Module rechargé · sauvegarde de session")
        self.open_locks.clear()
        log.debug("exo: unloaded count=%s", len(views))

    async def autocomplete(self, interaction, current: str):
        try:
            wiki = self.wiki()
            cached = wiki.client.peek(INDEX_PATHS[0])
            if not cached:
                wiki.start_warmup()
                return []
            entries = tuple(entry for entry in cached if is_mageable(entry))
            found, _ = find_entries(entries, current, limit=25)
            return [app_commands.Choice(name=truncate_text(entry.label, 100), value=entry.token) for entry in found]
        except (WikiError, ValueError):
            log.debug("exo: autocomplete_unavailable", exc_info=True)
            return []

    @app_commands.command(name="exo", description="Atelier FM Rétro : posez des runes, suivez les jets, le puits et vos objectifs.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.checks.cooldown(2, 15, key=lambda interaction: (interaction.guild_id, interaction.user.id))
    @app_commands.describe(
        objet="Objet mageable, sinon démonstration Gelano sans réseau.",
        objectif="Exo visé, sinon choix automatique d'un bonus absent de l'objet.",
        reprise="Reprendre un export JSON personnel /exo (8 Mio maximum).",
    )
    @app_commands.choices(objectif=[
        app_commands.Choice(name="Exo PM", value="pm"),
        app_commands.Choice(name="Exo PA", value="pa"),
        app_commands.Choice(name="Exo Portée", value="po"),
    ])
    async def exo(
        self, interaction: discord.Interaction, objet: str | None = None,
        objectif: str | None = None, reprise: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = (interaction.guild_id, interaction.user.id)
        if key not in self.views and len(self.views) + len(self.open_locks) >= MAX_SESSIONS:
            await interaction.edit_original_response(content="Tous les ateliers sont occupés. Réessayez après l'expiration d'une session.")
            return
        if key in self.open_locks:
            await interaction.edit_original_response(content="Une ouverture d'atelier est déjà en cours pour vous.")
            return
        self.open_locks[key] = asyncio.Lock()
        new_view = None
        try:
            if reprise is not None and objet is not None:
                raise ValueError("Choisissez un objet OU un fichier de reprise, pas les deux.")
            entries, label, image = [], "", None
            if reprise is not None:
                if reprise.size > EXPORT_LIMIT or not reprise.filename.lower().endswith(".json"):
                    raise ValueError("La reprise attend un fichier .json de 8 Mio maximum.")
                async with asyncio.timeout(10):
                    raw = await reprise.read()
                session = import_session(raw)
            else:
                # L'objectif explicite concerne l'objet recherche, pas le Gelano provisoire.
                session = Session.create(demo_item(), objectif if not objet else None)
                session.notice = (
                    "Prêt à jouer : Gelano théorique PA=1, puits 0. Choisissez une rune puis « Poser ×1 ». "
                    "L'objet est terminé seulement lorsque tous les seuils affichés sont atteints. "
                    "« Réglages » propose un nouveau jet ; le menu permet de changer d'objet."
                )
                if objet:
                    entries_all = await self.entries()
                    found, fuzzy = find_entries(entries_all, objet, limit=200)
                    if not found:
                        raise ValueError("Aucun objet mageable trouvé ; /exo sans objet ouvre l'atelier de démonstration.")
                    exact = not fuzzy and len(found) == 1
                    if exact:
                        item, image = await self.load_item(found[0])
                        session = Session.create(item, objectif)
                        session.notice = (
                            "Prêt à simuler : jet au maximum naturel, puits 0. Posez une rune ou ouvrez "
                            "« Réglages » pour un jet aléatoire. « Objectifs » règle les minimums à conserver."
                        )
                    else:
                        entries, label = found, ("Suggestions approchantes" if fuzzy else "Résultats") + f" pour « {objet} »"
            if self.closed:
                raise ValueError("Module en cours de rechargement. Réessayez /exo.")
            new_view = ExoView(
                self, interaction.user.id, interaction.guild_id, session, image,
                search_objective=objectif if entries else None,
            )
            new_view.search_entries, new_view.search_label = entries, label
            self.history_budget.reserve(id(new_view), history_size(session))
            new_view.rebuild()
            await new_view.publish(interaction, initial=True)
            if self.closed:
                await new_view.on_timeout()
                raise ValueError("Module rechargé pendant l'ouverture. Réessayez /exo.")
            old_view = self.views.get(key)
            if old_view:
                await old_view.on_timeout("Atelier remplacé · sauvegarde de session")
            self.views[key] = new_view
            new_view.hard_timeout = asyncio.create_task(new_view.hard_expire())
            log.debug("exo: opened owner=%s item=%s imported=%s", interaction.user.id, session.item.token, reprise is not None)
        except (ValueError, WikiError, TimeoutError) as exc:
            if new_view is not None:
                new_view.stop()
            message = str(exc) or "Le catalogue n'a pas répondu à temps. /exo sans objet reste utilisable."
            await interaction.edit_original_response(
                content=message, allowed_mentions=discord.AllowedMentions.none(),
            )
            log.debug("exo: opening_rejected reason=%s", message)
        except asyncio.CancelledError:
            if new_view is not None:
                new_view.stop()
            raise
        except Exception:
            if new_view is not None:
                new_view.stop()
            log.exception("exo: opening_failed owner=%s", interaction.user.id)
            await interaction.edit_original_response(
                content="L'atelier n'a pas pu être ouvert. Le problème a été journalisé.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            self.open_locks.pop(key, None)

    async def open_build_session(self, interaction, reference, jets, sink, *, guild_id=None):
        """J'ouvre les jets confirmés du builder sans détruire un atelier en cas d'échec."""
        key = (guild_id or interaction.guild_id, interaction.user.id)
        if self.closed or key in self.open_locks:
            raise ValueError("Une ouverture est en cours ou le module se recharge.")
        if key not in self.views and len(self.views) + len(self.open_locks) >= MAX_SESSIONS:
            raise ValueError("Tous les ateliers sont occupés.")
        declared_sink = decimal_value(str(sink), "1000000")
        self.open_locks[key] = asyncio.Lock()
        new_view = None
        committed = False
        try:
            entries = await self.entries()
            found = [entry for entry in entries if entry.token == reference]
            if len(found) != 1:
                raise ValueError("Objet mageable introuvable ou ambigu dans le catalogue /exo.")
            item, image = await self.load_item(found[0])
            validate_item_jets(item, jets)
            session = Session.create(item)
            session.sim = State(dict(jets), declared_sink)
            session.observed = State(dict(jets), None)
            session.notice = "Jets copiés du builder. Puits déclaré séparément ; aucune déduction des jets."
            if self.closed:
                raise ValueError("Module rechargé pendant l'ouverture.")
            new_view = ExoView(self, interaction.user.id, key[0], session, image)
            self.history_budget.reserve(id(new_view), history_size(session))
            new_view.rebuild()
            await new_view.publish(interaction, initial=True)
            if self.closed:
                raise ValueError("Module rechargé pendant l'ouverture.")
            old_view = self.views.get(key)
            self.views[key] = new_view
            new_view.hard_timeout = asyncio.create_task(new_view.hard_expire())
            committed = True
            if old_view:
                try:
                    await old_view.on_timeout("Atelier remplacé depuis Build · sauvegarde de session")
                except asyncio.CancelledError:
                    old_view.stop()
                    raise
                except Exception:
                    old_view.stop()
                    log.debug("exo: build_previous_archive_failed owner=%s", key[1], exc_info=True)
            log.debug("exo: build_session_opened owner=%s item=%s", key[1], reference)
            return new_view
        except BaseException:
            if new_view is not None and not committed:
                new_view.stop()
            log.debug("exo: build_session_interrupted owner=%s item=%s committed=%s", key[1], reference, committed)
            raise
        finally:
            self.open_locks.pop(key, None)

    @exo.autocomplete("objet")
    async def exo_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.autocomplete(interaction, current)

    @exo.error
    async def exo_error(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown):
            await private_message(interaction, "Deux ouvertures maximum par 15 secondes. Utilisez le panneau déjà ouvert.")
        else:
            log.error("exo: command_error", exc_info=error)
            await private_message(interaction, "Impossible d'ouvrir /exo. Le problème a été journalisé.")


async def setup(bot):
    await bot.add_cog(ExoCog(bot))
