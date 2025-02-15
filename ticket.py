"""
Fichier : ticket.py
Description :
    Extension (Cog) gérant la création et le suivi des tickets de support.
    Le bot interagit avec l'utilisateur via des messages privés (DM) pour
    recueillir sa demande, puis envoie un embed interactif dans un canal
    nommé "#ticket" sur le serveur, avec des boutons permettant au staff
    de prendre en charge et de modifier le statut du ticket.

Note importante :
    Pour que la commande !staff fonctionne (et affiche bien les membres du rôle "Staff"),
    vous devez activer l'intent 'members' dans votre bot :
      1) Sur le portail développeur Discord (onglet Bot), cochez "Server Members Intent".
      2) Dans le code principal, faites :
           intents = discord.Intents.default()
           intents.members = True
           bot = commands.Bot(command_prefix="!", intents=intents)
      3) S'assurer que le bot est autorisé à voir les membres (droits sur le serveur).

De plus, pour éviter les soucis de cache, on force ici un fetch des membres
dans la commande !staff (cf. staff_list).
"""

import discord
from discord.ext import commands
import asyncio
import datetime

# Ensemble global permettant de suivre les utilisateurs ayant déjà un ticket ouvert
open_tickets = set()


class TicketView(discord.ui.View):
    """
    Vue (View) contenant les boutons d'interaction pour la gestion d'un ticket.
    """
    def __init__(self, author: discord.User):
        super().__init__(timeout=None)
        self.author = author
        self.taken_by = None  # Membre du staff qui prend en charge le ticket

    @discord.ui.button(label="Prendre en charge ✅", style=discord.ButtonStyle.primary)
    async def take_ticket(self, button: discord.ui.Button, interaction: discord.Interaction):
        print(f"[DEBUG] Bouton 'Prendre en charge' cliqué par {interaction.user} (ID: {interaction.user.id}).")

        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Cette interaction ne peut pas être effectuée depuis un DM.",
                ephemeral=True
            )
            return

        # Récupération du rôle "Staff"
        staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
        print(f"[DEBUG] staff_role = {staff_role}, user roles = {[r.name for r in interaction.user.roles]}")

        if staff_role is None or staff_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Vous n'avez pas la permission (rôle 'Staff') pour prendre en charge ce ticket.",
                ephemeral=True
            )
            return

        if self.taken_by is None:
            self.taken_by = interaction.user
            embed = interaction.message.embeds[0]

            # Remplacer "En attente ⌛" par "En cours 🔄"
            for index, field in enumerate(embed.fields):
                if field.name == "Statut":
                    embed.set_field_at(index, name="Statut", value="En cours 🔄", inline=field.inline)
                    break

            # Ajout du champ "Pris en charge par"
            embed.add_field(
                name="Pris en charge par",
                value=interaction.user.display_name,
                inline=False
            )

            embed.color = discord.Color.gold()  # Couleur or = "en cours"
            button.disabled = True

            await interaction.response.edit_message(embed=embed, view=self)
        else:
            print("[DEBUG] Ticket déjà pris en charge.")
            await interaction.response.send_message(
                "⚠️ Ce ticket est déjà pris en charge par quelqu'un d'autre.",
                ephemeral=True
            )

    @discord.ui.button(label="Résolu ✅", style=discord.ButtonStyle.success)
    async def mark_resolved(self, button: discord.ui.Button, interaction: discord.Interaction):
        print(f"[DEBUG] Bouton 'Résolu' cliqué par {interaction.user} (ID: {interaction.user.id}).")

        if not interaction.guild:
            await interaction.response.send_message(
                "❌ Cette interaction ne peut pas être effectuée depuis un DM.",
                ephemeral=True
            )
            return

        staff_role = discord.utils.get(interaction.guild.roles, name="Staff")
        if staff_role is None or staff_role not in interaction.user.roles:
            await interaction.response.send_message(
                "❌ Vous n'avez pas la permission de modifier le statut de ce ticket (rôle 'Staff' requis).",
                ephemeral=True
            )
            return

        if self.taken_by is None:
            await interaction.response.send_message(
                "⚠️ Veuillez d'abord prendre en charge le ticket avant de le marquer comme résolu.",
                ephemeral=True
            )
            return

        embed = interaction.message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "Statut":
                embed.set_field_at(index, name="Statut", value="Résolu ✅", inline=field.inline)
                break

        embed.color = discord.Color.green()  # Couleur verte = résolu

        # Désactiver tous les boutons
        for child in self.children:
            child.disabled = True

        # Retirer l'utilisateur de l'ensemble open_tickets
        open_tickets.discard(self.author.id)

        await interaction.response.edit_message(embed=embed, view=self)

        # Envoi d'un MP à l'utilisateur pour le prévenir
        try:
            await self.author.send(
                f"Votre ticket a été résolu par le staff : **{self.taken_by.display_name}**.\n"
                "Merci pour votre patience !"
            )
        except discord.Forbidden:
            print("[DEBUG] Impossible d'envoyer un DM à l'auteur (DM bloqués).")

        self.stop()


