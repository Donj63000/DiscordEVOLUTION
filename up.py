import os
import json
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime
from collections import defaultdict

CHECK_INTERVAL_HOURS = 168  # 1 semaine
VOTE_DURATION_SECONDS = 300  # 5 minutes
STAFF_ROLE_NAME = "Staff"
VALID_MEMBER_ROLE_NAME = "Membre validé d'Evolution"
INVITE_ROLE_NAME = "Invité"
VETERAN_ROLE_NAME = "Vétéran"
STAFF_CHANNEL_NAME = "📊 Général-staff 📊"
CONSOLE_CHANNEL_NAME = "console"  # <-- canal console, identique à job.py
BOTUP_TAG = "===BOTUP==="         # <-- marqueur spécial pour retrouver le JSON
MESSAGE_THRESHOLD = 20
JOINED_THRESHOLD_DAYS = 6 * 30
PROMOTIONS_FILE = "promotions_data.json"


class UpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_count = defaultdict(int)

        # On stocke la data dans self.promotions_data
        self.promotions_data = {}

        # On va charger la data plus tard (au load du cog), via load_promotions_data()
        # On démarre le loop
        self.check_up_status.start()

    def cog_unload(self):
        self.check_up_status.cancel()

    # =========================
    #  Chargement / Sauvegarde
    # =========================

    async def cog_load(self):
        """Méthode appelée après l'ajout du cog. On charge nos data depuis le canal console (ou depuis le fichier)."""
        await self.load_promotions_data()

    async def load_promotions_data(self):
        """
        Tente de lire le JSON promotions_data depuis le canal console,
        via un message contenant BOTUP_TAG. Sinon, fallback sur le fichier local.
        """
        # 1) Cherche dans le canal console un message avec "===BOTUP===" et du JSON
        console_channel = None
        for guild in self.bot.guilds:
            c = discord.utils.get(guild.text_channels, name=CONSOLE_CHANNEL_NAME)
            if c:
                console_channel = c
                break

        if console_channel:
            # On lit l'historique à la recherche de la mention BOTUP_TAG
            async for msg in console_channel.history(limit=1000, oldest_first=False):
                if msg.author == self.bot.user and BOTUP_TAG in msg.content:
                    # On tente d'extraire le JSON entre ```json\n et \n```
                    try:
                        start_idx = msg.content.index("```json\n") + len("```json\n")
                        end_idx = msg.content.rindex("\n```")
                        raw_json = msg.content[start_idx:end_idx]
                        data_loaded = json.loads(raw_json)
                        self.promotions_data = data_loaded
                        print(f"[UpCog] Data rechargée depuis le canal {CONSOLE_CHANNEL_NAME} !")
                        break
                    except Exception as e:
                        print(f"[UpCog] Erreur parsing {CONSOLE_CHANNEL_NAME} data:", e)
                        pass

        # 2) Si on n'a rien chargé depuis le console_channel et que c'est toujours vide, fallback sur le fichier local
        if not self.promotions_data and os.path.exists(PROMOTIONS_FILE):
            try:
                with open(PROMOTIONS_FILE, "r", encoding="utf-8") as f:
                    self.promotions_data = json.load(f)
                print("[UpCog] Data rechargée depuis le fichier local (promotions_data.json).")
            except:
                self.promotions_data = {}
        else:
            if not self.promotions_data:
                print(f"[UpCog] Aucune data existante ({CONSOLE_CHANNEL_NAME} + local). promotions_data reste vide.")

    def save_promotions_data_local(self):
        """Sauvegarde locale classique."""
        with open(PROMOTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.promotions_data, f, indent=4, ensure_ascii=False)

    async def dump_data_to_console(self):
        """
        Envoie (ou met à jour) le JSON complet dans le canal console, en utilisant BOTUP_TAG.
        On le fait à chaque fois qu'on modifie promotions_data.
        """
        console_channel = None
        for guild in self.bot.guilds:
            c = discord.utils.get(guild.text_channels, name=CONSOLE_CHANNEL_NAME)
            if c:
                console_channel = c
                break
        if not console_channel:
            print(f"[UpCog] Pas de canal {CONSOLE_CHANNEL_NAME}, impossible d'y sauvegarder la data.")
            return

        # On supprime éventuellement les anciens messages BOTUP pour éviter la confusion
        # (optionnel, on peut vouloir garder l'historique)
        async for old_msg in console_channel.history(limit=1000, oldest_first=False):
            if old_msg.author == self.bot.user and BOTUP_TAG in old_msg.content:
                try:
                    await old_msg.delete()
                except:
                    pass
                break

        # On génère la string JSON
        data_str = json.dumps(self.promotions_data, indent=4, ensure_ascii=False)
        # Envoi
        if len(data_str) < 1900:
            content_msg = f"{BOTUP_TAG}\n```json\n{data_str}\n```"
            await console_channel.send(content_msg)
        else:
            # Envoi en fichier
            content_msg = f"{BOTUP_TAG} (fichier car trop long)"
            filename = "promotions_data.json"
            with open(filename, "w", encoding="utf-8") as tmp:
                tmp.write(data_str)
            await console_channel.send(content_msg, file=discord.File(filename))

    # ========================
    #   Fonctions d'Accès
    # ========================

    def get_promotion_status(self, user_id: int):
        return self.promotions_data.get(str(user_id), {}).get("status")

    def set_promotion_status(self, user_id: int, status: str):
        user_id_str = str(user_id)
        if user_id_str not in self.promotions_data:
            self.promotions_data[user_id_str] = {}
        self.promotions_data[user_id_str]["status"] = status

    # ========================
    #   Tâche programmée
    # ========================

    @tasks.loop(hours=CHECK_INTERVAL_HOURS)
    async def check_up_status(self):
        """
        Tâche qui se réveille toutes les 168h (une semaine), scanne l'historique,
        puis vérifie les membres éligibles, et lance des votes si nécessaire.
        """
        await self.bot.wait_until_ready()

        # On (re)charge la data depuis le console, au cas où un reload a eu lieu
        # (Optionnel si on veut forcer la synchro avant chaque check)
        # => Décommenter si nécessaire.
        # await self.load_promotions_data()

        await self.scan_entire_history()
        await self.verifier_membres_eligibles()

        # Après modifications éventuelles, on sauvegarde
        self.save_promotions_data_local()
        await self.dump_data_to_console()

    # ========================
    #   Scan de l'historique
    # ========================

    async def scan_entire_history(self):
        """
        Parcourt tous les channels texte de toutes les guilds, et
        incrémente un compteur de messages par utilisateur.
        """
        self.user_message_count.clear()
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    async for msg in channel.history(limit=None, oldest_first=True):
                        if not msg.author.bot:
                            self.user_message_count[str(msg.author.id)] += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def verifier_membres_eligibles(self):
        """
        Vérifie pour chaque membre s'il remplit les conditions (JOINED_THRESHOLD_DAYS, MESSAGE_THRESHOLD, etc.)
        et si oui, lance un vote de promotion si pas déjà voté/refusé/promu.
        """
        for guild in self.bot.guilds:
            staff_channel = discord.utils.get(guild.text_channels, name=STAFF_CHANNEL_NAME)
            if not staff_channel:
                continue

            for member in guild.members:
                if member.bot:
                    continue

                join_days = 0
                if member.joined_at:
                    join_days = (discord.utils.utcnow() - member.joined_at).days

                has_valid_role = any(r.name == VALID_MEMBER_ROLE_NAME for r in member.roles)
                has_invite_role = any(r.name == INVITE_ROLE_NAME for r in member.roles)
                msg_count = self.user_message_count.get(str(member.id), 0)
                status = self.get_promotion_status(member.id)

                # On ignore ceux qui sont déjà promus ou refusés ou en cours de vote
                if status in ["promoted", "refused", "voting"]:
                    continue

                # S'il avait été reporté (postponed) => on retente
                # On autorise un second vote en status=postponed ou None
                if status not in ["postponed", None]:
                    continue

                # Conditions pour être éligible
                if (
                    join_days >= JOINED_THRESHOLD_DAYS
                    and has_valid_role
                    and not has_invite_role
                    and msg_count >= MESSAGE_THRESHOLD
                    and not any(r.name == VETERAN_ROLE_NAME for r in member.roles)
                ):
                    await self.lancer_vote(staff_channel, member)

        # Après modifications, on resauvegarde
        self.save_promotions_data_local()
        await self.dump_data_to_console()

    async def lancer_vote(self, staff_channel: discord.TextChannel, member: discord.Member):
        mention_staff_role = discord.utils.get(member.guild.roles, name=STAFF_ROLE_NAME)
        mention_text = mention_staff_role.mention if mention_staff_role else "@Staff"

        embed = discord.Embed(
            title="Vote Promotion",
            description=(
                f"{mention_text} — Promotion de {member.mention} en **{VETERAN_ROLE_NAME}** ?\n"
                f"Réagissez ✅ ou ❌ (durée: {VOTE_DURATION_SECONDS // 60} min)."
            ),
            color=discord.Color.blue()
        )

        vote_message = await staff_channel.send(embed=embed)
        await vote_message.add_reaction("✅")
        await vote_message.add_reaction("❌")

        self.set_promotion_status(member.id, "voting")
        self.save_promotions_data_local()
        await self.dump_data_to_console()

        await asyncio.sleep(VOTE_DURATION_SECONDS)

        try:
            vote_message = await vote_message.channel.fetch_message(vote_message.id)
        except discord.NotFound:
            await staff_channel.send(f"Le message de vote pour {member.mention} a disparu, vote reporté.")
            self.set_promotion_status(member.id, "postponed")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
            return

        yes_count = 0
        no_count = 0
        for reaction in vote_message.reactions:
            if str(reaction.emoji) == "✅":
                yes_count = reaction.count - 1
            elif str(reaction.emoji) == "❌":
                no_count = reaction.count - 1

        total_votes = yes_count + no_count

        if total_votes == 0:
            await staff_channel.send(f"Aucun vote exprimé pour {member.mention}, proposition reportée à la semaine prochaine.")
            self.set_promotion_status(member.id, "postponed")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
            return

        # Si un seul NON => refus
        if no_count >= 1:
            await staff_channel.send(f"Promotion refusée pour {member.mention}. (Un ❌ suffit à annuler la promotion)")
            self.set_promotion_status(member.id, "refused")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
            return

        # Sinon => promotion
        await self.promouvoir_veteran(staff_channel, member)

    async def promouvoir_veteran(self, staff_channel: discord.TextChannel, member: discord.Member):
        veteran_role = discord.utils.get(member.guild.roles, name=VETERAN_ROLE_NAME)
        if not veteran_role:
            await staff_channel.send("Rôle 'Vétéran' introuvable, impossible de promouvoir.")
            self.set_promotion_status(member.id, "refused")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
            return

        try:
            await member.add_roles(veteran_role)
            await staff_channel.send(f"{member.mention} promu(e) **{VETERAN_ROLE_NAME}**.")
            self.set_promotion_status(member.id, "promoted")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
        except discord.Forbidden:
            await staff_channel.send(f"Permissions insuffisantes pour promouvoir {member.display_name}.")
            self.set_promotion_status(member.id, "refused")
            self.save_promotions_data_local()
            await self.dump_data_to_console()
        except discord.HTTPException as e:
            await staff_channel.send(f"Erreur promotion {member.display_name} : {e}")
            self.set_promotion_status(member.id, "refused")
            self.save_promotions_data_local()
            await self.dump_data_to_console()

async def setup(bot: commands.Bot):
    await bot.add_cog(UpCog(bot))
