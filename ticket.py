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
    def __init__(self, author: discord.User, staff_role_name: str):
        super().__init__(timeout=None)
        self.author = author
        self.staff_role_name = staff_role_name
        self.taken_by = None  # Membre du staff qui prend en charge le ticket

    @discord.ui.button(label="Prendre en charge ✅", style=discord.ButtonStyle.primary)
    async def take_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Bouton pour qu'un membre du staff prenne en charge le ticket.
        """
        # On différer la réponse immédiatement pour éviter l'expiration de l'interaction
        await interaction.response.defer()

        # Vérification : l'interaction est-elle dans un salon guild ?
        if not interaction.guild:
            # Envoi d'une réponse éphémère car l'interaction se fait peut-être en DM.
            return await interaction.followup.send(
                "❌ Cette interaction ne peut pas être effectuée depuis un DM.",
                ephemeral=True
            )

        # Récupération du rôle "Staff" via son nom stocké dans self.staff_role_name
        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        # Vérifie que l'utilisateur dispose bien de ce rôle
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send(
                "❌ Vous n'avez pas la permission (rôle 'Staff') pour prendre en charge ce ticket.",
                ephemeral=True
            )

        # Si personne n'a encore pris le ticket
        if self.taken_by is None:
            self.taken_by = interaction.user

            # On sécurise l'accès à l'embed
            message = interaction.message
            if not message or not message.embeds:
                # Cas où l'embed n'est plus accessible ou n'existe pas
                return await interaction.followup.send(
                    "⚠️ Impossible de modifier ce ticket : l'embed d'origine est introuvable.",
                    ephemeral=True
                )

            embed = message.embeds[0]

            # Recherche du champ "Statut" et remplacement de sa valeur
            statut_field_index = None
            for index, field in enumerate(embed.fields):
                if field.name.lower() == "statut":
                    statut_field_index = index
                    break

            if statut_field_index is not None:
                embed.set_field_at(
                    statut_field_index,
                    name="Statut",
                    value="En cours 🔄",
                    inline=embed.fields[statut_field_index].inline
                )
            else:
                # Si pas de champ "Statut", on l'ajoute
                embed.add_field(name="Statut", value="En cours 🔄", inline=True)

            # Ajout du champ "Pris en charge par"
            embed.add_field(
                name="Pris en charge par",
                value=interaction.user.display_name,
                inline=False
            )

            embed.color = discord.Color.gold()  # Couleur or = "en cours"

            # Désactiver le bouton "Prendre en charge" pour éviter les collisions
            button.disabled = True

            # On édite le message original
            await interaction.followup.edit_message(
                message_id=interaction.message.id,
                embed=embed,
                view=self
            )

            # Envoi d'un petit message éphémère de confirmation
            await interaction.followup.send(
                "Le ticket est maintenant pris en charge. 🛠️",
                ephemeral=True
            )
        else:
            # Cas où quelqu'un a déjà pris le ticket
            await interaction.followup.send(
                "⚠️ Ce ticket est déjà pris en charge par quelqu'un d'autre.",
                ephemeral=True
            )

    @discord.ui.button(label="Résolu ✅", style=discord.ButtonStyle.success)
    async def mark_resolved(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Bouton pour marquer un ticket comme résolu.
        """
        # On différer la réponse immédiatement
        await interaction.response.defer()

        if not interaction.guild:
            return await interaction.followup.send(
                "❌ Cette interaction ne peut pas être effectuée depuis un DM.",
                ephemeral=True
            )

        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send(
                "❌ Vous n'avez pas la permission de modifier le statut de ce ticket (rôle 'Staff' requis).",
                ephemeral=True
            )

        # Si le ticket n'a pas encore été pris en charge, on empêche la résolution directe
        if self.taken_by is None:
            return await interaction.followup.send(
                "⚠️ Veuillez d'abord prendre en charge le ticket avant de le marquer comme résolu.",
                ephemeral=True
            )

        message = interaction.message
        if not message or not message.embeds:
            return await interaction.followup.send(
                "⚠️ Impossible de modifier ce ticket : l'embed d'origine est introuvable.",
                ephemeral=True
            )

        embed = message.embeds[0]

        # Recherche du champ "Statut"
        statut_field_index = None
        for index, field in enumerate(embed.fields):
            if field.name.lower() == "statut":
                statut_field_index = index
                break

        if statut_field_index is not None:
            embed.set_field_at(
                statut_field_index,
                name="Statut",
                value="Résolu ✅",
                inline=embed.fields[statut_field_index].inline
            )
        else:
            # Si le champ n'existe pas, on l'ajoute
            embed.add_field(name="Statut", value="Résolu ✅", inline=True)

        embed.color = discord.Color.green()  # Couleur verte = résolu

        # Désactiver tous les boutons de la vue
        for child in self.children:
            child.disabled = True

        # Retirer l'utilisateur de l'ensemble open_tickets
        open_tickets.discard(self.author.id)

        # Éditer le message original pour afficher "Résolu"
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=embed,
            view=self
        )

        # Envoi d'un MP à l'utilisateur (le demandeur) pour le prévenir
        try:
            await self.author.send(
                f"Votre ticket a été résolu par le staff : **{self.taken_by.display_name}**.\n"
                "Merci pour votre patience !"
            )
        except discord.Forbidden:
            # L'utilisateur a peut-être désactivé ses MP
            pass

        # Envoi d'un message éphémère de confirmation côté staff
        await interaction.followup.send(
            f"Le ticket de {self.author.mention} est marqué comme résolu. ✅",
            ephemeral=True
        )

        self.stop()


