#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import discord
import json
import logging
import os
import unicodedata
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel
from utils.console_json_store import ConsoleJSONSnapshotStore

INVITES_ROLE_NAME = "Invites"
VALIDATED_ROLE_NAME = "Membre valide d Evolution"
GENERAL_CHANNEL_NAME = "📄 Général 📄"
RECRUITMENT_CHANNEL_NAME = "📌 Recrutement 📌"
WELCOME_CHANNEL_NAME = "𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞"
TIMEOUT_RESPONSE = 300.0
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "welcome_data.json")
WELCOME_IMAGE_PATH = os.path.join(BASE_DIR, "welcome1.png")
WELCOME_MARKER = "===WELCOME==="
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
log = logging.getLogger(__name__)


class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.already_welcomed: set[int] = set()
        self.pending_welcomes: dict[int, dict] = {}
        self.console_message_id: int | None = None
        self._init_task: asyncio.Task | None = None
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=WELCOME_MARKER,
            filename="welcome_data.json",
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="WELCOME_HISTORY_LIMIT",
        )
        self.load_welcomed_data()

    async def cog_load(self):
        if hasattr(self.bot, "wait_until_ready"):
            if self._init_task is None or self._init_task.done():
                self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()

    async def _post_ready_init(self):
        await self.bot.wait_until_ready()
        await self._load_from_console()

    def _serialize_state(self) -> dict:
        return {
            "already_welcomed": sorted(self.already_welcomed),
            "pending_welcomes": {str(k): dict(v) for k, v in self.pending_welcomes.items()},
        }

    async def _load_from_console(self):
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)
        if not isinstance(payload, dict):
            return
        ids = payload.get("already_welcomed", [])
        pending = payload.get("pending_welcomes", {})
        if isinstance(ids, list):
            self.already_welcomed = {int(x) for x in ids}
        if isinstance(pending, dict):
            cleaned: dict[int, dict] = {}
            for key, value in pending.items():
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    cleaned[uid] = value
            self.pending_welcomes = cleaned
        self.console_message_id = getattr(message, "id", None)

    async def _persist_state(self):
        self.save_welcomed_data()
        message = await self.store.save(self._serialize_state(), current_message_id=self.console_message_id)
        if message is not None:
            self.console_message_id = message.id

    def load_welcomed_data(self):
        if os.path.isfile(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    self.already_welcomed = set(int(x) for x in raw)
                    self.pending_welcomes = {}
                    return
                if isinstance(raw, dict):
                    ids = raw.get("already_welcomed", [])
                    pending = raw.get("pending_welcomes", {})
                    self.already_welcomed = set(int(x) for x in ids)
                    self.pending_welcomes = {
                        int(k): v for k, v in pending.items() if isinstance(v, dict)
                    }
            except Exception as e:
                print(f"[Welcome] Erreur chargement {DATA_FILE}: {e}")

    def save_welcomed_data(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self._serialize_state(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Welcome] Erreur sauvegarde {DATA_FILE}: {e}")

    def _normalize_reply(self, value: str) -> str:
        text = (value or "").strip().lower()
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        cleaned = []
        for ch in text:
            if ch.isalnum() or ch.isspace():
                cleaned.append(ch)
            else:
                cleaned.append(" ")
        return " ".join("".join(cleaned).split())

    def _is_yes(self, value: str) -> bool:
        normalized = self._normalize_reply(value)
        if not normalized:
            return False
        if normalized.startswith("oui"):
            return True
        return normalized in {"ok", "okay", "ouais", "ouai", "dac", "daccord", "d accord"}

    def _resolve_status(self, value: str):
        normalized = self._normalize_reply(value)
        if normalized.startswith("membre"):
            return "membre"
        if normalized.startswith("inv"):
            return "invite"
        return None

    def _is_onboarded(self, member: discord.Member) -> bool:
        return self._member_has_role(member, INVITES_ROLE_NAME) or self._member_has_role(member, VALIDATED_ROLE_NAME)

    def _member_has_role(self, member: discord.Member, expected_name: str) -> bool:
        target = self._normalize_reply(expected_name)
        return any(self._normalize_reply(role.name) == target for role in member.roles)

    def _find_role(self, guild: discord.Guild, expected_name: str):
        target = self._normalize_reply(expected_name)
        for role in guild.roles:
            if self._normalize_reply(role.name) == target:
                return role
        return None

    def _find_member_for_user(self, user_id: int) -> discord.Member | None:
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                return member
        return None

    def _mark_welcomed(self, user_id: int) -> None:
        if user_id in self.already_welcomed:
            return
        self.already_welcomed.add(user_id)
        self.save_welcomed_data()

    def _status_prompt(self) -> str:
        return (
            "**Parfait !** Maintenant, dis-moi : tu es **membre** de la guilde ou juste **invite** sur le serveur ?\n\n"
            "*(Reponds par `membre` ou `invite`.)*"
        )

    def _pseudo_prompt(self) -> str:
        return "**Super nouvelle !** J'ai juste besoin d'une petite info : quel est ton pseudo exact sur Dofus ?"

    def _recruiter_prompt(self) -> str:
        return (
            "Derniere etape : qui t'a invite a nous rejoindre ? (Pseudo Discord ou Dofus)\n\n"
            "Si tu ne te souviens plus, reponds simplement `non`."
        )

    async def _send_welcome_intro(self, member: discord.Member, dm_channel: discord.DMChannel) -> None:
        description = (
            "Nous sommes super contents de t'accueillir parmi nous.\n\n"
            "Avant de commencer, prends quelques instants pour parcourir notre reglement; "
            "on prefere que tout se passe dans la bonne ambiance.\n\n"
            "D'ailleurs, l'as-tu lu et accepte ?\n\n"
            "Pour confirmer, reponds simplement par **oui**."
        )
        embed = discord.Embed(
            title=f"Bienvenue dans Evolution, {member.display_name}!",
            description=description,
            color=discord.Color.green(),
        )
        file = discord.File(WELCOME_IMAGE_PATH, filename="welcome1.png")
        embed.set_image(url="attachment://welcome1.png")
        await dm_channel.send(embed=embed, file=file)

    async def _start_welcome_flow(self, member: discord.Member, dm_channel: discord.DMChannel | None = None) -> None:
        channel = dm_channel or await member.create_dm()
        await self._send_welcome_intro(member, channel)
        self.pending_welcomes[member.id] = {"stage": "reglement"}
        await self._persist_state()
        log.debug("Welcome flow started for %s.", member.id)

    async def _handle_invite_status(self, member: discord.Member, dm_channel: discord.DMChannel) -> None:
        guests_role = self._find_role(member.guild, INVITES_ROLE_NAME)
        if guests_role:
            try:
                await member.add_roles(guests_role)
                await dm_channel.send(
                    "Pas de souci ! Je t'ai attribue le role **Invites**. "
                    "Profite du serveur et n'hesite pas a discuter avec nous. "
                    "Et si tu veux rejoindre la guilde plus tard, fais signe au staff."
                )
                log.debug("Invite role added for %s.", member.id)
            except Exception as exc:
                log.debug("Unable to add invite role for %s: %s", member.id, exc, exc_info=True)
        else:
            await dm_channel.send("Le role 'Invites' n'existe pas encore. Peux-tu prevenir un admin ?")
        self.pending_welcomes.pop(member.id, None)
        self._mark_welcomed(member.id)
        await self._persist_state()

    async def _finalize_member_registration(
        self,
        member: discord.Member,
        dm_channel: discord.DMChannel,
        dofus_pseudo: str,
        recruiter_pseudo: str,
    ) -> None:
        recruitment_date = discord.utils.utcnow().strftime("%d/%m/%Y")
        validated_role = self._find_role(member.guild, VALIDATED_ROLE_NAME)
        try:
            await member.edit(nick=dofus_pseudo)
            log.debug("Nickname updated for %s.", member.id)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.debug("Unable to rename %s: %s", member.id, exc, exc_info=True)

        if validated_role:
            try:
                await member.add_roles(validated_role)
                log.debug("Validated role added for %s.", member.id)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.debug("Unable to add validated role for %s: %s", member.id, exc, exc_info=True)
        else:
            await dm_channel.send("Le role **Membre valide d'Evolution** est introuvable. Signale-le a un admin.")

        try:
            await dm_channel.send(
                f"Genial, {dofus_pseudo}! Te voila membre officiel de la guilde Evolution. "
                "Bienvenue a toi et profite bien du serveur !"
            )
        except discord.Forbidden:
            pass

        players_cog = self.bot.get_cog("PlayersCog")
        if players_cog:
            await players_cog.auto_register_member(
                discord_id=member.id,
                discord_display_name=member.display_name,
                dofus_pseudo=dofus_pseudo,
            )
            log.debug("PlayersCog auto registration done for %s.", member.id)
        else:
            log.debug("PlayersCog missing during auto registration for %s.", member.id)

        general_channel = resolve_text_channel(
            member.guild,
            id_env="GENERAL_CHANNEL_ID",
            name_env="GENERAL_CHANNEL_NAME",
            default_name=GENERAL_CHANNEL_NAME,
        )
        if general_channel:
            annonce_msg_general = (
                "Nouvelle recrue en approche !\n\n"
                f"Faites un triomphe a {member.mention}, alias **{dofus_pseudo}** sur Dofus, "
                "qui rejoint officiellement nos rangs !\n"
                "Un grand bienvenue de la part de toute la guilde !"
            )
            await general_channel.send(annonce_msg_general)
        else:
            log.debug("General channel not found for announcement.")

        recruitment_channel = resolve_text_channel(
            member.guild,
            id_env="RECRUITMENT_CHANNEL_ID",
            name_env="RECRUITMENT_CHANNEL_NAME",
            default_name=RECRUITMENT_CHANNEL_NAME,
        )
        if recruitment_channel:
            if recruiter_pseudo.lower() == "non":
                recruiter_info = "n'a pas indique de recruteur"
            else:
                recruiter_info = f"a ete invite par **{recruiter_pseudo}**"
            await recruitment_channel.send(
                f"Le joueur **{dofus_pseudo}** a rejoint la guilde le **{recruitment_date}** "
                f"et {recruiter_info}."
            )
        else:
            log.debug("Recruitment channel not found for announcement.")

    async def _handle_welcome_message(self, member: discord.Member, message: discord.Message) -> None:
        content = message.content or ""
        if self._is_onboarded(member):
            self.pending_welcomes.pop(member.id, None)
            self._mark_welcomed(member.id)
            await self._persist_state()
            return

        state = self.pending_welcomes.get(member.id)
        if state is None:
            status = self._resolve_status(content)
            if self._is_yes(content):
                self.pending_welcomes[member.id] = {"stage": "status"}
                await self._persist_state()
                await message.channel.send(self._status_prompt())
                return
            if status == "invite":
                await self._handle_invite_status(member, message.channel)
                return
            if status == "membre":
                self.pending_welcomes[member.id] = {"stage": "pseudo"}
                await self._persist_state()
                await message.channel.send(self._pseudo_prompt())
                return
            await self._start_welcome_flow(member, message.channel)
            return

        stage = state.get("stage")
        if stage == "reglement":
            if not self._is_yes(content):
                await message.channel.send("Pour continuer, reponds simplement par **oui**.")
                return
            state["stage"] = "status"
            await self._persist_state()
            await message.channel.send(self._status_prompt())
            return
        if stage == "status":
            status = self._resolve_status(content)
            if not status:
                await message.channel.send("Reponds par `membre` ou `invite` pour continuer.")
                return
            if status == "invite":
                await self._handle_invite_status(member, message.channel)
                return
            state["stage"] = "pseudo"
            await self._persist_state()
            await message.channel.send(self._pseudo_prompt())
            return
        if stage == "pseudo":
            dofus_pseudo = content.strip()
            if not dofus_pseudo:
                await message.channel.send("J'ai besoin de ton pseudo Dofus exact pour continuer.")
                return
            state["dofus_pseudo"] = dofus_pseudo
            state["stage"] = "recruiter"
            await self._persist_state()
            await message.channel.send(self._recruiter_prompt())
            return
        if stage == "recruiter":
            recruiter_pseudo = content.strip() or "non"
            dofus_pseudo = state.get("dofus_pseudo") or member.display_name
            await self._finalize_member_registration(member, message.channel, dofus_pseudo, recruiter_pseudo)
            self.pending_welcomes.pop(member.id, None)
            self._mark_welcomed(member.id)
            await self._persist_state()
            return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        log.debug("Welcome join event for %s.", member.id)
        if member.bot:
            return
        if member.id in self.already_welcomed:
            return
        if self._is_onboarded(member):
            self._mark_welcomed(member.id)
            await self._persist_state()
            return
        if member.id in self.pending_welcomes:
            return
        try:
            await self._start_welcome_flow(member)
        except discord.Forbidden:
            await self.fallback_public_greeting(member)
        except Exception as exc:
            log.debug("Welcome DM failed for %s: %s", member.id, exc, exc_info=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        if message.content and message.content.strip().startswith("!"):
            return
        member = self._find_member_for_user(message.author.id)
        if not member:
            return
        if member.id in self.already_welcomed:
            return
        await self._handle_welcome_message(member, message)

    async def fallback_public_greeting(self, member: discord.Member):
        general_channel = resolve_text_channel(
            member.guild,
            id_env="GENERAL_CHANNEL_ID",
            name_env="GENERAL_CHANNEL_NAME",
            default_name=GENERAL_CHANNEL_NAME,
        )
        welcome_channel = resolve_text_channel(
            member.guild,
            id_env="WELCOME_CHANNEL_ID",
            name_env="WELCOME_CHANNEL_NAME",
            default_name=WELCOME_CHANNEL_NAME,
        )
        if general_channel:
            extra = f" Passe sur {welcome_channel.mention} pour plus d'informations." if welcome_channel else ""
            await general_channel.send(
                f"👋 {member.mention}, je n’ai pas pu t’envoyer de message privé ! "
                "Active tes MP pour finaliser l’accueil. "
                "En attendant, sois le/la bienvenu·e parmi nous ! 🎉" + extra
            )
        else:
            print(f"[DEBUG] Fallback impossible : canal #{GENERAL_CHANNEL_NAME} introuvable.")


async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