class TicketCog(commands.Cog):
    """
    Gère la commande !ticket et la commande !staff pour afficher les membres du Staff.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="ticket")
    async def create_ticket(self, ctx: commands.Context):
        """
        Crée un nouveau ticket. Envoie d'abord un DM à l'utilisateur pour recueillir sa demande,
        puis publie un embed dans #ticket avec des boutons pour le staff.
        """
        print(f"[DEBUG] Commande !ticket appelée par {ctx.author} (ID: {ctx.author.id}).")

        if ctx.guild is None:
            return  # Commande lancée en DM => rien ne se passe

        user = ctx.author

        # Supprimer le message de commande si possible
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("[DEBUG] Impossible de supprimer le message !ticket")

        if user.id in open_tickets:
            await user.send(
                "⚠️ Vous avez déjà un ticket en cours. Veuillez attendre qu'il soit traité avant d'en créer un nouveau."
            )
            return

        # Marquer l'utilisateur comme "ayant un ticket"
        open_tickets.add(user.id)

        # Tente d'envoyer un DM pour recueillir la description
        try:
            await user.send(
                "Bonjour, vous avez ouvert un ticket de support.\n"
                "Veuillez décrire votre problème ou demande. *(Vous avez 5 minutes pour répondre.)*"
            )
        except discord.Forbidden:
            open_tickets.discard(user.id)
            await ctx.send(
                f"{user.mention}, je n'ai pas pu ouvrir le ticket car vous bloquez les MP."
            )
            return

        # On attend la réponse en DM
        def check_dm(m: discord.Message):
            return m.author == user and isinstance(m.channel, discord.DMChannel)

        try:
            dm_message = await self.bot.wait_for('message', timeout=300.0, check=check_dm)
        except asyncio.TimeoutError:
            open_tickets.discard(user.id)
            await user.send("⏰ Temps écoulé. Votre ticket a été annulé.")
            return

        ticket_content = dm_message.content

        # Création de l'embed
        embed = discord.Embed(
            title="🎫 Nouveau Ticket",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Demandeur", value=user.display_name, inline=True)
        embed.add_field(name="Statut", value="En attente ⌛", inline=True)
        embed.add_field(name="Contenu du ticket", value=ticket_content, inline=False)

        # Chercher le salon #ticket
        ticket_channel = discord.utils.get(ctx.guild.text_channels, name="ticket")
        if ticket_channel is None:
            open_tickets.discard(user.id)
            await user.send("❌ Le ticket n'a pas pu être créé car le canal `#ticket` est introuvable.")
            return

        # Créer la View
        view = TicketView(user)

        # Envoyer l'embed dans #ticket
        await ticket_channel.send(embed=embed, view=view)
        await user.send(
            "✅ Votre ticket a bien été envoyé au staff. Vous serez recontacté ici une fois pris en charge."
        )
        print(f"[DEBUG] Ticket créé par {user} et envoyé dans #{ticket_channel.name}.")

    @commands.command(name="staff")
    async def staff_list(self, ctx: commands.Context):
        """
        Affiche la liste de tous les membres possédant le rôle 'Staff'.
        Force un fetch_members() pour contourner les caches.
        """
        if not self.bot.intents.members:
            await ctx.send(
                "❌ L'intent 'members' n'est pas activé. Activez-le dans votre code et sur le portail Discord (Server Members Intent)."
            )
            return

        all_fetched_members = []
        try:
            # On récupère tous les membres du serveur
            async for member in ctx.guild.fetch_members(limit=None):
                all_fetched_members.append(member)
        except discord.HTTPException as e:
            await ctx.send(f"Erreur lors du fetch des membres : {e}")
            return

        staff_role = discord.utils.get(ctx.guild.roles, name="Staff")
        if staff_role is None:
            await ctx.send("Le rôle 'Staff' n'existe pas sur ce serveur.")
            return

        # Filtrer sur ceux qui ont le rôle staff
        members_with_staff_role = [m for m in all_fetched_members if staff_role in m.roles]

        if not members_with_staff_role:
            await ctx.send("Aucun membre ne possède le rôle 'Staff'.")
            return

        # Construire la liste
        lines = [f"- {member.mention} (ID: {member.id})" for member in members_with_staff_role]
        staff_list_str = "\n".join(lines)

        embed = discord.Embed(
            title="Liste des membres Staff",
            description=staff_list_str,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


# Pour Py-Cord / Discord.py 2.x, on définit la fonction setup asynchrone
async def setup(bot: commands.Bot):
    """
    Pour charger ce cog :
      await bot.load_extension('ticket')

    Assurez-vous d'activer l'intent "members" :
      intents = discord.Intents.default()
      intents.members = True
      bot = commands.Bot(command_prefix='!', intents=intents)

    Et sur le portail dev Discord (onglet Bot), cochez "Server Members Intent".
    """
    await bot.add_cog(TicketCog(bot))
