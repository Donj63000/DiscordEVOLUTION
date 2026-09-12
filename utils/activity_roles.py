"""Reconcile temporary team roles against the durable activity roster."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

import discord

from utils.activity_data import ActivityError, PARIS, activity_end, utc
from utils.calendar_data import one_line, shorten

log = logging.getLogger(__name__)
ROLE_IO_TIMEOUT = 12.0


def team_role_name(record: dict) -> str:
    start = utc(datetime.fromisoformat(record.get("starts_at") or record["date_str"]))
    suffix = start.astimezone(PARIS).strftime(" %d/%m/%Y %H:%M")
    return "equipe " + shorten(one_line(record["titre"], 100), 100 - 7 - len(suffix)) + suffix


def role_needed(record: dict, now: datetime) -> bool:
    return not record.get("cancelled") and activity_end(record) > utc(now)


async def role_request(awaitable):
    return await asyncio.wait_for(awaitable, timeout=ROLE_IO_TIMEOUT)


class ActivityRoleManager:
    """Only stored IDs or unique, persisted staging names may be adopted or deleted."""

    def __init__(self, cog):
        self.cog = cog
        self._locks = {}

    def current(self, guild, key):
        return self.cog.events_for_guild(guild.id).get(key)

    @staticmethod
    def check_safe(role, guild):
        if role.is_default() or role.managed or role.permissions.value:
            raise ActivityError("Rôle d'équipe protégé ou doté de permissions : intervention du Staff requise.")
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or not role < me.top_role:
            raise ActivityError("Placer le rôle du bot au-dessus des équipes et autoriser « Gérer les rôles ».")

    async def update(self, guild, key, **fields):
        await self.cog._update_event_fields(guild, key, **fields)

    async def sync(self, guild, identifier) -> bool:
        key = str(identifier)
        async with self._locks.setdefault(key, asyncio.Lock()):
            try:
                return await self._sync(guild, key)
            except (ActivityError, discord.HTTPException, asyncio.TimeoutError) as exc:
                text = str(exc) if isinstance(exc, ActivityError) else (
                    "Synchronisation du rôle en attente : vérifier les permissions et la hiérarchie du bot."
                )
                log.warning("Activity role deferred event_id=%s error=%s", key, type(exc).__name__)
                try:
                    await self.update(guild, key, role_sync_pending=True, role_error=text)
                except Exception:
                    log.exception("Activity role error could not be persisted event_id=%s", key)
                return False

    async def _sync(self, guild, key):
        record = self.current(guild, key)
        if record is None:
            return False
        needed = role_needed(record, self.cog.now())
        role_id = int(record["role_id"]) if record.get("role_id") else None
        staging = record.get("role_staging_name")
        if not needed and not role_id and not staging:
            if record.get("role_sync_pending") or record.get("role_error"):
                await self.update(guild, key, role_sync_pending=False, role_error=None, role_member_ids=[])
            return True
        me = getattr(guild, "me", None)
        if me is None or not me.guild_permissions.manage_roles:
            raise ActivityError("Autoriser « Gérer les rôles » et placer le rôle du bot au-dessus des équipes.")
        role = guild.get_role(int(role_id)) if role_id else None
        staged = []
        if role is None or staging:
            roles = await role_request(guild.fetch_roles())
            role = next((item for item in roles if item.id == role_id), None)
            staged = [item for item in roles if staging and item.name == staging]
            if role is None and staged:
                role = min(staged, key=lambda item: item.id)
        if role is None and needed:
            if not staging:
                staging = f"evo-activity-{guild.id}-{key}-{uuid.uuid4().hex}"
                await self.update(guild, key, role_staging_name=staging, role_sync_pending=True)
            record = self.current(guild, key)
            if not role_needed(record, self.cog.now()):
                return True
            role = await role_request(guild.create_role(
                name=staging, permissions=discord.Permissions.none(),
                mentionable=False, hoist=False, reason=f"Équipe temporaire activité #{key}",
            ))
            log.debug("Activity staging role created event_id=%s role_id=%s", key, role.id)
        if role is None:
            await self.update(guild, key, role_id=None, role_member_ids=[], role_staging_name=None,
                              role_sync_pending=False, role_error=None)
            return True
        self.check_safe(role, guild)
        if record.get("role_id") != role.id:
            await self.update(guild, key, role_id=role.id, role_sync_pending=True)
        for extra in staged:
            if extra.id != role.id:
                self.check_safe(extra, guild)
                await role_request(extra.delete(reason=f"Doublon de préparation activité #{key}"))
        record = self.current(guild, key)
        if not role_needed(record, self.cog.now()):
            try:
                await role_request(role.delete(reason=f"Fin ou annulation de l'activité #{key}"))
            except discord.NotFound:
                pass
            await self.update(guild, key, role_id=None, role_member_ids=[], role_staging_name=None,
                              role_sync_pending=False, role_error=None)
            log.debug("Activity team role removed event_id=%s role_id=%s", key, role.id)
            return True
        name = team_role_name(record)
        if role.name != name or role.mentionable or role.hoist:
            await role_request(role.edit(
                name=name, mentionable=False, hoist=False, reason=f"Activité #{key} actualisée",
            ))
        desired = set(record.get("participants", []))
        known = set(record.get("role_member_ids", []))
        known.update(member.id for member in role.members)
        failed = set()
        for user_id in sorted(desired | known):
            try:
                member = guild.get_member(user_id)
                if member is None:
                    try:
                        member = await role_request(guild.fetch_member(user_id))
                    except discord.NotFound:
                        continue
                if user_id in desired:
                    if not any(item.id == role.id for item in member.roles):
                        await role_request(member.add_roles(
                            role, reason=f"Inscription activité #{key}", atomic=True,
                        ))
                else:
                    await role_request(member.remove_roles(
                        role, reason=f"Désinscription activité #{key}", atomic=True,
                    ))
            except (discord.HTTPException, asyncio.TimeoutError):
                failed.add(user_id)
                log.warning("Activity role member deferred event_id=%s user_id=%s", key, user_id)
        latest = self.current(guild, key)
        stale = set(latest.get("participants", [])) != desired or not role_needed(latest, self.cog.now())
        await self.update(
            guild, key, role_member_ids=sorted(desired | failed),
            role_sync_pending=bool(failed or stale),
            role_error="Certains rôles sont en attente de synchronisation." if failed else None,
        )
        log.debug("Activity role reconciled event_id=%s participants=%s retry=%s",
                  key, len(desired), bool(failed or stale))
        return not failed and not stale
