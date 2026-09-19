"""Boutons de partage redémarrables via DynamicItem (discord.py >= 2.4).

Le custom_id n'accorde aucun droit : la consultation recontrôle le serveur,
la présence du membre, la révocation et l'expiration dans le stockage.
Aucune vue d'édition du propriétaire n'est donnée au lecteur du partage.
"""
from __future__ import annotations

import re
import discord

from .calculator import calculate
from .embeds import card
from .models import BuildError

PATTERN = r"evobuild:(?P<action>voir|copier):(?P<code>[A-Za-z0-9_-]{32})"


class SharedBuildAction(discord.ui.DynamicItem[discord.ui.Button], template=PATTERN):
    def __init__(self, action: str, code: str):
        custom_id = f"evobuild:{action}:{code}"
        if re.fullmatch(PATTERN, custom_id) is None:
            raise ValueError("Identifiant de partage invalide.")
        self.action, self.code = action, code
        super().__init__(discord.ui.Button(
            label="Voir le build" if action == "voir" else "Copier dans mes builds",
            style=discord.ButtonStyle.primary if action == "voir" else discord.ButtonStyle.secondary,
            custom_id=custom_id,
        ))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        # Aucun accès réseau avant l'acquittement de l'interaction.
        return cls(match["action"], match["code"])

    async def callback(self, interaction):
        cog = interaction.client.get_cog("BuildCog")
        if cog is None:
            await interaction.response.send_message(
                "Evolution Build est actuellement désactivé.", ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            cog.ensure_ready()
            actor = cog.actor(interaction)
            if self.action == "copier":
                preview = await cog.service.copy(actor, self.code)
                await cog.show_preview(interaction, actor, preview)
                return
            build = await cog.repository.shared(actor, self.code)
            catalog, rules = await cog.service.dependencies(build)
            report = calculate(build, catalog, rules)
            await interaction.edit_original_response(
                content="**Révision partagée, lecture seule.** Copier crée ton propre exemplaire après confirmation.",
                embed=card(build, report, catalog, cog.repository.durable),
                # Le bouton de copie reste soumis aux mêmes contrôles.
                view=SharedBuildView(self.code),
            )
        except Exception as exc:
            # DynamicItem n'appelle pas automatiquement OwnerView.on_error.
            await cog.error(interaction, exc)


class SharedBuildView(discord.ui.View):
    def __init__(self, code: str):
        super().__init__(timeout=None)
        self.add_item(SharedBuildAction("voir", code))
        self.add_item(SharedBuildAction("copier", code))
