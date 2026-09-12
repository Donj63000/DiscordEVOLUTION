"""Confirmation privée à usage unique pour les actions destructrices."""

from __future__ import annotations

import asyncio
import logging

import discord

from utils.slash_errors import error_message, log_command_error, send_interaction_error
from utils.slash_support import invoke_from_component

log = logging.getLogger(__name__)

DESTRUCTIVE_ROUTES = frozenset({
    ("membre", "supprimer"), ("profil", "supprimer"), ("stats", "reinitialiser"),
    ("resetwarnings",), ("activite", "annuler"), ("job", "nettoyer"),
    ("annonce-cancel",), ("accueil", "reset"),
})


class ActionConfirmation(discord.ui.View):
    def __init__(self, bot, interaction, route, arguments: str, values: dict[str, object]):
        super().__init__(timeout=90)
        self.bot = bot
        self.owner_id = interaction.user.id
        self.guild_id = interaction.guild_id
        self.route = route
        self.arguments = arguments
        self.values = dict(values)
        self.message = None
        self._used = False
        self._lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await send_interaction_error(interaction, "Cette confirmation appartient à une autre personne.")
            return False
        if self._used or self.is_finished():
            await send_interaction_error(interaction, "Cette confirmation n'est plus active.")
            return False
        return True

    async def _claim(self, interaction, content: str) -> bool:
        # Une double requête Discord ne doit jamais exécuter deux suppressions.
        async with self._lock:
            if self._used or self.is_finished():
                await send_interaction_error(interaction, "Cette demande a déjà été traitée.")
                return False
            self._used = True
            self.stop()
            await interaction.response.edit_message(
                content=content, embed=None, view=None, allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # La méthode est aussi protégée lors d'un appel direct dans les tests.
        if not await self.interaction_check(interaction):
            return
        if not await self._claim(interaction, "Demande confirmée. Vérification des droits et traitement…"):
            return
        # Le pont recontrôle rôles, conversions, cooldowns et droits métier au clic.
        # Après toute tentative, le bouton reste consommé, même si Discord échoue.
        await invoke_from_component(
            self.bot, interaction, self.route.target, self.arguments, values=self.values,
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.interaction_check(interaction):
            await self._claim(interaction, "Action annulée. Aucune modification.")

    async def on_timeout(self):
        self._used = True
        if self.message is not None:
            try:
                await self.message.edit(content="Confirmation expirée. Aucune action exécutée.",
                                        embed=None, view=None)
            except discord.HTTPException:
                log.debug("Slash : confirmation expirée devenue inaccessible.", exc_info=True)

    async def on_error(self, interaction, error, item):
        log_command_error(log, error, command=" ".join(self.route.path))
        await send_interaction_error(interaction, error_message(error))


async def request_confirmation(bot, interaction, route, arguments, values):
    view = ActionConfirmation(bot, interaction, route, arguments, values)
    # Les valeurs affichées ne déclenchent aucune mention ; la commande ne s'exécute
    # pas tant que son auteur n'a pas cliqué sur Confirmer.
    details = "\n".join(
        f"**{name}** : {discord.utils.escape_markdown(str(getattr(value, 'mention', value)))[:180]}"
        for name, value in values.items() if value is not None and value != ""
    )
    if not details:
        details = "L'action portera sur la cible par défaut de cette commande."
    embed = discord.Embed(
        title="Confirmer cette action ?",
        description=f"`/{' '.join(route.path)}`\n\n{details}\n\nCette action peut supprimer des données.",
        colour=discord.Colour.orange(),
    )
    await interaction.response.send_message(
        embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
    )
    view.message = await interaction.original_response()
    return view
