#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel

log = logging.getLogger(__name__)

FORMER_MEMBERS_MARKER = "===FORMER_MEMBERS==="
FORMER_MEMBERS_FILENAME = "former_members.json"
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
DEFAULT_STAFF_CHANNEL_NAME = os.getenv("STAFF_CHANNEL_NAME", "general-staff")
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", os.getenv("STAFF_ROLE_NAME", "Staff"))
CHECK_EMOJI = "\N{WHITE HEAVY CHECK MARK}"
CROSS_EMOJI = "\N{CROSS MARK}"
KICK_REASON = "Non respect de la regle de depart definitif"


class FormerMemberGuardCog(commands.Cog):
    """Tracks former members and alerts staff when they return."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.data: Dict[str, Any] = {"guilds": {}}
        self.console_message_id: Optional[int] = None
        self.pending_alerts: Dict[int, Tuple[int, int]] = {}
        self.invite_cache: Dict[int, Dict[str, Dict[str, Any]]] = {}
        self.initialized = False
        self._init_lock = asyncio.Lock()

    async def cog_load(self) -> None:
        self.bot.loop.create_task(self._post_ready_setup())

    async def _post_ready_setup(self) -> None:
        await self.bot.wait_until_ready()
        await self._ensure_initialized()
        await self._warm_invite_cache()

    async def _ensure_initialized(self) -> None:
        if self.initialized:
            return
        async with self._init_lock:
            if self.initialized:
                return
            wait_ready = getattr(self.bot, "wait_until_ready", None)
            if callable(wait_ready):
                await wait_ready()
            console_channel = await self._resolve_console_channel()
            if console_channel:
                data = await self._load_from_console(console_channel)
                if data:
                    self.data = data
            self._rebuild_pending_alerts()
            self.initialized = True
            log.debug("Former member guard initialized with %s guilds.", len(self.data.get("guilds", {})))

    async def _warm_invite_cache(self) -> None:
        for guild in getattr(self.bot, "guilds", []):
            await self._refresh_invite_cache(guild)

    async def _resolve_console_channel(
        self, guild: Optional[discord.Guild] = None
    ) -> Optional[discord.TextChannel]:
        candidates = []
        if guild:
            candidates.append(guild)
        for g in getattr(self.bot, "guilds", []):
            if g not in candidates:
                candidates.append(g)
        for g in candidates:
            channel = resolve_text_channel(
                g,
                id_env="CHANNEL_CONSOLE_ID",
                name_env="CHANNEL_CONSOLE",
                default_name=CONSOLE_CHANNEL_NAME,
            )
            if channel:
                return channel
        return None

    def _is_console_snapshot(self, message: discord.Message) -> bool:
        if message.author != self.bot.user:
            return False
        content = message.content or ""
        if FORMER_MEMBERS_MARKER in content:
            return True
        for attachment in getattr(message, "attachments", []) or []:
            if attachment.filename == FORMER_MEMBERS_FILENAME:
                return True
        return False

    async def _get_console_snapshot(
        self, console_channel: discord.TextChannel
    ) -> Optional[discord.Message]:
        if self.console_message_id:
            try:
                message = await console_channel.fetch_message(self.console_message_id)
                if self._is_console_snapshot(message):
                    return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self.console_message_id = None
        try:
            pinned = await console_channel.pins()
        except Exception:
            pinned = []
        for msg in pinned:
            if self._is_console_snapshot(msg):
                self.console_message_id = msg.id
                return msg
        async for msg in console_channel.history(limit=200):
            if self._is_console_snapshot(msg):
                self.console_message_id = msg.id
                return msg
        return None

    async def _load_from_console(self, console_channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
        candidates = []
        try:
            candidates.extend(await console_channel.pins())
        except Exception:
            pass
        async for msg in console_channel.history(limit=200):
            candidates.append(msg)
        best = None
        best_size = -1
        for msg in candidates:
            if not self._is_console_snapshot(msg):
                continue
            data = await self._extract_payload(msg)
            if data is None:
                continue
            guilds = data.get("guilds", {})
            size = sum(len(bucket.get("members", {})) for bucket in guilds.values())
            if size > best_size:
                best = data
                best_size = size
                self.console_message_id = msg.id
        if best:
            log.debug("Former member guard loaded %s entries from console.", best_size)
        return best

    async def _extract_payload(self, message: discord.Message) -> Optional[Dict[str, Any]]:
        for attachment in getattr(message, "attachments", []) or []:
            if attachment.filename != FORMER_MEMBERS_FILENAME:
                continue
            try:
                raw = await attachment.read()
                data = json.loads(raw.decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                log.debug("Failed to read former member data from attachment.", exc_info=True)
        content = message.content or ""
        if "```json" not in content:
            return None
        try:
            start = content.index("```json") + len("```json")
            if content[start] == "\n":
                start += 1
            end = content.rindex("```")
            raw_json = content[start:end].strip()
            if not raw_json:
                return None
            data = json.loads(raw_json)
            if isinstance(data, dict):
                return data
        except Exception:
            log.debug("Failed to parse former member data from console message.", exc_info=True)
        return None

    async def _persist_data(self, guild: Optional[discord.Guild] = None) -> None:
        console_channel = await self._resolve_console_channel(guild)
        if not console_channel:
            log.warning("Console channel not found; former member data not persisted.")
            return
        data_str = json.dumps(self.data, indent=4, ensure_ascii=False)
        message = await self._get_console_snapshot(console_channel)
        if len(data_str) < 1900:
            content = f"{FORMER_MEMBERS_MARKER}\n```json\n{data_str}\n```"
            if message:
                await message.edit(content=content)
            else:
                message = await console_channel.send(content)
                self.console_message_id = message.id
                try:
                    await message.pin()
                except Exception:
                    log.debug("Unable to pin former member data snapshot.", exc_info=True)
            return
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(data_str)
            if message:
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            message = await console_channel.send(
                f"{FORMER_MEMBERS_MARKER} (fichier)",
                file=discord.File(fp=path, filename=FORMER_MEMBERS_FILENAME),
            )
            self.console_message_id = message.id
            try:
                await message.pin()
            except Exception:
                log.debug("Unable to pin former member data file snapshot.", exc_info=True)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def _rebuild_pending_alerts(self) -> None:
        self.pending_alerts = {}
        for guild_id, bucket in self.data.get("guilds", {}).items():
            for user_id, record in bucket.get("members", {}).items():
                message_id = record.get("pending_message_id")
                if message_id:
                    self.pending_alerts[int(message_id)] = (int(guild_id), int(user_id))

    def _guild_bucket(self, guild_id: int) -> Dict[str, Any]:
        guilds = self.data.setdefault("guilds", {})
        bucket = guilds.setdefault(str(guild_id), {"members": {}})
        if "members" not in bucket:
            bucket["members"] = {}
        return bucket

    def _get_member_record(self, guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        bucket = self._guild_bucket(guild_id)
        return bucket["members"].get(str(user_id))

    def _set_member_record(self, guild_id: int, user_id: int, record: Dict[str, Any]) -> None:
        bucket = self._guild_bucket(guild_id)
        bucket["members"][str(user_id)] = record

    def _format_timestamp(self, now: Optional[datetime] = None) -> str:
        current = now or datetime.now(timezone.utc)
        local_now = current.astimezone()
        return local_now.strftime("%d/%m/%Y %H:%M")

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _role_names(self, member: discord.Member) -> list[str]:
        roles = []
        default_role = getattr(member.guild, "default_role", None)
        for role in getattr(member, "roles", []) or []:
            if role == default_role or getattr(role, "name", "") == "@everyone":
                continue
            roles.append(role.name)
        return roles

    def _staff_mention(self, guild: discord.Guild) -> str:
        staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        if staff_role:
            return staff_role.mention
        return "@Staff"

    def _inviter_label(self, guild: discord.Guild, info: Optional[Dict[str, Any]]) -> str:
        if not info:
            return "inconnu"
        inviter_id = info.get("inviter_id")
        inviter_name = info.get("inviter_name") or "inconnu"
        if inviter_id:
            member = guild.get_member(inviter_id)
            if member:
                return f"{member.display_name} ({member.mention})"
        return inviter_name

    def _is_staff_member(self, member: Optional[discord.Member]) -> bool:
        if member is None or getattr(member, "bot", False):
            return False
        staff_role = discord.utils.get(member.guild.roles, name=STAFF_ROLE_NAME)
        if staff_role:
            return staff_role in member.roles
        return True

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._ensure_initialized()
        roles = self._role_names(member)
        record = {
            "user_id": str(member.id),
            "name": member.display_name,
            "left_at": self._now_iso(),
            "left_roles": roles,
        }
        existing = self._get_member_record(member.guild.id, member.id)
        if existing:
            existing.update(record)
            record = existing
        self._set_member_record(member.guild.id, member.id, record)
        log.debug("Recorded former member %s in guild %s.", member.id, member.guild.id)
        await self._persist_data(member.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        await self._ensure_initialized()
        record = self._get_member_record(member.guild.id, member.id)
        if not record:
            return
        staff_channel = resolve_text_channel(
            member.guild,
            id_env="STAFF_CHANNEL_ID",
            name_env="STAFF_CHANNEL_NAME",
            default_name=DEFAULT_STAFF_CHANNEL_NAME,
        )
        if not staff_channel:
            log.warning("Staff channel not found; cannot alert on returning member %s.", member.id)
            return
        inviter_info = await self._resolve_inviter(member)
        roles = self._role_names(member)
        roles_text = ", ".join(roles) if roles else "aucun"
        joined_at = self._format_timestamp()
        inviter_text = self._inviter_label(member.guild, inviter_info)
        staff_mention = self._staff_mention(member.guild)
        message = (
            f"{staff_mention} Attention le joueur {member.display_name} ({member.mention}) "
            "etait deja present en guilde, il vient de revenir en contradiction avec la regle "
            "sur les departs definitifs.\n"
            f"Roles actuels: {roles_text}\n"
            f"Retour: {joined_at}\n"
            f"Invite par: {inviter_text}\n"
            "Voulez vous le garder sur le discord ?\n"
            f"Reactez avec {CHECK_EMOJI} pour garder ou {CROSS_EMOJI} pour exclure."
        )
        alert_message = await staff_channel.send(message)
        await alert_message.add_reaction(CHECK_EMOJI)
        await alert_message.add_reaction(CROSS_EMOJI)
        record.update(
            {
                "last_rejoin_at": self._now_iso(),
                "last_roles": roles,
                "last_inviter": inviter_info,
                "pending_message_id": alert_message.id,
                "alert_channel_id": staff_channel.id,
            }
        )
        self._set_member_record(member.guild.id, member.id, record)
        self.pending_alerts[alert_message.id] = (member.guild.id, member.id)
        log.debug("Sent returning member alert for %s in guild %s.", member.id, member.guild.id)
        await self._persist_data(member.guild)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.message_id not in self.pending_alerts:
            return
        if payload.user_id == getattr(self.bot.user, "id", None):
            return
        emoji = str(payload.emoji)
        if emoji not in {CHECK_EMOJI, CROSS_EMOJI}:
            return
        await self._ensure_initialized()
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        actor = guild.get_member(payload.user_id)
        if not self._is_staff_member(actor):
            return
        guild_id, target_id = self.pending_alerts[payload.message_id]
        record = self._get_member_record(guild_id, target_id) or {}
        record["decision_at"] = self._now_iso()
        record["decision_by"] = str(payload.user_id)
        record["pending_message_id"] = None
        if emoji == CHECK_EMOJI:
            record["decision"] = "kept"
            self._set_member_record(guild_id, target_id, record)
            self.pending_alerts.pop(payload.message_id, None)
            log.debug("Returning member %s kept by %s.", target_id, payload.user_id)
            await self._persist_data(guild)
            return
        target = guild.get_member(target_id)
        if target:
            try:
                await target.kick(reason=KICK_REASON)
                record["decision"] = "kicked"
                log.debug("Returning member %s kicked by %s.", target_id, payload.user_id)
            except discord.Forbidden:
                record["decision"] = "kick_failed"
                log.warning("Missing permissions to kick member %s.", target_id)
                await self._notify_kick_failure(guild, payload.channel_id, target_id)
            except discord.HTTPException as exc:
                record["decision"] = "kick_failed"
                log.warning("Kick failed for member %s: %s", target_id, exc)
                await self._notify_kick_failure(guild, payload.channel_id, target_id)
        else:
            record["decision"] = "not_found"
            log.debug("Member %s already left before kick.", target_id)
        self._set_member_record(guild_id, target_id, record)
        self.pending_alerts.pop(payload.message_id, None)
        await self._persist_data(guild)

    async def _notify_kick_failure(self, guild: discord.Guild, channel_id: Optional[int], target_id: int) -> None:
        if channel_id is None:
            return
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                f"Impossible d'exclure <@{target_id}>: permissions insuffisantes ou erreur Discord."
            )

    async def _refresh_invite_cache(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        invites = await self._fetch_invites(guild)
        self.invite_cache[guild.id] = invites
        return invites

    async def _fetch_invites(self, guild: discord.Guild) -> Dict[str, Dict[str, Any]]:
        invites = {}
        if not hasattr(guild, "invites"):
            return invites
        try:
            fetched = await guild.invites()
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.debug("Unable to fetch invites for guild %s: %s", guild.id, exc)
            return invites
        for invite in fetched:
            inviter = invite.inviter
            inviter_name = None
            inviter_id = None
            if inviter:
                inviter_id = getattr(inviter, "id", None)
                inviter_name = getattr(inviter, "display_name", None) or str(inviter)
            invites[invite.code] = {
                "uses": invite.uses or 0,
                "inviter_id": inviter_id,
                "inviter_name": inviter_name,
            }
        return invites

    async def _resolve_inviter(self, member: discord.Member) -> Optional[Dict[str, Any]]:
        guild = member.guild
        before = self.invite_cache.get(guild.id, {})
        after = await self._refresh_invite_cache(guild)
        if not after:
            return None
        best_code = None
        best_info = None
        best_delta = 0
        for code, info in after.items():
            previous = before.get(code, {})
            delta = (info.get("uses") or 0) - (previous.get("uses") or 0)
            if delta > best_delta:
                best_delta = delta
                best_code = code
                best_info = info
        if best_info is None:
            for code, info in after.items():
                if code not in before and (info.get("uses") or 0) > 0:
                    best_code = code
                    best_info = info
                    break
        if best_info is None:
            return None
        result = dict(best_info)
        result["code"] = best_code
        return result

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild_id = invite.guild.id
        cache = self.invite_cache.setdefault(guild_id, {})
        inviter = invite.inviter
        inviter_id = getattr(inviter, "id", None) if inviter else None
        inviter_name = getattr(inviter, "display_name", None) if inviter else None
        cache[invite.code] = {
            "uses": invite.uses or 0,
            "inviter_id": inviter_id,
            "inviter_name": inviter_name,
        }
        log.debug("Invite created cached for guild %s.", guild_id)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild_id = invite.guild.id
        cache = self.invite_cache.get(guild_id, {})
        cache.pop(invite.code, None)
        log.debug("Invite deleted removed from cache for guild %s.", guild_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FormerMemberGuardCog(bot))
