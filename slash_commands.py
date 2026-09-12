"""Je rends les commandes installées accessibles dans le menu / d'Evolution BOT."""

from __future__ import annotations

import inspect
import logging
import types
import typing
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from utils.slash_catalog import (
    COMMAND_NAMES, DESCRIPTIONS, GROUP_DESCRIPTIONS, PARAMETERS,
    Option, Route, custom_routes, format_arguments, quote_token, text_limit, validate_values,
)
from utils.slash_support import invoke_from_slash
from utils.command_policy import remove_unavailable_commands, unavailable_reason
from utils.slash_confirm import DESTRUCTIVE_ROUTES, request_confirmation
from utils.slash_errors import SlashInputError
from utils.calendar_data import one_line, snapshot_events
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _search_key(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )


def generic_route(command: commands.Command) -> Route:
    options = []
    for name, parameter in command.clean_params.items():
        option_name, description = PARAMETERS.get(name, (name, f"Valeur de {name}."))
        if command.qualified_name == "membre" and name == "arg":
            option_name, description = "joueur", "Nom du personnage ou mention du membre à consulter."
        annotation = parameter.annotation
        if typing.get_origin(annotation) in (typing.Union, types.UnionType):
            annotation = next(t for t in typing.get_args(annotation) if t is not type(None))
        if annotation not in (str, int, float, bool, discord.Member, discord.User, discord.TextChannel):
            annotation = str
        if name == "message_id":
            annotation = str
        default = ... if parameter.required else parameter.default
        if command.qualified_name in {
            "recrutement", "membre principal", "membre addmule", "membre delmule",
            "membre del", "scan", "close_sondage",
        }:
            default = ...
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            option_name, description, default = "arguments", "Arguments complémentaires de la commande.", ""
        options.append(Option(option_name, description, annotation, default))
    path = tuple(parent.name for parent in reversed(command.parents))
    path += (COMMAND_NAMES.get(command.qualified_name, command.name),)
    if isinstance(command, commands.Group):
        path += ("voir" if command.name in {"profil", "membre", "stats"} else "aide",)
    description = DESCRIPTIONS.get(command.qualified_name)
    if description is None:
        description = command.short_doc or f"Utiliser la commande {command.qualified_name}."
    return Route(path, command.qualified_name, description[:100], tuple(options), mode="generic")


def generic_arguments(command: commands.Command, route: Route, values: dict[str, object]) -> str:
    parts = []
    for parameter, option in zip(command.clean_params.values(), route.options):
        value = values.get(option.name)
        if value is None:
            continue
        text = str(getattr(value, "mention", value))
        if parameter.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_POSITIONAL):
            parts.append(text)
        else:
            parts.append(quote_token(text))
    return " ".join(parts)


class SlashCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.routes: dict[tuple[str, ...], Route] = {}
        self.owned_commands: dict[str, app_commands.Command | app_commands.Group] = {}
        self.covered_commands: set[str] = set()
        self.excluded_commands: dict[str, str] = {}
        self.activity_context_menu = None

    async def cog_load(self):
        try:
            self.register_commands()
        except Exception:
            self.cog_unload()
            raise

    def cog_unload(self):
        for name, command in self.owned_commands.items():
            if self.bot.tree.get_command(name) is command:
                self.bot.tree.remove_command(name)
        self.owned_commands.clear()
        if self.activity_context_menu is not None:
            self.bot.tree.remove_command(self.activity_context_menu.name, type=discord.AppCommandType.message)
            self.activity_context_menu = None

    def register_commands(self):
        remove_unavailable_commands(self.bot)
        native_names = {command.name for command in self.bot.tree.get_commands()}
        custom = custom_routes()
        custom_targets = {route.target for route in custom}
        for command in self.bot.walk_commands():
            if not command.enabled or command.hidden or any(not p.enabled or p.hidden for p in command.parents):
                self.excluded_commands[command.qualified_name] = "commande désactivée ou masquée"
        for route in custom:
            if self.bot.get_command(route.target) is not None and route.target not in self.excluded_commands:
                self._register_route(route)
        for command in sorted(self.bot.walk_commands(), key=lambda cmd: cmd.qualified_name):
            if command.qualified_name in custom_targets or command.qualified_name in self.excluded_commands:
                continue
            if command.parent is None and command.name in native_names:
                self.covered_commands.add(command.qualified_name)
                continue
            self._register_route(generic_route(command))
        missing = (
            {command.qualified_name for command in self.bot.walk_commands()}
            - self.covered_commands - self.excluded_commands.keys()
        )
        if missing:
            raise RuntimeError(f"Commandes sans accès slash : {', '.join(sorted(missing))}")
        if self.bot.get_command("activite") is not None:
            self.activity_context_menu = app_commands.ContextMenu(
                name="Créer une activité", callback=self.activity_from_message,
                allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
            )
            self.bot.tree.add_command(self.activity_context_menu)
        log.debug(
            "Slash: catalog_registered roots=%s routes=%s covered_prefix_commands=%s",
            len(self.bot.tree.get_commands()), len(self.routes), len(self.covered_commands),
        )

    def _register_route(self, route: Route):
        if route.path in self.routes:
            raise ValueError(f"Route slash déjà enregistrée : {' '.join(route.path)}")
        command = self._make_command(route)
        if len(route.path) == 1:
            self.bot.tree.add_command(command)
            self.owned_commands[command.name] = command
        else:
            root_name = route.path[0]
            group = self.owned_commands.get(root_name)
            if group is None:
                group = app_commands.Group(
                    name=root_name,
                    description=GROUP_DESCRIPTIONS.get(root_name, f"Commandes {root_name}."),
                    guild_only=True,
                    allowed_installs=app_commands.AppInstallationType(guild=True, user=False),
                )
                self.bot.tree.add_command(group)
                self.owned_commands[root_name] = group
            if not isinstance(group, app_commands.Group):
                raise ValueError(f"Le nom slash {root_name} est déjà utilisé par une commande.")
            for subgroup_name in route.path[1:-1]:
                subgroup = group.get_command(subgroup_name)
                if subgroup is None:
                    subgroup = app_commands.Group(name=subgroup_name, description=f"Commandes {subgroup_name}.")
                    group.add_command(subgroup)
                group = subgroup
            group.add_command(command)
        self.routes[route.path] = route
        self.covered_commands.add(route.target)

    def _make_command(self, route: Route) -> app_commands.Command:
        async def callback(interaction: discord.Interaction, **values):
            values = validate_values(route, values)
            target = self.bot.get_command(route.target)
            if route.mode == "generic" and target is not None:
                arguments = generic_arguments(target, route, values)
            else:
                arguments = format_arguments(route, values)
            if route.path in DESTRUCTIVE_ROUTES:
                return await request_confirmation(self.bot, interaction, route, arguments, values)
            return await invoke_from_slash(
                self.bot, interaction, route.target, arguments, values=values,
                message_reference=values.get("message") if route.mode == "message_reference" else None,
                private=route.path in {("job", "ajouter"), ("job", "supprimer"), ("job", "nettoyer")}
                or route.path[:1] == ("activite",),
                defer_response=route.mode != "activity_form",
            )

        callback.__signature__ = inspect.Signature([
            inspect.Parameter("interaction", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=discord.Interaction),
            *[
                inspect.Parameter(
                    option.name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=(
                        app_commands.Range[str, 1 if option.default is ... else 0, text_limit(route, option)]
                        if option.annotation is str else option.annotation
                    ),
                    default=inspect.Parameter.empty if option.default is ... else option.default,
                ) for option in route.options
            ],
        ])
        callback = app_commands.describe(**{o.name: o.description for o in route.options})(callback)
        callback = app_commands.guild_only()(callback)
        callback = app_commands.allowed_installs(guilds=True, users=False)(callback)
        if route.target == "defenderstatus":
            callback = app_commands.default_permissions(manage_guild=True)(callback)
        for option in route.options:
            if option.choices:
                callback = app_commands.choices(**{
                    option.name: [app_commands.Choice(name=value, value=value) for value in option.choices]
                })(callback)
        command = app_commands.Command(name=route.path[-1], description=route.description, callback=callback)
        for option in route.options:
            if option.autocomplete == "jobs":
                command.autocomplete(option.name)(self.autocomplete_jobs)
            elif option.autocomplete == "polls":
                command.autocomplete(option.name)(self.autocomplete_polls)
            elif option.autocomplete == "activities":
                command.autocomplete(option.name)(self.autocomplete_activities)
            elif option.autocomplete and option.autocomplete.startswith("wiki_"):
                command.autocomplete(option.name)(self.wiki_autocomplete(option.autocomplete))
        return command

    @app_commands.guild_only()
    async def activity_from_message(self, interaction: discord.Interaction, message: discord.Message):
        await invoke_from_slash(
            self.bot, interaction, "activite", "depuis", values={"_source_message": message},
            private=True, defer_response=False,
        )

    def wiki_autocomplete(self, kind: str):
        async def callback(interaction: discord.Interaction, current: str):
            cog = self.bot.get_cog("DofusWikiCog")
            if cog is None:
                return []
            return await cog.autocomplete(kind, current)

        return callback

    async def autocomplete_jobs(self, interaction: discord.Interaction, current: str):
        from job import CANONICAL_JOBS_ORDERED

        names = set(CANONICAL_JOBS_ORDERED)
        cog = self.bot.get_cog("JobCog")
        if cog is not None:
            for data in cog.jobs_data.values():
                if isinstance(data, dict) and isinstance(data.get("jobs", {}), dict):
                    names.update(name for name in data.get("jobs", {}) if isinstance(name, str))
        query = _search_key(current)
        ordered = sorted(names, key=lambda name: (not _search_key(name).startswith(query), _search_key(name)))
        return [
            app_commands.Choice(name=name[:100], value=name)
            for name in ordered if query in _search_key(name) and len(name) <= 100
        ][:25]

    async def autocomplete_activities(self, interaction: discord.Interaction, current: str):
        cog = self.bot.get_cog("ActiviteCog")
        if cog is None:
            return []
        guild_id = getattr(interaction, "guild_id", None)
        if hasattr(cog, "events_for_guild"):
            records = cog.events_for_guild(guild_id)
        else:
            records = {
                key: event for key, event in cog.activities_data.get("events", {}).items()
                if isinstance(event, dict) and event.get("guild_id", guild_id) == guild_id
            }
        now = datetime.now(timezone.utc)
        route_name = getattr(getattr(interaction, "command", None), "name", "")
        user = getattr(interaction, "user", None)
        user_id = getattr(user, "id", None)
        choices = []
        snapshot = snapshot_events({
            key: {**record, "cancelled": False} for key, record in records.items()
        })
        ordered = sorted(snapshot.events, key=lambda event: (
            event.has_started(now), event.timestamp if not event.has_started(now) else -event.timestamp,
        ))
        for event in ordered:
            record = records[event.id]
            participants = record.get("participants", []) + record.get("waitlist", [])
            if route_name != "info" and (record.get("cancelled") or event.has_started(now)):
                continue
            if route_name == "quitter" and user_id not in participants:
                continue
            if route_name == "rejoindre" and user_id in participants:
                continue
            if route_name in {"modifier", "annuler"} and (
                record.get("creator_id") != user_id
                and not (hasattr(cog, "is_staff") and cog.is_staff(user))
            ):
                continue
            label = f"{event.starts_at:%d/%m %H:%M} · {event.title} · #{event.id}"
            if record.get("cancelled"):
                label += " · annulée"
            if _search_key(current) in _search_key(label):
                choices.append(app_commands.Choice(name=one_line(label, 100), value=event.id))
            if len(choices) == 25:
                break
        return choices

    async def autocomplete_polls(self, interaction: discord.Interaction, current: str):
        from sondage import POLL_STORAGE

        cog = self.bot.get_cog("SondageCog")
        if cog is None or interaction.guild is None:
            return []
        staff = cog._is_staff(interaction.user)
        choices = []
        for message_id, poll in list(POLL_STORAGE.items()):
            channel = self.bot.get_channel(poll.get("channel_id"))
            guild_id = poll.get("guild_id") or getattr(getattr(channel, "guild", None), "id", None)
            if guild_id != interaction.guild_id:
                continue
            if not staff and poll.get("author_id") != interaction.user.id:
                continue
            label = f"{poll.get('title', 'Sondage')} · {message_id}"
            if _search_key(current) in _search_key(label):
                choices.append(app_commands.Choice(name=one_line(label, 100), value=str(message_id)))
            if len(choices) == 25:
                break
        return choices


async def setup(bot: commands.Bot):
    await bot.add_cog(SlashCommandsCog(bot))