class TicketCog(commands.Cog):
    """
    Gère la commande !ticket et la commande !staff pour afficher les membres du Staff.
    """
    def __init__(self, bot: commands.Bot, staff_role_name: str = "Staff"):
        self.bot = bot
        self.staff_role_name = staff_role_name  # Nom du rôle staff

    @commands.command(name="ticket")
    async def create_ticket(self, ctx: commands.Context):
        """
        Crée un nouveau ticket. Envoie d'abord un DM à l'utilisateur pour recueillir sa demande,
        puis publie un embed dans #ticket avec des boutons pour le staff.
        """
        print(f"[DEBUG] Commande !ticket appelée par {ctx.author} (ID: {ctx.author.id}).")

        # Empêcher les utilisateurs de créer un ticket en DM (commande doit être exécutée sur un serveur)
        if ctx.guild is None:
            return

        user = ctx.author

        # Supprimer le message de commande si possible
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("[DEBUG] Impossible de supprimer le message !ticket")

        # Vérifier si l'utilisateur n'a pas déjà un ticket en cours
        if user.id in open_tickets:
            try:
                await user.send(
                    "⚠️ Vous avez déjà un ticket en cours. Veuillez attendre qu'il soit résolu avant d'en créer un nouveau."
                )
            except discord.Forbidden:
                pass
            return

        # Marquer l'utilisateur comme "ayant un ticket"
        open_tickets.add(user.id)

        # Tente d'envoyer un DM pour recueillir la description du ticket
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
            try:
                await user.send("⏰ Temps écoulé. Votre ticket a été annulé.")
            except discord.Forbidden:
                pass
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
            try:
                await user.send("❌ Le ticket n'a pas pu être créé car le canal `#ticket` est introuvable.")
            except discord.Forbidden:
                pass
            return

        # Créer la View
        view = TicketView(user, self.staff_role_name)

        # Envoyer l'embed dans #ticket (avec mention du rôle staff pour info)
        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        mention_staff = staff_role.mention if staff_role else "**[Staff non trouvé]**"

        await ticket_channel.send(
            content=f"{mention_staff}, un nouveau ticket a été ouvert !",
            embed=embed,
            view=view
        )

        # Message de confirmation en DM
        try:
            await user.send(
                "✅ Votre ticket a bien été envoyé au staff. Vous serez recontacté ici une fois pris en charge."
            )
        except discord.Forbidden:
            pass

        print(f"[DEBUG] Ticket créé par {user} et envoyé dans #{ticket_channel.name}.")

    @commands.command(name="staff")
    async def staff_list(self, ctx: commands.Context):
        """
        Affiche la liste de tous les membres possédant le rôle 'Staff'.
        Force un fetch_members() pour contourner les caches.
        """
        if not self.bot.intents.members:
            return await ctx.send(
                "❌ L'intent 'members' n'est pas activé. Activez-le dans votre code et sur le portail Discord (Server Members Intent)."
            )

        all_fetched_members = []
        try:
            # On récupère tous les membres du serveur
            async for member in ctx.guild.fetch_members(limit=None):
                all_fetched_members.append(member)
        except discord.HTTPException as e:
            return await ctx.send(f"Erreur lors du fetch des membres : {e}")

        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        if staff_role is None:
            return await ctx.send(f"Le rôle '{self.staff_role_name}' n'existe pas sur ce serveur.")

        # Filtrer sur ceux qui ont le rôle staff
        members_with_staff_role = [m for m in all_fetched_members if staff_role in m.roles]

        if not members_with_staff_role:
            return await ctx.send("Aucun membre ne possède le rôle 'Staff'.")

        # Construire la liste
        lines = [f"- {member.mention} (ID: {member.id})" for member in members_with_staff_role]
        staff_list_str = "\n".join(lines)

        embed = discord.Embed(
            title="Liste des membres Staff",
            description=staff_list_str,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


# Fonction setup à appeler pour charger l’extension
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
    # Vous pouvez changer le nom du rôle Staff ici si besoin
    await bot.add_cog(TicketCog(bot, staff_role_name="Staff"))
