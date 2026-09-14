"""Collecte en lecture seule via les méthodes publiques de discord.py.

Le SDK reste dans le cog. Les objets injectés exposent les mêmes méthodes
publiques, ce qui permet de tester pagination, confidentialité et erreurs hors réseau.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
from typing import Any
from zoneinfo import ZoneInfo

from utils.enquete_core import (
    Alias, Config, EnqueteError, MARKER, Matcher, Record, ReportMeta, Window, Workspace,
    line, normalise, parse_target_id, snowflake_at, stamp,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cursor:
    id: int


def type_number(channel) -> int:
    return int(getattr(getattr(channel, "type", -1), "value", getattr(channel, "type", -1)))


def staff(member, cfg: Config) -> bool:
    if getattr(getattr(member, "guild_permissions", None), "administrator", False):
        return True
    roles = getattr(member, "roles", ())
    if cfg.role_ids:
        return any(r.id in cfg.role_ids for r in roles)
    return any(r.name == cfg.role_name for r in roles)


def names_of(member) -> list[str]:
    return list(dict.fromkeys(str(v) for v in (
        getattr(member, "name", None), getattr(member, "global_name", None),
        getattr(member, "nick", None), getattr(member, "display_name", None),
    ) if v))


def resolve_target(text: str, members: list) -> tuple[int, Any | None]:
    text = text.strip()
    if not text or len(text) > 100:
        raise EnqueteError("Indique un pseudo, une mention ou un identifiant Discord (100 caractères maximum).")
    user_id = parse_target_id(text)
    if user_id:
        # Pas de fetch_user : aucune recherche d'identité en dehors de ce serveur.
        return user_id, next((m for m in members if m.id == user_id), None)
    key = normalise(text)
    matches = [m for m in members if key in {normalise(n) for n in names_of(m)}]
    if len(matches) > 1:
        choices = " ; ".join(f"{line(m.display_name)} [{m.id}]" for m in matches[:8])
        raise EnqueteError("Pseudo ambigu. Utilise une mention ou l'ID exact : " + choices)
    if not matches:
        raise EnqueteError(
            "Aucun membre actuel ne correspond exactement. Utilise l'autocomplétion ou l'ID Discord ; "
            "pour un ancien membre, fournis son ID et ses anciens pseudos dans aliases."
        )
    return matches[0].id, matches[0]


def channel_label(channel) -> str:
    parent = getattr(channel, "parent", None)
    category = getattr(channel, "category", None) or getattr(parent, "category", None)
    names = [getattr(category, "name", ""), getattr(parent, "name", ""), getattr(channel, "name", "")]
    return " / ".join(str(n) for n in names if n)


async def retry_read(factory, *, attempts: int = 3):
    """Le SDK gère ses rate limits ; ce garde-fou traite les échecs transitoires restants."""
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(factory(), timeout=90)
        except Exception as exc:
            status = getattr(exc, "status", None)
            retryable = status == 429 or (isinstance(status, int) and status >= 500) or isinstance(exc, (OSError, TimeoutError))
            if not retryable or attempt + 1 >= attempts:
                raise
            retry_after = getattr(exc, "retry_after", None)
            delay = min(max(float(retry_after or 2**attempt), 0), 90)
            await asyncio.sleep(delay)
    raise RuntimeError("Boucle de reprise inaccessible")


async def as_list(iterator):
    return [value async for value in iterator]


class AccessScope:
    """La diffusion n'élargit pas l'accès : chaque lecteur doit pouvoir lire la source."""
    def __init__(self, guild, bot_member, requester_id: int, cfg: Config, destination: str,
                 *, current_channel_id: int | None = None):
        self.guild, self.bot_member, self.requester_id, self.cfg = guild, bot_member, requester_id, cfg
        if destination not in {"ici", "staff", "console", "les-deux"}:
            raise EnqueteError("Destination invalide.")
        ids = ([current_channel_id] if destination == "ici" else
               [cfg.staff_channel] if destination == "staff" else
               [cfg.console_channel] if destination == "console" else
               [cfg.staff_channel, cfg.console_channel])
        log.debug("Destination enquête mode=%s salons=%s", destination, ids)
        if not all(ids):
            if destination == "ici":
                raise EnqueteError("Lance /enquete dans un salon textuel privé du serveur.")
            raise EnqueteError(
                "Utilise destination:ici depuis un salon staff privé, ou configure "
                "ENQUETE_STAFF_CHANNEL_ID et/ou ENQUETE_CONSOLE_CHANNEL_ID "
                "(CHANNEL_CONSOLE_ID est accepté pour la console). Aucun salon n'est deviné par son nom."
            )
        self.destination_ids = list(dict.fromkeys(ids))
        self.members: list = []
        self.requester = None
        self.channels: dict[int, Any] = {}
        self.destinations: list = []
        self.readers: list = []
        self.target_id: int | None = None
        self.private_members: dict[tuple[int, int], bool] = {}

    async def refresh(self):
        fresh_roles = await retry_read(self.guild.fetch_roles)
        fingerprint = lambda roles: {(r.id, r.permissions.value) for r in roles}
        if fingerprint(fresh_roles) != fingerprint(self.guild.roles):
            raise EnqueteError("Le cache des rôles n'est pas à jour. Attends la synchronisation Discord puis réessaie.")
        # Une liste REST complète évite de se fier à un cache de membres incomplet.
        self.members = await retry_read(lambda: as_list(self.guild.fetch_members(limit=None)))
        self.requester = next((m for m in self.members if m.id == self.requester_id), None)
        self.bot_member = next((m for m in self.members if m.id == self.bot_member.id), self.bot_member)
        if self.requester is None or not staff(self.requester, self.cfg):
            raise EnqueteError("Le demandeur n'est plus autorisé à consulter ce dossier.")
        channels = await retry_read(self.guild.fetch_channels)
        self.channels = {c.id: c for c in channels}
        self.destinations = []
        for cid in self.destination_ids:
            channel = self.channels.get(cid)
            # Pas de salon d'annonces : il pourrait être suivi/crossposté ailleurs.
            if channel is None or type_number(channel) != 0:
                raise EnqueteError("La destination doit être un salon textuel ordinaire de ce serveur.")
            if channel.permissions_for(self.guild.default_role).view_channel:
                raise EnqueteError("Destination non confidentielle : @everyone peut la voir.")
            perms = channel.permissions_for(self.bot_member)
            if not all(getattr(perms, x, False) for x in ("view_channel", "read_message_history", "send_messages", "attach_files")):
                raise EnqueteError("Le bot doit pouvoir voir, lire, écrire et joindre des fichiers dans la destination.")
            if not channel.permissions_for(self.requester).view_channel:
                raise EnqueteError("Le demandeur ne peut pas voir la destination.")
            self.destinations.append(channel)
        readers = {}
        for member in self.members:
            if member.id == self.bot_member.id:
                continue
            if any(c.permissions_for(member).view_channel for c in self.destinations):
                if member.id == self.target_id:
                    raise EnqueteError(
                        "La personne recherchée peut lire la destination. Choisis un salon plus restreint ; "
                        "un administrateur ne peut pas être exclu par le bot."
                    )
                if not staff(member, self.cfg):
                    raise EnqueteError(
                        "Publication refusée : la destination est visible par un compte sans autorisation staff. "
                        "Vérifie les rôles et les permissions individuelles."
                    )
                readers[member.id] = member
        self.readers = list(readers.values())
        self.private_members.clear()

    def bind_target(self, target_id: int):
        self.target_id = target_id
        if any(m.id == target_id for m in self.readers):
            raise EnqueteError("La cible peut voir la destination. Utilise un salon staff plus restreint.")

    async def can_read(self, channel) -> tuple[bool, str]:
        if getattr(getattr(channel, "guild", None), "id", None) != self.guild.id:
            return False, "autre serveur"
        parent_id = getattr(channel, "parent_id", None)
        parent = self.channels.get(parent_id) if parent_id else None
        category_id = getattr(channel, "category_id", None) if parent_id is None else getattr(parent, "category_id", None)
        if self.cfg.excluded.intersection({channel.id, parent_id, category_id}):
            return False, "exclusion configurée (salon, parent ou catégorie)"
        nsfw_source = parent or channel
        if getattr(nsfw_source, "is_nsfw", lambda: False)() and not all(
            getattr(c, "is_nsfw", lambda: False)() for c in self.destinations
        ):
            return False, "espace à limite d'âge ; destination non classée de même"
        # Les permissions de fils sont héritées. Leur appartenance privée se vérifie séparément.
        permission_source = parent or channel
        for member in [self.bot_member, self.requester, *self.readers]:
            if member is None:
                return False, "membre indisponible"
            try:
                permissions = permission_source.permissions_for(member)
            except Exception:
                return False, "permissions impossibles à vérifier"
            if not permissions.view_channel or not permissions.read_message_history:
                return False, "accès non partagé par le bot, le demandeur et tous les destinataires"
            if type_number(channel) in {2, 13} and not getattr(permissions, "connect", False):
                return False, "permission Connecter requise pour le chat vocal"
            if type_number(channel) == 12 and not getattr(permissions, "manage_threads", False):
                key = (channel.id, member.id)
                if key not in self.private_members:
                    try:
                        await retry_read(lambda: channel.fetch_member(member.id))
                        self.private_members[key] = True
                    except Exception:
                        self.private_members[key] = False
                if not self.private_members[key]:
                    return False, "appartenance au fil privé non vérifiable pour tous les destinataires"
        return True, ""

    async def revalidate(self, sources: dict[int, Any]):
        await self.refresh()
        for cid, previous in sources.items():
            current = self.channels.get(cid)
            if current is None and type_number(previous) in {10, 11, 12}:
                # Le cache ne contient pas nécessairement les fils archivés.
                current = await retry_read(lambda: self.guild.fetch_channel(cid))
            if current is None:
                raise EnqueteError("Un salon source a disparu. Publication refusée ; relance un nouveau dossier.")
            allowed, _ = await self.can_read(current)
            if not allowed:
                raise EnqueteError("Les droits d'un salon source ont changé. Publication refusée pour éviter une fuite.")


def identity_snapshot(member, target_id: int) -> dict:
    if member is None:
        return {
            "Identifiant fourni": target_id,
            "Présence actuelle": "absent de la liste des membres ; appartenance historique à vérifier dans les sources",
            "Identité globale": "non recherchée hors du serveur",
        }
    return {
        "Identifiant Discord": target_id,
        "Nom de compte actuel": member.name,
        "Nom global actuel": getattr(member, "global_name", None) or "non défini",
        "Surnom actuel sur le serveur": getattr(member, "nick", None) or "non défini",
        "Compte bot": "oui" if member.bot else "non",
        "Création du compte Discord (pas l'âge de la personne)": stamp(member.created_at),
        "Dernière arrivée connue dans ce serveur": stamp(member.joined_at),
        "Rôles actuellement attribués": "; ".join(f"{r.name} [{r.id}]" for r in member.roles if not r.is_default()),
        "Timeout actuellement visible": stamp(getattr(member, "timed_out_until", None)),
        "Validation d'entrée en attente": str(getattr(member, "pending", False)),
        "Date de début du boost actuellement visible": stamp(getattr(member, "premium_since", None)),
    }


def embed_text(message) -> str:
    parts = []
    for embed in getattr(message, "embeds", []):
        for value in (getattr(embed, "title", None), getattr(embed, "description", None),
                      getattr(getattr(embed, "author", None), "name", None),
                      getattr(getattr(embed, "footer", None), "text", None)):
            if value:
                parts.append(str(value))
        for field in getattr(embed, "fields", []):
            parts.append(f"{field.name}\n{field.value}")
    return "\n".join(parts)


def internal_snapshot(message, own_bot_id: int) -> bool:
    if message.author.id != own_bot_id:
        return False
    content = message.content or ""
    if content.startswith(MARKER) or content.lstrip().startswith("==="):
        return True
    return any(
        str(a.filename).startswith("enquete_") or str(a.filename).endswith("_data.json")
        for a in getattr(message, "attachments", [])
    )


def to_record(message, guild_id: int, cid: int) -> Record:
    if getattr(getattr(message, "guild", None), "id", None) != guild_id or message.channel.id != cid:
        raise EnqueteError("Une réponse Discord ne correspond pas au serveur/salon demandé ; collecte arrêtée.")
    embeds = embed_text(message)
    attachments = [
        {"name": a.filename, "size": a.size, "url": a.url, "type": getattr(a, "content_type", None)}
        for a in getattr(message, "attachments", [])
    ]
    stickers = [s.name for s in getattr(message, "stickers", [])]
    poll = getattr(message, "poll", None)
    poll_text = ""
    if poll is not None:
        question = getattr(getattr(poll, "question", None), "text", getattr(poll, "question", ""))
        poll_text = str(question) + "\n" + "\n".join(
            f"{getattr(a, 'text', '')} : {getattr(a, 'vote_count', '?')} votes"
            for a in getattr(poll, "answers", [])
        )
    reference = getattr(message, "reference", None)
    resolved = getattr(reference, "resolved", None)
    # Ne pas récupérer l'auteur d'une référence située dans un autre espace.
    same_source = getattr(reference, "channel_id", None) == cid
    resolved_author = getattr(getattr(resolved, "author", None), "id", None) if same_source else None
    return Record(
        mid=message.id, cid=cid, aid=message.author.id, name=getattr(message.author, "display_name", message.author.name),
        created=message.created_at.astimezone(timezone.utc).isoformat(), content=message.content or "",
        edited=message.edited_at.astimezone(timezone.utc).isoformat() if message.edited_at else None,
        mentions=list(getattr(message, "raw_mentions", [])),
        reply_id=getattr(reference, "message_id", None), reply_channel=getattr(reference, "channel_id", None),
        reply_author=resolved_author,
        extra={
            "names": names_of(message.author), "embed_text": embeds, "attachments": attachments, "stickers": stickers,
            "search_text": "\n".join([embeds, poll_text, *[a["name"] for a in attachments], *stickers]),
            "poll": poll_text, "pinned": bool(getattr(message, "pinned", False)),
            "webhook_id": getattr(message, "webhook_id", None),
            "bot": bool(getattr(message.author, "bot", False)),
            "forwarded": bool(getattr(message, "message_snapshots", [])),
            "reactions": [{"emoji": str(r.emoji), "count": int(r.count)} for r in getattr(message, "reactions", [])],
        },
    )


class Collector:
    def __init__(self, bot, scope: AccessScope, work: Workspace, meta: ReportMeta, cfg: Config,
                 *, reaction_types: tuple = (), progress=None):
        self.bot, self.scope, self.work, self.meta, self.cfg = bot, scope, work, meta, cfg
        self.reaction_types = reaction_types
        self.progress = progress
        self.sources: dict[int, Any] = {}
        self.seen_channels: set[int] = set()
        self.visited_messages = 0
        self.skipped_internal = 0
        self.reaction_errors = 0
        self.thread_count = 0
        self.last_progress = 0.0

    async def notify(self, stage: str):
        now = time.monotonic()
        if self.progress and now - self.last_progress >= 30:
            self.last_progress = now
            await self.progress(stage, self.visited_messages)

    async def add_channel(self, channel):
        if channel.id in self.seen_channels:
            return
        self.seen_channels.add(channel.id)
        allowed, detail = await self.scope.can_read(channel)
        kind = "fil" if type_number(channel) in {10, 11, 12} else "salon"
        if not allowed:
            self.work.channel(channel.id, "Espace exclu", kind, "EXCLU", detail)
            return
        if not hasattr(channel, "history"):
            self.work.channel(channel.id, channel_label(channel), "conteneur", "CONTENEUR",
                              "messages présents dans les fils, pas directement dans le conteneur")
            return
        self.sources[channel.id] = channel
        self.work.channel(channel.id, channel_label(channel), kind, "EN_ATTENTE",
                          title=str(getattr(channel, "name", "")) if kind == "fil" else "")

    async def discover(self):
        for channel in self.scope.channels.values():
            if type_number(channel) != 4:  # pas les catégories
                await self.add_channel(channel)
        try:
            active = await retry_read(self.scope.guild.active_threads)
            for thread in active:
                if self.thread_count >= self.cfg.max_threads:
                    self.meta.partial = True
                    self.meta.notes.append("Limite globale de découverte des fils atteinte ; certains fils ne sont pas listés.")
                    break
                if thread.id not in self.seen_channels:
                    self.thread_count += 1
                    await self.add_channel(thread)
        except Exception as exc:
            self.meta.partial = True
            self.meta.notes.append(f"Liste des fils actifs incomplète ({type(exc).__name__}).")
        for parent in self.scope.channels.values():
            if not hasattr(parent, "archived_threads"):
                continue
            allowed, _ = await self.scope.can_read(parent)
            if not allowed:
                continue
            variants = [{}]
            if type_number(parent) in {0, 5}:
                manage = getattr(parent.permissions_for(self.scope.bot_member), "manage_threads", False)
                variants.append({"private": True, "joined": not manage})
                if not manage:
                    self.meta.notes.append(
                        f"Parent {parent.id} : seuls les fils privés archivés déjà rejoints par le bot sont découvrables."
                    )
            for variant in variants:
                if self.thread_count >= self.cfg.max_threads:
                    self.meta.partial = True
                    self.meta.notes.append("Découverte des archives interrompue : limite ENQUETE_MAX_THREADS.")
                    self.work.commit()
                    return
                try:
                    # Ne PAS borner par date d'archivage : elle ne correspond pas à la date des messages.
                    async for thread in parent.archived_threads(limit=None, **variant):
                        if thread.id not in self.seen_channels:
                            if self.thread_count >= self.cfg.max_threads:
                                self.meta.partial = True
                                self.meta.notes.append("Limite de fils atteinte pendant la pagination des archives.")
                                self.work.commit()
                                return
                            self.thread_count += 1
                            await self.add_channel(thread)
                        await self.notify("découverte des fils")
                except Exception as exc:
                    self.meta.partial = True
                    self.meta.notes.append(
                        f"Archives du parent {parent.id}, privé={bool(variant)} : liste incomplète ({type(exc).__name__})."
                    )
            await self.notify("découverte des fils")
        self.work.commit()

    async def target_reactions(self, message) -> list[str]:
        result = []
        for reaction in getattr(message, "reactions", []):
            for name, reaction_type in self.reaction_types:
                count = getattr(reaction, "burst_count" if name == "burst" else "normal_count",
                                reaction.count if name == "normal" else 0)
                if not count:
                    continue
                try:
                    # Les utilisateurs sont triés par ID : une seule entrée après cible-1 suffit.
                    users = await retry_read(lambda: as_list(reaction.users(
                        limit=1, after=Cursor(self.meta.target_id - 1), type=reaction_type,
                    )))
                    if any(user.id == self.meta.target_id for user in users):
                        result.append(f"{reaction.emoji} ({name})")
                except Exception:
                    self.reaction_errors += 1
                    self.meta.partial = True
        return result

    async def scan(self):
        queue = deque((channel, snowflake_at(self.meta.window.end)) for channel in self.sources.values())
        while queue:
            if self.cfg.max_messages and self.visited_messages >= self.cfg.max_messages:
                self.meta.partial = True
                self.meta.notes.append("Collecte interrompue : ENQUETE_MAX_MESSAGES atteint.")
                break
            if self.work.size >= self.cfg.disk_bytes // 2:
                # Réserve la seconde moitié pour la sélection et les exports.
                self.meta.partial = True
                self.meta.notes.append("Collecte interrompue : budget de stockage temporaire atteint.")
                break
            channel, before = queue.popleft()
            self.work.status(channel.id, "LECTURE")
            limit = min(100, self.cfg.max_messages - self.visited_messages) if self.cfg.max_messages else 100
            try:
                page = await retry_read(lambda: as_list(channel.history(
                    limit=limit, before=Cursor(before), oldest_first=False,
                )))
                if not page:
                    self.work.status(channel.id, "LU")
                    continue
                page.sort(key=lambda m: m.id, reverse=True)
                if any(m.id >= before for m in page):
                    raise EnqueteError("Curseur de pagination non décroissant.")
                complete = len(page) < limit
                for message in page:
                    self.visited_messages += 1
                    if message.created_at < self.meta.window.start:
                        complete = True
                        continue
                    if not self.meta.window.contains(message.created_at):
                        continue
                    if internal_snapshot(message, self.scope.bot_member.id):
                        self.skipped_internal += 1
                        continue
                    record = to_record(message, self.meta.guild_id, channel.id)
                    # Enregistrer d'abord le message : un timeout sur ses réactions ne le perd pas.
                    self.work.add(record)
                    if self.meta.deep_reactions:
                        record.extra["target_reactions"] = await self.target_reactions(message)
                        self.work.db.execute("UPDATE messages SET extra=? WHERE mid=?",
                                             (json.dumps(record.extra, ensure_ascii=False), record.mid))
                if complete:
                    self.work.status(channel.id, "LU")
                else:
                    queue.append((channel, min(m.id for m in page)))
            except EnqueteError:
                raise
            except Exception as exc:
                self.meta.partial = True
                self.work.status(channel.id, "ERREUR", f"lecture interrompue : {type(exc).__name__}")
            self.work.commit()
            await self.notify("lecture des messages")
            await asyncio.sleep(0)
        self.work.db.execute(
            "UPDATE channels SET status='PARTIEL',detail='collecte arrêtée avant la fin de cette pagination' "
            "WHERE status IN ('LECTURE','EN_ATTENTE')"
        )
        self.work.commit()

    @staticmethod
    def audit_value(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (list, tuple)):
            return [Collector.audit_value(v) for v in value]
        if hasattr(value, "id"):
            return {"id": value.id, "nom": getattr(value, "name", str(value))}
        if value is None or isinstance(value, (str, int, bool, float)):
            return value
        return str(value)

    async def audits(self):
        everyone = [self.scope.bot_member, self.scope.requester, *self.scope.readers]
        if not self.cfg.audit_limit or not all(
            getattr(m.guild_permissions, "view_audit_log", False) for m in everyone
        ):
            self.meta.notes.append("Journal d'audit non exporté : option désactivée ou droit Voir les logs non partagé.")
            return
        try:
            async for i, entry in async_enumerate(self.scope.guild.audit_logs(
                limit=self.cfg.audit_limit + 1, before=self.meta.window.end,
                after=self.meta.window.start, oldest_first=False,
            )):
                if i >= self.cfg.audit_limit:
                    self.meta.partial = True
                    self.meta.notes.append("Journal d'audit tronqué au plafond ENQUETE_AUDIT_LIMIT (entrées du serveur).")
                    break
                if not self.meta.window.contains(entry.created_at):
                    continue
                subject_id = getattr(getattr(entry, "target", None), "id", None)
                actor_id = getattr(getattr(entry, "user", None), "id", None)
                if self.meta.target_id not in {subject_id, actor_id}:
                    continue
                payload = {
                    "action_Discord": getattr(entry.action, "name", str(entry.action)),
                    "acteur_ID": actor_id, "objet_ou_compte_cible_ID": subject_id,
                    "motif_enregistré": entry.reason or "non renseigné",
                    "attribution": "la cible est l'acteur" if actor_id == self.meta.target_id else "la cible est l'objet de l'action",
                }
                for side in ("before", "after"):
                    diff = getattr(entry, side, None)
                    if diff:
                        values = {}
                        for key, value in diff:
                            if key in {"nick", "roles", "timed_out_until", "communication_disabled_until", "mute", "deaf"}:
                                values[key] = self.audit_value(value)
                                if key == "nick" and value and subject_id == self.meta.target_id:
                                    self.meta.aliases.append(Alias(str(value), "surnom présent dans un journal d'audit daté"))
                        if values:
                            payload["avant" if side == "before" else "après"] = values
                self.work.event(f"discord-audit:{entry.id}", "Journal d'audit Discord",
                                entry.created_at.astimezone(timezone.utc).isoformat(), payload)
                if i % 100 == 0:
                    self.work.commit()
                    await asyncio.sleep(0)
        except Exception as exc:
            self.meta.partial = True
            self.meta.notes.append(f"Lecture du journal d'audit interrompue ({type(exc).__name__}).")
        self.work.commit()

    async def legacy(self):
        data = getattr(self.bot.get_cog("StatsCog"), "stats_data", {})
        logs = data.get("logs", {}) if isinstance(data, dict) else {}
        ignored = CounterLike()
        for kind in ("messages_created", "messages_edited", "messages_deleted", "reactions", "voice"):
            events = list(logs.get(kind, [])) if isinstance(logs, dict) else []
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    continue
                uid = event.get("author_id", event.get("user_id", event.get("member_id")))
                if str(uid) != str(self.meta.target_id):
                    continue
                raw_cids = ([event.get("old_channel_id"), event.get("new_channel_id")] if kind == "voice"
                            else [event.get("channel_id")])
                try:
                    cids = [int(cid) for cid in raw_cids if cid]
                except (TypeError, ValueError):
                    ignored.add("salon invalide")
                    continue
                if not cids:
                    ignored.add("serveur indéterminable")
                    continue
                channels = [self.sources.get(cid) or self.scope.channels.get(cid) for cid in cids]
                allowed = True
                for channel in channels:
                    if channel is None or not (await self.scope.can_read(channel))[0]:
                        allowed = False
                        break
                if not allowed:
                    ignored.add("source hors périmètre ou supprimée")
                    continue
                # Ces sources de traces doivent elles aussi être revalidées avant diffusion.
                self.sources.update({c.id: c for c in channels})
                try:
                    created = datetime.fromisoformat(str(event.get("timestamp", "")))
                except ValueError:
                    ignored.add("date invalide")
                    continue
                if created.tzinfo is None:
                    if not self.cfg.legacy_timezone:
                        ignored.add("ancien horodatage sans fuseau déclaré")
                        continue
                    zone = ZoneInfo(self.cfg.legacy_timezone)
                    first = created.replace(tzinfo=zone, fold=0)
                    second = created.replace(tzinfo=zone, fold=1)
                    if (first.utcoffset() != second.utcoffset() or
                            first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != created):
                        ignored.add("ancien horaire ambigu ou inexistant lors d'un changement d'heure")
                        continue
                    created = first
                if not self.meta.window.contains(created):
                    continue
                keys = {
                    "message_id", "channel_id", "old_channel_id", "new_channel_id", "author_id", "user_id",
                    "member_id", "type", "emoji", "content", "old_content", "new_content",
                }
                payload = {key: event[key] for key in keys if key in event}
                payload["limite"] = (
                    "Trace déjà conservée par StatsCog, pas un historique exhaustif ; "
                    "texte absent ou déjà tronqué possible ; horodatage ancien interprété selon la configuration."
                )
                digest = hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode()).hexdigest()[:20]
                self.work.event(f"stats:{kind}:{digest}", "StatsCog / " + kind,
                                created.astimezone(timezone.utc).isoformat(), payload)
                if index % 100 == 0:
                    await asyncio.sleep(0)
        self.meta.notes.append(
            "StatsCog : seules les traces rattachables à des salons autorisés sont relues ; "
            "les compteurs globaux, la présence et les durées supposées ne sont pas repris."
        )
        self.meta.notes.extend(f"Traces StatsCog non incluses ({reason}) : {count}" for reason, count in ignored.items())
        if self.cfg.legacy_guild == self.meta.guild_id:
            warnings = getattr(self.bot.get_cog("ModerationCog"), "warnings", {})
            if isinstance(warnings, dict) and str(self.meta.target_id) in warnings:
                self.meta.identity["Avertissements de l'ancienne base (non datés)"] = warnings[str(self.meta.target_id)]
            players = getattr(self.bot.get_cog("PlayersCog"), "persos_data", {})
            record = players.get(str(self.meta.target_id), {}) if isinstance(players, dict) else {}
            if isinstance(record, dict):
                values = [record.get("main"), *(record.get("mules", []) if isinstance(record.get("mules"), list) else [])]
                for value in values:
                    if isinstance(value, str) and 2 <= len(value) <= 100:
                        self.meta.aliases.append(Alias(value, "personnage déclaré dans PlayersCog ; base affectée à ce serveur"))
            self.meta.notes.append(
                "L'administrateur a explicitement affecté les anciennes bases joueurs/avertissements à ce serveur ; "
                "leur provenance historique n'est pas vérifiable automatiquement."
            )
        else:
            self.meta.notes.append(
                "Anciennes bases joueurs/avertissements sans guild_id non importées. "
                "ENQUETE_LEGACY_DATA_GUILD_ID doit être renseigné après vérification de leur provenance."
            )
        self.work.commit()

    async def collect(self):
        try:
            async with asyncio.timeout(self.cfg.max_seconds):
                await self.discover()
                await self.scan()
                await self.audits()
                await self.legacy()
        except TimeoutError:
            self.meta.partial = True
            self.meta.notes.append("Délai maximal de collecte atteint. Ce rapport est partiel.")
        finally:
            self.work.db.execute(
                "UPDATE channels SET status='PARTIEL',detail='collecte interrompue' "
                "WHERE status IN ('LECTURE','EN_ATTENTE')"
            )
            self.work.commit()
        self.meta.notes.append(f"Sauvegardes internes / rapports du bot exclus de la recherche : {self.skipped_internal}.")
        if self.reaction_errors:
            self.meta.notes.append(f"Vérifications de réactions non abouties : {self.reaction_errors}.")
        self.meta.aliases.extend(self.work.observed_names(self.meta.target_id))
        matcher = Matcher(self.meta.aliases, approximate=self.meta.approximate)
        if len({normalise(a.value) for a in self.meta.aliases}) > 128:
            self.meta.partial = True
            self.meta.notes.append("Plus de 128 noms/alias distincts : seuls les 128 premiers sont recherchés.")
        self.meta.aliases = list(matcher.aliases.values())
        for member in self.scope.members:
            if member.id == self.meta.target_id:
                continue
            for name in names_of(member):
                if normalise(name) in matcher.aliases:
                    self.meta.collisions.append(
                        f"le nom « {name} » correspond aussi au membre actuel ID {member.id}"
                    )
        await self.work.select(self.meta.target_id, matcher, context=self.meta.context,
                               context_minutes=self.cfg.context_minutes)
        self.work.commit()


async def async_enumerate(iterator):
    index = 0
    async for value in iterator:
        yield index, value
        index += 1


class CounterLike(dict):
    def add(self, key):
        self[key] = self.get(key, 0) + 1
