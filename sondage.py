import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import random

# Émojis Unicode pour les choix (A, B, C, D, E...)
ALPHABET_EMOJIS = [
    "🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮",
    "🇯", "🇰", "🇱", "🇲", "🇳", "🇴", "🇵", "🇶", "🇷",
    "🇸", "🇹", "🇺", "🇻", "🇼", "🇽", "🇾", "🇿"
]

# Dictionnaire stockant temporairement nos sondages
# Clé : message_id
# Valeur : dict avec "title", "choices", "channel_id", "end_time", "author_id", etc.
POLL_STORAGE = {}


def random_pastel_color() -> int:
    """
    Génère une couleur pastel aléatoire sous forme d'entier (hex).
    """
    r = random.randint(128, 255)
    g = random.randint(128, 255)
    b = random.randint(128, 255)
    return (r << 16) + (g << 8) + b


def make_progress_bar(count: int, max_count: int, bar_length: int = 10) -> str:
    """
    Construit une barre de progression textuelle en fonction du nombre de votes.
    Exemple : "████░░░░░"
    """
    if max_count == 0:
        return "░" * bar_length
    fraction = count / max_count
    filled = int(round(fraction * bar_length))
    return "█" * filled + "░" * (bar_length - filled)


class SondageCog(commands.Cog):
    """
    Cog gérant les sondages avec un délai (JJ:HH:MM), un séparateur ';'
    et une publication automatique sur #annonces avec mention @everyone.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Tâche périodique pour vérifier la fin des sondages
        self.poll_watcher.start()

    def cog_unload(self):
        self.poll_watcher.cancel()

    @tasks.loop(seconds=20.0)
    async def poll_watcher(self):
        """
        Vérifie toutes les 20s si un sondage doit être clôturé.
        """
        now = datetime.utcnow()
        ended_polls = []

        for message_id, poll_data in list(POLL_STORAGE.items()):
            end_time = poll_data.get("end_time")
            if end_time and now >= end_time:
                ended_polls.append(message_id)

        # Clôture des sondages expirés
        for msg_id in ended_polls:
            await self.close_poll(msg_id)
            POLL_STORAGE.pop(msg_id, None)

    @commands.command(name="sondage")
    async def create_sondage(self, ctx: commands.Context, *, args: str = None):
        """
        Crée un sondage, l'envoie sur #annonces, mentionne @everyone.

        Usage :
          !sondage Titre ; Choix1 ; Choix2 ; ... ; temps=JJ:HH:MM

        Ex :
          !sondage Donjon ; Bworker ; Ougah ; temps=1:12:30
          => se fermera dans 1 jour, 12 heures et 30 minutes

        Si aucun temps= n'est fourni, le sondage n'expire pas automatiquement
        et doit être fermé par !close_sondage.
        """
        if not args:
            await ctx.send(
                "Utilisation : `!sondage <Titre> ; <Choix1> ; Choix2 ; ... ; temps=JJ:HH:MM`\n"
                "Exemple : `!sondage Sortie Donjon ; Bworker ; Ougah ; temps=1:12:30` (1j12h30min)"
            )
            return

        # Séparation du texte par ';'
        parts = [p.strip() for p in args.split(";")]

        # Chercher le paramètre temps=JJ:HH:MM
        delay_seconds = None
        for i, part in enumerate(parts):
            if part.lower().startswith("temps="):
                # Ex: "temps=1:12:30"
                try:
                    raw_time = part.split("=")[1].strip()  # "1:12:30"
                    d, h, m = raw_time.split(":")
                    days = int(d)
                    hours = int(h)
                    mins = int(m)
                    delay_seconds = days * 24 * 3600 + hours * 3600 + mins * 60
                    # On retire ce segment de la liste
                    parts.pop(i)
                except (ValueError, IndexError):
                    pass
                break

        if len(parts) < 2:
            await ctx.send(
                "Veuillez spécifier au moins un titre et un choix.\n"
                "Exemple : `!sondage Titre ; Choix1 ; Choix2`"
            )
            return

        title = parts[0]
        choices = parts[1:]

        # Vérification du nombre de choix
        max_choices = len(ALPHABET_EMOJIS)
        if len(choices) > max_choices:
            await ctx.send(f"Nombre de choix trop élevé (max = {max_choices}). Réduisez vos options.")
            return

        # Construction du texte
        description_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            description_lines.append(f"{emoji} **{choice}**")
        description_str = "\n".join(description_lines)

        # Couleur pastel aléatoire
        embed_color = random_pastel_color()

        # Embed du sondage
        embed = discord.Embed(
            title=f"📊 {title}",
            description=description_str,
            color=embed_color
        )
        embed.set_author(
            name=f"Sondage créé par {ctx.author.display_name}",
            icon_url=(ctx.author.display_avatar.url if ctx.author.display_avatar else None)
        )
        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2550/2550205.png")

        # Calcul de la fin du sondage
        end_time_val = None
        end_time_msg = "Aucune (clôture manuelle)."
        if delay_seconds is not None:
            end_time_val = datetime.utcnow() + timedelta(seconds=delay_seconds)
            end_time_msg = f"Fin prévue : {end_time_val.strftime('%d/%m/%Y %H:%M')}"

        embed.add_field(
            name="⏳ Fin du sondage",
            value=end_time_msg,
            inline=False
        )
        embed.set_footer(text=f"ID du message: (pour !close_sondage) {ctx.message.id}")

        # Récupérer le canal #annonces
        annonce_channel = discord.utils.get(ctx.guild.text_channels, name="annonces")
        if not annonce_channel:
            await ctx.send("Le canal #annonces est introuvable.")
            return

        # Poster le sondage avec @everyone
        sondage_message = await annonce_channel.send(
            "@everyone Nouveau sondage :",
            embed=embed
        )

        # Ajout des réactions
        for i in range(len(choices)):
            await sondage_message.add_reaction(ALPHABET_EMOJIS[i])

        # Suppression du message d'origine (optionnel)
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        # Stockage en mémoire
        POLL_STORAGE[sondage_message.id] = {
            "title": title,
            "choices": choices,
            "channel_id": annonce_channel.id,
            "author_id": ctx.author.id,
            "end_time": end_time_val,
        }

        if end_time_val:
            # Convertir delay_seconds en format J, H, M
            d = delay_seconds // (24 * 3600)
            rem = delay_seconds % (24 * 3600)
            h = rem // 3600
            rem = rem % 3600
            m = rem // 60
            await ctx.send(
                f"Sondage lancé: `{title}`\n"
                f"Fin estimée dans ~{d}j {h}h {m}m."
            )

    @commands.command(name="close_sondage")
    async def manual_close_poll(self, ctx: commands.Context, message_id: int = None):
        """
        Ferme manuellement le sondage correspondant au message_id donné.
        Ex: !close_sondage 1234567890
        """
        if not message_id:
            await ctx.send("Veuillez préciser l'ID du message. Ex: `!close_sondage 1234567890`")
            return

        if message_id not in POLL_STORAGE:
            await ctx.send("Aucun sondage trouvé pour cet ID.")
            return

        # Optionnel : vérifier l'auteur ou un rôle
        # poll_data = POLL_STORAGE[message_id]
        # if ctx.author.id != poll_data["author_id"] and not ctx.author.guild_permissions.administrator:
        #     return await ctx.send("Seul l'auteur ou un admin peut fermer ce sondage.")

        await self.close_poll(message_id)
        # Retirer du stockage
        POLL_STORAGE.pop(message_id, None)

    async def close_poll(self, message_id: int):
        """
        Récupère les infos, comptabilise les votes, publie les résultats,
        et modifie l'embed original pour le marquer '[Clôturé]'.
        """
        poll_data = POLL_STORAGE.get(message_id)
        if not poll_data:
            return

        channel_id = poll_data["channel_id"]
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        try:
            msg_sondage = await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        # Comptabiliser les votes
        choices = poll_data["choices"]
        title = poll_data["title"]
        vote_counts = [0] * len(choices)

        for reaction in msg_sondage.reactions:
            if reaction.emoji in ALPHABET_EMOJIS:
                idx = ALPHABET_EMOJIS.index(reaction.emoji)
                if idx < len(choices):
                    vote_counts[idx] = reaction.count - 1  # -1 : bot

        max_votes = max(vote_counts) if vote_counts else 1
        results_lines = []
        for i, choice in enumerate(choices):
            emoji = ALPHABET_EMOJIS[i]
            count = vote_counts[i]
            bar = make_progress_bar(count, max_votes)
            results_lines.append(f"{emoji} **{choice}** : {count} vote(s)\n    `{bar}`")

        results_str = "\n".join(results_lines)

        # Embed résultats
        embed = discord.Embed(
            title=f"Résultats du sondage : {title}",
            description=results_str,
            color=0xFEE75C
        )
        embed.set_footer(text="Le sondage est maintenant clôturé.")

        # Publier le résumé dans #annonces
        await channel.send(
            f"**Fin du sondage** : `{title}`\nVoici le récapitulatif :",
            embed=embed
        )

        # Marquer l'embed initial comme [Clôturé]
        closed_embed = msg_sondage.embeds[0].copy()
        closed_embed.title += " [Clôturé]"
        closed_embed.color = 0x2C2F33  # Gris foncé
        await msg_sondage.edit(embed=closed_embed)

        # (Optionnel) Supprimer le sondage original
        # await msg_sondage.delete()


def setup(bot: commands.Bot):
    bot.add_cog(SondageCog(bot))
