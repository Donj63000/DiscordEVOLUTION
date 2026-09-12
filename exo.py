"""Commande native /exo : atelier personnel, suivi declaratif et probabilites explicites."""

from __future__ import annotations

import asyncio
import copy
from io import BytesIO
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.dofus_wiki import INDEX_PATHS, WikiError, find_entries
from utils.exo_data import demo_item, from_detail, is_mageable
from utils.exo_embeds import build_embed, number, percent
from utils.exo_engine import (
    D, Rates, Rune, STATS, State, attempt, decimal_value, observe,
    parse_jets, risk, stat_key,
)
from utils.exo_math import Budget, integer, run_campaigns, success_within
from utils.exo_session import EXPORT_LIMIT, Session, export_session, import_session
from utils.wiki_embeds import display_text, truncate_text
from utils.xixou_api import XixouError

log = logging.getLogger(__name__)
MAX_SESSIONS = 120
TABS = (
    ("atelier", "Atelier de forgemagie", "🔨"),
    ("maths", "Probabilités et budget", "📊"),
    ("journal", "Historique du mode courant", "📜"),
    ("aide", "Guide, limites et sources", "📖"),
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
        await self.view.dispatch(interaction, self.action)


class ChoiceSelect(discord.ui.Select):
    def __init__(self, action: str, options: list[discord.SelectOption], placeholder: str, row: int):
        super().__init__(
            custom_id=f"exo:{action}", options=options, placeholder=placeholder, row=row,
        )
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.dispatch(interaction, self.action, self.values[0])


class ExoModal(discord.ui.Modal):
    """Les soumissions retardees sont refusees si le panneau a change entre-temps."""

    def __init__(self, view, kind: str):
        titles = {
            "jets": "Jet, objectif et puits nominal", "rates": "Hypothèses de cette rune",
            "observe": "Consigner un résultat en jeu", "math": "Calcul probabiliste",
            "budget": "Budget exo (kamas)", "search": "Rechercher un objet mageable",
        }
        super().__init__(title=titles[kind], timeout=300)
        self.panel, self.kind, self.revision = view, kind, view.session.revision
        self.inputs: dict[str, discord.ui.TextInput] = {}
        s = view.session
        self.rune_key = s.rune_key
        if kind == "jets":
            self.add_input(
                "jets", "Jet complet (clés du panneau, ex. pa=1)",
                "; ".join(f"{key}={value}" for key, value in s.state.jets.items()),
                paragraph=True, maximum=1800,
            )
            self.add_input("sink", "Puits nominal de départ (? = inconnu)", "?" if s.state.sink is None else str(s.state.sink), maximum=20)
            self.add_input("goal", "Objectif (exemple pm=1 ou vi=250)", f"{s.goal_stat}={s.goal_value}", maximum=80)
            self.add_input("seed", "Graine de simulation (entier)", str(s.seed), maximum=20)
        elif kind == "rates":
            self.add_input("sc", "SC % personnalisé (vide = non défini)", "" if s.rates is None else format(s.rates.sc * 100, ".2f"), required=False, maximum=8)
            self.add_input("sn", "SN % personnalisé (vide = non défini)", "" if s.rates is None else format(s.rates.sn * 100, ".2f"), required=False, maximum=8)
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
            except (ValueError, WikiError) as exc:
                await private_message(interaction, str(exc))
                log.debug("exo: modal_rejected kind=%s reason=%s", self.kind, exc)

    def apply(self, session: Session, values: dict[str, str]) -> None:
        if self.kind == "jets":
            jets = parse_jets(values["jets"])
            if not jets:
                raise ValueError("Indiquez au moins une ligne ; les lignes omises seront à zéro.")
            goal = parse_jets(values["goal"])
            if len(goal) != 1:
                raise ValueError("Indiquez exactement un objectif, par exemple pm=1.")
            key, value = next(iter(goal.items()))
            integer(value, 1, 10000, "Objectif")
            state = State(
                {**{key: 0 for key in session.item.bounds}, **jets},
                None if values["sink"] == "?" else decimal_value(values["sink"]),
            )
            state.validate()
            if session.mode == "simulation":
                session.sim = state
            else:
                session.observed = state
                session.observation_ready = True
            session.goal_stat, session.goal_value = key, value
            session.seed = whole(values["seed"], 0, 2**64 - 1, "Graine")
            session.notice = "Jet déclaré. Compteurs du mode courant remis à zéro ; l'autre mode est inchangé."
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
                "Hypothèses enregistrées pour cette rune uniquement. Le preset exo PA/PM/PO reste à 1 % ; "
                "pour comparer d'autres taux, utilisez l'onglet probabilités."
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
    def __init__(self, cog, owner_id: int, guild_id: int, session: Session, image=None):
        super().__init__(timeout=600)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id
        self.session, self.image = session, image
        self.lock = asyncio.Lock()
        self.message = None
        self.modals = set()
        self.undo_session = None
        self.retired = False
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
        if self.retired or self.cog.closed or time.monotonic() - self.created >= 840:
            await private_message(interaction, "Atelier expiré. Ouvrez /exo ; un export JSON permet de reprendre.")
            return False
        return True

    async def on_timeout(self):
        async with self.lock:
            self.stop()
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    log.debug("exo: timeout_message_unavailable", exc_info=True)

    def add_button(self, label, action, row=3, style=discord.ButtonStyle.secondary, disabled=False):
        self.add_item(ActionButton(label, action, row, style, disabled))

    def rebuild(self):
        self.clear_items()
        s = self.session
        self.add_item(ChoiceSelect(
            "tab", [discord.SelectOption(label=label, value=key, emoji=emoji, default=key == s.tab)
                    for key, label, emoji in TABS],
            "Choisir un écran", 0,
        ))
        if self.search_entries:
            page = self.search_entries[self.search_page * 25:(self.search_page + 1) * 25]
            self.add_item(ChoiceSelect(
                "item", [discord.SelectOption(label=truncate_text(entry.label, 100), value=str(index))
                         for index, entry in enumerate(page, self.search_page * 25)],
                "Choisir explicitement un objet", 1,
            ))
            self.add_button("Précédent", "previous", disabled=self.search_page == 0)
            self.add_button("Suivant", "next", disabled=(self.search_page + 1) * 25 >= len(self.search_entries))
            self.add_button("Retour atelier", "cancel_search")
            return
        if s.tab == "maths":
            self.add_button("Paramètres / taux", "math", style=discord.ButtonStyle.primary)
            self.add_button("Prix / budget", "budget")
            self.add_button("Simuler les campagnes", "campaign", style=discord.ButtonStyle.success)
            self.add_button("Exporter JSON", "export", 4)
            self.add_button("Annuler dernière action", "undo", 4, disabled=self.undo_session is None)
            self.add_button("Fermer", "close", 4)
            return
        if s.tab in {"aide", "journal"}:
            self.add_button("Exporter JSON", "export")
            self.add_button("Basculer simulation / suivi", "mode")
            self.add_button("Annuler dernière action", "undo", disabled=self.undo_session is None)
            self.add_button("Fermer", "close")
            return
        keys = list(STATS)
        chunk = keys[self.page * 24:(self.page + 1) * 24]
        options = [
            discord.SelectOption(label=STATS[key].name, value=key, default=key == s.rune.stat)
            for key in chunk
        ]
        options.append(discord.SelectOption(label="Autres caractéristiques →", value="__page"))
        self.add_item(ChoiceSelect("stat", options, f"Caractéristique : {STATS[s.rune.stat].name}", 1))
        self.add_item(ChoiceSelect(
            "rune", [
                discord.SelectOption(
                    label=f"{Rune(s.rune.stat, tier).name} · +{gain} · poids {Rune(s.rune.stat, tier).weight}",
                    value=str(tier), default=tier == s.rune.tier,
                )
                for tier, gain in enumerate(STATS[s.rune.stat].gains)
            ],
            "Taille de rune", 2,
        ))
        active = s.mode == "simulation"
        self.add_button("Passer ×1", "one", style=discord.ButtonStyle.success, disabled=not active)
        self.add_button("×10", "ten", disabled=not active)
        self.add_button("×100", "hundred", disabled=not active)
        self.add_button("Risque du modèle", "risk", disabled=not active)
        self.add_button("Jet / objectif", "jets")
        self.add_button("Taux / prix rune", "rates", 4)
        self.add_button("Noter un résultat", "observe", 4, disabled=active)
        self.add_button("Mode : simu ↔ suivi", "mode", 4)
        self.add_button("Annuler", "undo", 4, disabled=self.undo_session is None)
        self.add_button("Exporter", "export", 4)

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

    async def publish(self, interaction, *, initial=False):
        embed = self.embed()
        image = None if self.search_entries else self.image
        kwargs = {"embed": embed, "view": self, "allowed_mentions": discord.AllowedMentions.none()}
        if image is not None:
            stream = BytesIO(image.data)
            file = discord.File(stream, filename="exo-objet.png")
            embed.set_thumbnail(url="attachment://exo-objet.png")
            kwargs["attachments"] = [file]
            try:
                message = await interaction.edit_original_response(**kwargs)
            except discord.HTTPException:
                embed.set_thumbnail(url=image.source_url)
                kwargs["attachments"] = []
                message = await interaction.edit_original_response(**kwargs)
            finally:
                file.close()
                stream.close()
        else:
            kwargs["attachments"] = []
            message = await interaction.edit_original_response(**kwargs)
        if message is not None:
            self.message = message
        return message

    async def commit(self, interaction, candidate: Session, *, undo=False):
        if not await self.ensure_active(interaction):
            return
        previous, old_undo = self.session, self.undo_session
        candidate.revision = previous.revision + 1
        self.session = candidate
        if undo:
            self.undo_session = copy.deepcopy(previous)
        self.rebuild()
        try:
            await self.publish(interaction)
        except Exception:
            self.session, self.undo_session = previous, old_undo
            self.rebuild()
            raise
        log.debug("exo: committed owner=%s revision=%s mode=%s", self.owner_id, candidate.revision, candidate.mode)

    @staticmethod
    def result_text(row, prefix="Tentative"):
        lost = ", ".join(f"{STATS[key].name} −{value}" for key, value in row["losses"].items() if value) or "aucune"
        text = (
            f"{prefix} : **{row['outcome']}** · {row['rune']}\n"
            f"Pertes : {lost} · puits nominal {row['sink_before']} → {row['sink_after']}."
        )
        if D(row["unexplained_weight"]) > 0:
            text += (
                f"\n⚠ Poids non compensé {row['unexplained_weight']} : le modèle ne reproduit pas "
                "la résolution serveur de cette situation."
            )
        return text

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
        self.rebuild()
        try:
            await self.publish(interaction)
        except Exception:
            self.search_entries, self.search_page, self.search_label = old
            self.session.revision -= 1
            self.rebuild()
            raise

    async def dispatch(self, interaction, action, value=None):
        if not await self.interaction_check(interaction):
            return
        modal_action = "search" if action == "tab" and value == "search" else action
        if modal_action in {"jets", "rates", "observe", "math", "budget", "search"}:
            async with self.lock:
                if not await self.ensure_active(interaction):
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
            return
        await interaction.response.defer()
        async with self.lock:
            if not await self.ensure_active(interaction):
                return
            try:
                await self.handle(interaction, action, value)
            except (ValueError, WikiError) as exc:
                log.debug("exo: action_rejected action=%s reason=%s", action, exc)
                await private_message(interaction, str(exc))

    async def handle(self, interaction, action, value):
        s = copy.deepcopy(self.session)
        if action == "export":
            data = export_session(s)
            stream = BytesIO(data)
            file = discord.File(stream, filename="exo-retro-session.json")
            try:
                await interaction.followup.send(
                    "Sauvegarde personnelle : /exo reprise:<ce fichier>. Les jets restent déclaratifs.",
                    file=file, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                file.close()
                stream.close()
            log.debug("exo: exported owner=%s size=%s", self.owner_id, len(data))
            return
        if action == "close":
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(view=self)
            self.stop()
            return
        if action == "item":
            index = whole(value, 0, max(0, len(self.search_entries) - 1), "Objet")
            item, image = await self.cog.load_item(self.search_entries[index])
            old_image, old_entries = self.image, self.search_entries
            old_page = self.page
            self.image, self.search_entries, self.page = image, [], 0
            previous_undo = self.undo_session
            self.undo_session = None
            try:
                await self.commit(interaction, Session.create(item, self.session.goal_stat))
            except Exception:
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
            except Exception:
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
            except Exception:
                self.search_entries = old_entries
                self.rebuild()
                raise
            return
        if action == "stat":
            if value == "__page":
                old_page = self.page
                self.page = (self.page + 1) % ((len(STATS) + 23) // 24)
                try:
                    await self.commit(interaction, s)
                except Exception:
                    self.page = old_page
                    self.rebuild()
                    raise
                return
            s.rune = Rune(stat_key(value))
            s.notice = ""
        elif action == "rune":
            s.rune = Rune(s.rune.stat, whole(value, 0, 2, "Taille"))
            s.notice = ""
        elif action == "mode":
            s.mode = "observation" if s.mode == "simulation" else "simulation"
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
            except Exception:
                self.undo_session = previous_undo
                self.rebuild()
                raise
            return
        elif action in {"one", "ten", "hundred"}:
            if s.mode != "simulation":
                raise ValueError("Les tirages sont désactivés dans le suivi réel.")
            count = {"one": 1, "ten": 10, "hundred": 100}[action]
            if count > 1 and s.reached:
                raise ValueError("Objectif déjà atteint. Changez-le pour lancer un lot.")
            last, done = None, 0
            for _ in range(count):
                try:
                    last = attempt(s.item, s.sim, s.rune, s.rates, s.seed, s.price)
                except ValueError:
                    if done == 0:
                        raise
                    break
                done += 1
                if s.reached:
                    break
            s.notice = f"Lot : {done}/{count} tentative(s).\n" + self.result_text(last)
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
            undo=action in {"one", "ten", "hundred"},
        )

    async def on_error(self, interaction, error, item):
        log.exception("exo: view_error owner=%s", self.owner_id, exc_info=error)
        await private_message(interaction, "L'action n'a pas pu être validée. Réessayez depuis le dernier état affiché.")


class ExoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.views: dict[tuple[int, int], ExoView] = {}
        self.open_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self.compute_slots = asyncio.Semaphore(2)
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
        async with asyncio.timeout(25):
            detail = await wiki.client.detail(entry)
            try:
                async with asyncio.timeout(10):
                    enrichment = await wiki.enrichment_client.enrich(detail) if wiki.enrichment_client else None
            except (TimeoutError, WikiError, XixouError):
                enrichment = None
                log.debug("exo: enrichment_fallback item=%s", entry.token, exc_info=True)
            item = from_detail(detail, enrichment)
            try:
                image = await wiki.resolve_item_image(detail, enrichment)
            except (TimeoutError, WikiError):
                image = None
                log.debug("exo: image_unavailable item=%s", entry.token, exc_info=True)
        return item, image

    async def cog_unload(self):
        self.closed = True
        views = list(self.views.values())
        for view in views:
            view.stop()
        for view in views:
            if view.message:
                for child in view.children:
                    child.disabled = True
                try:
                    await view.message.edit(view=view)
                except discord.HTTPException:
                    log.debug("exo: unload_message_unavailable", exc_info=True)
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
            return [app_commands.Choice(name=entry.label, value=entry.token) for entry in found]
        except (WikiError, ValueError):
            log.debug("exo: autocomplete_unavailable", exc_info=True)
            return []

    @app_commands.command(name="exo", description="Atelier exo Rétro : essais, puits nominal, probabilités, budget et suivi.")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.checks.cooldown(2, 15, key=lambda interaction: (interaction.guild_id, interaction.user.id))
    @app_commands.describe(
        objet="Objet mageable, sinon démonstration Gelano sans réseau.",
        objectif="Caractéristique visée au départ (modifiable dans l'atelier).",
        reprise="Reprendre un export JSON personnel /exo (128 Kio maximum).",
    )
    @app_commands.choices(objectif=[
        app_commands.Choice(name="Exo PM", value="pm"),
        app_commands.Choice(name="Exo PA", value="pa"),
        app_commands.Choice(name="Exo Portée", value="po"),
    ])
    async def exo(
        self, interaction: discord.Interaction, objet: str | None = None,
        objectif: str = "pm", reprise: discord.Attachment | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = (interaction.guild_id, interaction.user.id)
        if key not in self.views and len(self.views) + len(self.open_locks) >= MAX_SESSIONS:
            await private_message(interaction, "Tous les ateliers sont occupés. Réessayez après l'expiration d'une session.")
            return
        if key in self.open_locks:
            await private_message(interaction, "Une ouverture d'atelier est déjà en cours pour vous.")
            return
        self.open_locks[key] = asyncio.Lock()
        new_view = None
        try:
            if reprise is not None and objet is not None:
                raise ValueError("Choisissez un objet OU un fichier de reprise, pas les deux.")
            entries, label, image = [], "", None
            if reprise is not None:
                if reprise.size > EXPORT_LIMIT or not reprise.filename.lower().endswith(".json"):
                    raise ValueError("La reprise attend un fichier .json de 128 Kio maximum.")
                async with asyncio.timeout(10):
                    raw = await reprise.read()
                session = import_session(raw)
            else:
                session = Session.create(demo_item(), objectif)
                session.notice = (
                    "Démonstration locale : jet théorique PA=1, puits supposé 0. "
                    "Choisissez un objet réel dans le menu, puis déclarez votre jet."
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
                        session.notice = "Jet initial au maximum théorique ; puits 0 supposé. Saisissez votre jet avec « Jet / objectif »."
                    else:
                        entries, label = found, ("Suggestions approchantes" if fuzzy else "Résultats") + f" pour « {objet} »"
            if self.closed:
                raise ValueError("Module en cours de rechargement. Réessayez /exo.")
            new_view = ExoView(self, interaction.user.id, interaction.guild_id, session, image)
            new_view.search_entries, new_view.search_label = entries, label
            new_view.rebuild()
            await new_view.publish(interaction, initial=True)
            if self.closed:
                await new_view.on_timeout()
                raise ValueError("Module rechargé pendant l'ouverture. Réessayez /exo.")
            old_view = self.views.get(key)
            if old_view:
                await old_view.on_timeout()
            self.views[key] = new_view
            new_view.hard_timeout = asyncio.create_task(new_view.hard_expire())
            log.debug("exo: opened owner=%s item=%s imported=%s", interaction.user.id, session.item.token, reprise is not None)
        except (ValueError, WikiError, TimeoutError) as exc:
            if new_view is not None:
                new_view.stop()
            message = str(exc) or "Le catalogue n'a pas répondu à temps. /exo sans objet reste utilisable."
            await private_message(interaction, message)
            log.debug("exo: opening_rejected reason=%s", message)
        except Exception:
            if new_view is not None:
                new_view.stop()
            log.exception("exo: opening_failed owner=%s", interaction.user.id)
            await private_message(interaction, "L'atelier n'a pas pu être ouvert. Le problème a été journalisé.")
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
