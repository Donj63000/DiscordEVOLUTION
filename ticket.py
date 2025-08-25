import discord
from discord.ext import commands
import asyncio
import datetime

open_tickets = set()

class TicketView(discord.ui.View):
    def __init__(self, author: discord.User, staff_role_name: str):
        super().__init__(timeout=None)
        self.author = author
        self.staff_role_name = staff_role_name
        self.taken_by = None

    @discord.ui.button(label="Prendre en charge ✅", style=discord.ButtonStyle.primary)
    async def take_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ Cette action ne peut pas être réalisée en DM.", ephemeral=True)
        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send("❌ Vous ne disposez pas des autorisations requises (rôle 'Staff') pour prendre en charge ce ticket.", ephemeral=True)
        if self.taken_by is None:
            self.taken_by = interaction.user
            message = interaction.message
            if not message or not message.embeds:
                return await interaction.followup.send("⚠️ Impossible de mettre à jour ce ticket car l'embed original est introuvable.", ephemeral=True)
            embed = message.embeds[0]
            statut_field_index = None
            for index, field in enumerate(embed.fields):
                if field.name.lower() == "statut":
                    statut_field_index = index
                    break
            if statut_field_index is not None:
                embed.set_field_at(statut_field_index, name="Statut", value="En cours 🔄", inline=embed.fields[statut_field_index].inline)
            else:
                embed.add_field(name="Statut", value="En cours 🔄", inline=True)
            embed.add_field(name="Pris en charge par", value=interaction.user.display_name, inline=False)
            embed.color = discord.Color.gold()
            button.disabled = True
            await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
            await interaction.followup.send("Le ticket a été pris en charge avec succès. 🛠️", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Ce ticket est déjà en cours de traitement par un autre membre du staff.", ephemeral=True)

    @discord.ui.button(label="Résolu ✅", style=discord.ButtonStyle.success)
    async def mark_resolved(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not interaction.guild:
            return await interaction.followup.send("❌ Cette action ne peut pas être réalisée en DM.", ephemeral=True)
        staff_role = discord.utils.get(interaction.guild.roles, name=self.staff_role_name)
        if staff_role is None or staff_role not in interaction.user.roles:
            return await interaction.followup.send("❌ Vous n'êtes pas autorisé à modifier le statut de ce ticket (rôle 'Staff' requis).", ephemeral=True)
        if self.taken_by is None:
            return await interaction.followup.send("⚠️ Veuillez d'abord prendre en charge le ticket avant de le marquer comme résolu.", ephemeral=True)
        message = interaction.message
        if not message or not message.embeds:
            return await interaction.followup.send("⚠️ Impossible de mettre à jour ce ticket car l'embed original est introuvable.", ephemeral=True)
        embed = message.embeds[0]
        statut_field_index = None
        for index, field in enumerate(embed.fields):
            if field.name.lower() == "statut":
                statut_field_index = index
                break
        if statut_field_index is not None:
            embed.set_field_at(statut_field_index, name="Statut", value="Résolu ✅", inline=embed.fields[statut_field_index].inline)
        else:
            embed.add_field(name="Statut", value="Résolu ✅", inline=True)
        embed.color = discord.Color.green()
        for child in self.children:
            child.disabled = True
        open_tickets.discard(self.author.id)
        await interaction.followup.edit_message(message_id=interaction.message.id, embed=embed, view=self)
        try:
            await self.author.send(f"Votre ticket a été résolu par le staff : **{self.taken_by.display_name}**.\nNous vous remercions pour votre patience.")
        except discord.Forbidden:
            pass
        await interaction.followup.send(f"Le ticket de {self.author.mention} a été marqué comme résolu. ✅", ephemeral=True)
        self.stop()

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot, staff_role_name: str = "Staff"):
        self.bot = bot
        self.staff_role_name = staff_role_name

    @commands.command(name="ticket")
    async def create_ticket(self, ctx: commands.Context):
        print(f"DEBUG: Commande !ticket appelée par {ctx.author} (ID: {ctx.author.id}).")
        if ctx.guild is None:
            return
        user = ctx.author
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            print("DEBUG: Échec de la suppression du message de commande.")
        if user.id in open_tickets:
            try:
                await user.send("Vous avez déjà un ticket en cours. Merci de patienter jusqu'à sa résolution avant d'en ouvrir un autre.")
            except discord.Forbidden:
                pass
            return
        open_tickets.add(user.id)
        try:
            await user.send("Bonjour ! Vous avez ouvert un ticket de support.\nVeuillez décrire votre problème ou votre demande. *(Vous disposez de 5 minutes pour répondre.)*")
        except discord.Forbidden:
            open_tickets.discard(user.id)
            await ctx.send(f"{user.mention}, impossible de créer le ticket car vous bloquez les messages privés.")
            return
        def check_dm(m: discord.Message):
            return m.author == user and isinstance(m.channel, discord.DMChannel)
        try:
            dm_message = await self.bot.wait_for('message', timeout=300.0, check=check_dm)
        except asyncio.TimeoutError:
            open_tickets.discard(user.id)
            try:
                await user.send("Temps écoulé. Votre ticket a été annulé.")
            except discord.Forbidden:
                pass
            return
        ticket_content = dm_message.content
        embed = discord.Embed(title="🎫 Nouveau Ticket", color=discord.Color.blurple(), timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Demandeur", value=user.display_name, inline=True)
        embed.add_field(name="Statut", value="En attente ⌛", inline=True)
        embed.add_field(name="Contenu du ticket", value=ticket_content, inline=False)
        ticket_channel = discord.utils.get(ctx.guild.text_channels, name="🎟️ ticket 🎟️")
        if ticket_channel is None:
            open_tickets.discard(user.id)
            try:
                await user.send("❌ Le ticket n'a pu être créé car le salon `#🎟️ ticket 🎟️` est introuvable.")
            except discord.Forbidden:
                pass
            return
        view = TicketView(user, self.staff_role_name)
        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        mention_staff = staff_role.mention if staff_role else "**[Staff non trouvé]**"
        await ticket_channel.send(content=f"{mention_staff}, un nouveau ticket a été ouvert !", embed=embed, view=view)
        try:
            await user.send("Votre ticket a été envoyé au staff avec succès. Vous serez recontacté une fois qu'il sera pris en charge.")
        except discord.Forbidden:
            pass
        print(f"DEBUG: Ticket créé par {user} et envoyé dans #{ticket_channel.name}.")

    @commands.command(name="staff")
    async def staff_list(self, ctx: commands.Context):
        if not self.bot.intents.members:
            return await ctx.send("❌ L'intent 'members' n'est pas activé. Veuillez l'activer dans votre code et sur le portail Discord (Server Members Intent).")
        all_fetched_members = []
        try:
            async for member in ctx.guild.fetch_members(limit=None):
                all_fetched_members.append(member)
        except discord.HTTPException as e:
            return await ctx.send(f"Erreur lors de la récupération des membres : {e}")
        staff_role = discord.utils.get(ctx.guild.roles, name=self.staff_role_name)
        if staff_role is None:
            return await ctx.send(f"Le rôle '{self.staff_role_name}' est introuvable sur ce serveur.")
        members_with_staff_role = [m for m in all_fetched_members if staff_role in m.roles]
        if not members_with_staff_role:
            return await ctx.send("Aucun membre ne détient le rôle 'Staff'.")
        lines = [f"- {member.mention} (ID: {member.id})" for member in members_with_staff_role]
        staff_list_str = "\n".join(lines)
        embed = discord.Embed(title="Liste des membres du Staff", description=staff_list_str, color=discord.Color.blue())
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot, staff_role_name="Staff"))
