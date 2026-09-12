"""Guide interactif construit depuis le catalogue réellement installé."""

from __future__ import annotations

import logging
import math

import discord
from discord import app_commands

from utils.command_policy import unavailable_reason
from utils.slash_errors import error_message, log_command_error, send_interaction_error

log = logging.getLogger(__name__)
PAGE_SIZE = 7
CATEGORIES = ("Activités", "Métiers et personnages", "Dofus Rétro", "Vie de guilde", "Staff", "IA")


def category(command) -> str:
    name = command.qualified_name.split()[0]
    if command.description.startswith(("Staff", "Administration")) or name in {"accueil", "clear"}:
        return "Staff"
    if name in {"calendrier", "activite", "sondage", "close_sondage", "organisation", "event"}:
        return "Activités"
    if name in {"job", "membre", "profil", "ladder"}:
        return "Métiers et personnages"
    if name in {"objet", "recette", "equipement", "monstre", "rune"}:
        return "Dofus Rétro"
    if name in {"ia", "iahelp", "iaend", "bot", "analyse", "pl", "iastaff"}:
        return "IA"
    return "Vie de guilde"


def help_catalog(bot) -> dict[str, list]:
    catalog = {name: [] for name in CATEGORIES}
    for command in bot.tree.walk_commands():
        if not isinstance(command, app_commands.Command) or unavailable_reason(command.qualified_name):
            continue
        catalog[category(command)].append(command)
    return {name: sorted(commands, key=lambda command: command.qualified_name)
            for name, commands in catalog.items() if commands}


class HelpView(discord.ui.View):
    def __init__(self, bot, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.catalog = help_catalog(bot)
        self.section = "Démarrer"
        self.page = 0
        self.message = None
        self.sections.options = [
            discord.SelectOption(label=name, value=name) for name in ["Démarrer", *self.catalog]
        ]
        self.refresh()

    @property
    def pages(self):
        return max(1, math.ceil(len(self.catalog.get(self.section, [])) / PAGE_SIZE))

    def refresh(self):
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.pages - 1

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"Evolution BOT · {self.section}", colour=discord.Colour.blurple())
        if self.section == "Démarrer":
            available = {command.qualified_name for commands in self.catalog.values() for command in commands}
            shortcuts = {
                "calendrier": "Voir les sorties et s'inscrire, sans formulaire.",
                "job mes-metiers": "Retrouver ses métiers.",
                "membre moi": "Consulter ses personnages.",
                "profil modifier": "Compléter son profil en messages privés.",
                "ticket": "Contacter le Staff en privé.",
            }
            lines = [f"**`/{name}`** — {description}" for name, description in shortcuts.items()
                     if name in available]
            embed.description = (
                "Choisis une commande dans le menu `/` de Discord, puis envoie-la.\n"
                "Les champs facultatifs peuvent rester vides. Les commandes `!` restent disponibles.\n\n"
                + ("\n\n".join(lines) if lines else "Choisis une catégorie ci-dessous pour explorer le catalogue.")
            )
            embed.add_field(
                name="Un guide personnel",
                value="Choisis une catégorie. Les fonctions désactivées ne sont pas proposées. "
                      "Les rôles et permissions du serveur restent obligatoires.",
                inline=False,
            )
        else:
            commands = self.catalog[self.section][self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
            for command in commands:
                required = [f"`{parameter.display_name}`" for parameter in command.parameters if parameter.required]
                fields = "Champs obligatoires : " + ", ".join(required) if required else "Aucun champ obligatoire."
                embed.add_field(name="/" + command.qualified_name,
                                value=f"{command.description}\n{fields}", inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages} · /aide pour rouvrir ce guide")
        return embed

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await send_interaction_error(interaction, "Ouvre ton propre guide avec `/aide`.")
            return False
        return True

    async def update(self, interaction):
        self.refresh()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.select(placeholder="Choisir une catégorie")
    async def sections(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.section, self.page = select.values[0], 0
        await self.update(interaction)

    @discord.ui.button(label="Précédent", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self.update(interaction)

    @discord.ui.button(label="Suivant", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        await self.update(interaction)

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                log.debug("Slash : guide expiré devenu inaccessible.", exc_info=True)

    async def on_error(self, interaction, error, item):
        log_command_error(log, error, command="aide")
        await send_interaction_error(interaction, error_message(error))


async def show_help(ctx, bot):
    view = HelpView(bot, ctx.author.id)
    view.message = await ctx.send(embed=view.embed(), view=view)
