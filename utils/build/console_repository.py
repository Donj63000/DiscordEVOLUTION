"""Je conserve les builds uniquement dans les générations référencées de #console."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import re

import discord

from utils.evo_config import resolve_console_channel
from .models import Actor, Build, BuildError, Conflict, NotFound, canonical
from .repository import MemoryRepository, SNAPSHOT_KINDS, op_key, require_owner, share_hash

log = logging.getLogger(__name__)
MARKER = "===BOTEVOBUILD==="
ROOT_FILENAME = "evolution_build_root.json"
PART_FILENAME = "evolution_build.part"
MAX_BLOB_BYTES = 24 * 1024 * 1024
MAX_ROOT_BYTES = 4 * 1024 * 1024
PART_BYTES = 4 * 1024 * 1024
IO_TIMEOUT = 30
HASH = re.compile(r"[0-9a-f]{64}")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Clé JSON répétée")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"Nombre JSON invalide : {value}")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def _date(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Date sans fuseau horaire")
    return parsed


def _member_key(actor):
    return f"{actor.guild_id}:{actor.user_id}"


def _valid_int(value, minimum=1):
    return type(value) is int and value >= minimum


class ConsoleRepository:
    """Une racine vérifiée engage ensemble les données et les reçus d'un membre."""

    durable = True

    def __init__(self, bot, quota=20, *, channel=None, leadership=None):
        self.bot = bot
        self.quota = quota
        self.channel = channel
        self._injected_channel = channel is not None
        self._leadership = leadership
        self._leadership_channel = None
        self._gate = asyncio.Lock()
        self._open_lock = asyncio.Lock()
        self._root_message = None
        self._root = None
        self._members = {}
        self._snapshot_payloads = {}
        self._deleted_garbage = set()
        self._opened = False
        pending = getattr(bot, "_build_console_pending", None)
        if pending is None:
            pending = {}
            bot._build_console_pending = pending
        self._pending_roots = pending

    async def _io(self, awaitable):
        async with asyncio.timeout(IO_TIMEOUT):
            return await awaitable

    async def _check_leadership(self):
        check = self._leadership or getattr(self.bot, "ensure_build_leadership", None)
        if check is None:
            raise BuildError("Le verrou Build est indisponible : écritures suspendues.")
        verified = await self._io(check())
        self._leadership_channel = verified if (
            getattr(verified, "id", None) is not None and getattr(verified, "guild", None) is not None
        ) else None

    def _resolve_channel(self):
        if self._injected_channel:
            return self.channel
        if self._leadership_channel is not None:
            return self._leadership_channel
        lock_channel_id = getattr(self.bot, "_lock_channel_id", None)
        get_channel = getattr(self.bot, "get_channel", None)
        if lock_channel_id is not None and get_channel is not None:
            locked = get_channel(lock_channel_id)
            if locked is not None and getattr(locked, "guild", None) is not None:
                return locked
        channels = {
            candidate.id: candidate
            for guild in getattr(self.bot, "guilds", ())
            if (candidate := resolve_console_channel(guild)) is not None
        }
        if len(channels) != 1:
            raise BuildError("Configurer une console Discord unique pour Evolution Build.")
        return next(iter(channels.values()))

    def _owned(self, message):
        return getattr(message, "author", None) == self.bot.user

    def _module_message(self, message):
        return self._owned(message) and (
            (getattr(message, "content", "") or "").startswith(MARKER)
            or any(getattr(item, "filename", None) in {ROOT_FILENAME, PART_FILENAME}
                   for item in getattr(message, "attachments", ()))
        )

    def _file_limit(self):
        return min(PART_BYTES, getattr(self.channel.guild, "filesize_limit", PART_BYTES))

    async def _discover(self):
        pins = await self._io(self.channel.pins())
        messages = {message.id: message for message in pins if self._module_message(message)}
        async with asyncio.timeout(IO_TIMEOUT):
            async for message in self.channel.history(limit=None):
                if self._module_message(message):
                    messages[message.id] = message
        roots = [message for message in messages.values()
                 if (message.content or "").startswith(f"{MARKER} root ")]
        if len(roots) > 1:
            raise BuildError("Plusieurs racines Build existent dans #console : contrôle Staff requis.")
        if messages and not roots:
            raise BuildError("La racine Build a disparu ; restauration Staff requise, aucune base vide créée.")
        return roots[0] if roots else None

    async def open(self):
        async with self._open_lock:
            try:
                await self._check_leadership()
                channel = self._resolve_channel()
                if self.channel is not None and self.channel.id != channel.id:
                    raise BuildError("La console Build a changé : stockage suspendu.")
                self.channel = channel
                gates = getattr(self.bot, "_build_console_locks", None)
                if gates is None:
                    gates = {}
                    self.bot._build_console_locks = gates
                self._gate = gates.setdefault(channel.id, asyncio.Lock())
                async with self._gate:
                    root_message = await self._discover()
                    if root_message is None:
                        initial = {
                            "schema": 1, "revision": 0, "guild_id": channel.guild.id,
                            "members": {}, "snapshots": {}, "latest": {}, "garbage": [],
                        }
                        root_message = await self._create_root(initial)
                    if not root_message.pinned:
                        await self._io(root_message.pin(reason="Source de vérité Evolution Build"))
                    self._root_message = root_message
                    await self._refresh(force=True)
                    await self._check_leadership()
                    self._opened = True
                    await self._cleanup()
                log.debug("build console opened channel_id=%s revision=%s",
                          channel.id, self._root["revision"])
                return self
            except BuildError:
                self._opened = False
                raise
            except Exception as exc:
                self._opened = False
                log.debug("build console open failed type=%s", type(exc).__name__, exc_info=True)
                raise BuildError("Lecture ou épinglage Build impossible dans #console. Vérifie les permissions.") from None

    async def close(self):
        self._opened = False
        self._members.clear()
        self._snapshot_payloads.clear()

    async def _create_root(self, payload):
        content, file = self._root_content(payload)
        try:
            await self._check_leadership()
            try:
                kwargs = {"file": file} if file else {}
                message = await self._io(self.channel.send(
                    content, allowed_mentions=discord.AllowedMentions.none(), **kwargs,
                ))
            except Exception:
                message = await self._discover()
                if message is None or await self._read_root(message) != payload:
                    raise BuildError("Création de la racine Build incertaine : relecture nécessaire.") from None
            return message
        finally:
            if file:
                file.close()

    def _root_content(self, payload):
        raw = canonical(payload).replace("`", "\\u0060").encode("utf-8")
        if len(raw) > min(MAX_ROOT_BYTES, self._file_limit()):
            raise BuildError("L'index Build atteint la limite Discord ; écriture refusée sans perte.")
        header = f"{MARKER} root revision:{payload['revision']} sha256:{_sha(raw)}"
        content = f"{header}\n```json\n{raw.decode('utf-8')}\n```"
        if len(content.encode("utf-16-le")) // 2 <= 1950:
            return content, None
        return header, discord.File(io.BytesIO(raw), filename=ROOT_FILENAME)

    def _descriptor(self, value):
        if not isinstance(value, dict) or set(value) != {"sha256", "size", "parts"}:
            raise ValueError("Référence de snapshot invalide")
        if (not isinstance(value["sha256"], str) or not HASH.fullmatch(value["sha256"])
                or not _valid_int(value["size"]) or value["size"] > MAX_BLOB_BYTES
                or not isinstance(value["parts"], list) or not 1 <= len(value["parts"]) <= 1024):
            raise ValueError("Dimensions de snapshot invalides")
        ids = set()
        for part in value["parts"]:
            if (not isinstance(part, dict) or set(part) != {"id", "attachment_id", "sha256", "size"}
                    or not _valid_int(part["id"]) or part["id"] in ids
                    or not _valid_int(part["attachment_id"])
                    or not _valid_int(part["size"]) or part["size"] > PART_BYTES
                    or not isinstance(part["sha256"], str) or not HASH.fullmatch(part["sha256"])):
                raise ValueError("Fragment invalide")
            ids.add(part["id"])
        if sum(part["size"] for part in value["parts"]) != value["size"]:
            raise ValueError("Taille du snapshot incohérente")

    def _validate_root(self, payload):
        if (not isinstance(payload, dict)
                or set(payload) != {"schema", "revision", "guild_id", "members", "snapshots", "latest", "garbage"}
                or type(payload["schema"]) is not int or payload["schema"] != 1
                or payload["guild_id"] != self.channel.guild.id
                or not _valid_int(payload["revision"], 0)):
            raise ValueError("Schéma de racine invalide")
        for field in ("members", "snapshots", "latest"):
            if not isinstance(payload[field], dict):
                raise ValueError("Index invalide")
        for key, descriptor in payload["members"].items():
            guild, owner = key.split(":")
            actor = Actor(guild_id=int(guild), user_id=int(owner))
            if _member_key(actor) != key or actor.guild_id != self.channel.guild.id:
                raise ValueError("Propriétaire de snapshot invalide")
            self._descriptor(descriptor)
        for kind, versions in payload["snapshots"].items():
            if kind not in SNAPSHOT_KINDS or not isinstance(versions, dict):
                raise ValueError("Famille d'instantanés invalide")
            for identifier, descriptor in versions.items():
                if not HASH.fullmatch(identifier):
                    raise ValueError("Identifiant d'instantané invalide")
                self._descriptor(descriptor)
        if any(kind not in payload["snapshots"] or identifier not in payload["snapshots"][kind]
               for kind, identifier in payload["latest"].items()):
            raise ValueError("Dernier instantané absent")
        garbage = payload["garbage"]
        if (not isinstance(garbage, list) or any(not _valid_int(mid) for mid in garbage)
                or len(set(garbage)) != len(garbage)
                or set(garbage) & self._active_ids(payload)):
            raise ValueError("Liste de nettoyage incohérente")

    async def _read_root(self, message):
        if not self._owned(message):
            raise BuildError("L'auteur de la racine Build est invalide.")
        try:
            first = message.content.split("\n", 1)[0]
            match = re.fullmatch(re.escape(MARKER) + r" root revision:(\d+) sha256:([0-9a-f]{64})", first)
            if match is None:
                raise ValueError("En-tête de racine invalide")
            attachments = list(getattr(message, "attachments", ()))
            if attachments:
                if (len(attachments) != 1 or attachments[0].filename != ROOT_FILENAME
                        or attachments[0].size > MAX_ROOT_BYTES):
                    raise ValueError("Pièce jointe de racine invalide")
                raw = await self._io(attachments[0].read())
            else:
                prefix = first + "\n```json\n"
                if not message.content.startswith(prefix) or not message.content.endswith("\n```"):
                    raise ValueError("Racine tronquée")
                raw = message.content[len(prefix):-4].encode("utf-8")
            if len(raw) > MAX_ROOT_BYTES or _sha(raw) != match[2]:
                raise ValueError("Empreinte de racine invalide")
            payload = _json(raw)
            self._validate_root(payload)
            if payload["revision"] != int(match[1]):
                raise ValueError("Révision incohérente")
            return payload
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            log.debug("build console invalid root message_id=%s type=%s", message.id, type(exc).__name__)
            raise BuildError("La dernière racine Build est illisible ou altérée : restauration Staff requise.") from None

    async def _blob_attachments(self, descriptor):
        """L'identité immuable d'une pièce jointe évite de retélécharger le catalogue par clic."""
        attachments_to_read = []
        for position, part in enumerate(descriptor["parts"]):
            message = await self._io(self.channel.fetch_message(part["id"]))
            expected = (f"{MARKER} blob sha256:{descriptor['sha256']} "
                        f"part:{position + 1}/{len(descriptor['parts'])} sha256:{part['sha256']}")
            attachments = list(getattr(message, "attachments", ()))
            if (not self._owned(message) or message.content != expected or len(attachments) != 1
                    or attachments[0].filename != PART_FILENAME or attachments[0].size != part["size"]
                    or attachments[0].id != part["attachment_id"]):
                raise BuildError("Un fragment Build référencé est absent ou invalide : restauration requise.")
            attachments_to_read.append(attachments[0])
        return attachments_to_read

    async def _read_blob(self, descriptor):
        parts = []
        attachments = await self._blob_attachments(descriptor)
        for attachment, part in zip(attachments, descriptor["parts"]):
            raw = await self._io(attachment.read())
            if len(raw) != part["size"] or _sha(raw) != part["sha256"]:
                raise BuildError("Un fragment Build est incomplet ou altéré : stockage suspendu.")
            parts.append(raw)
        payload = b"".join(parts)
        if len(payload) != descriptor["size"] or _sha(payload) != descriptor["sha256"]:
            raise BuildError("Le snapshot Build est incomplet ou altéré : stockage suspendu.")
        return payload

    async def _write_blob(self, raw):
        if not raw or len(raw) > MAX_BLOB_BYTES:
            raise BuildError("Le snapshot Build dépasse la capacité autorisée ; aucune donnée supprimée.")
        chunk_size = self._file_limit()
        if chunk_size < 1:
            raise BuildError("Les pièces jointes sont indisponibles dans #console.")
        chunks = [raw[index:index + chunk_size] for index in range(0, len(raw), chunk_size)]
        if len(chunks) > 1024:
            raise BuildError("Le snapshot Build nécessite trop de fragments Discord.")
        descriptor = {"sha256": _sha(raw), "size": len(raw), "parts": []}
        for index, chunk in enumerate(chunks):
            await self._check_leadership()
            content = (f"{MARKER} blob sha256:{descriptor['sha256']} "
                       f"part:{index + 1}/{len(chunks)} sha256:{_sha(chunk)}")
            file = discord.File(io.BytesIO(chunk), filename=PART_FILENAME)
            try:
                message = await self._io(self.channel.send(
                    content, file=file, allowed_mentions=discord.AllowedMentions.none(),
                ))
            finally:
                file.close()
            if len(message.attachments) != 1:
                raise BuildError("La pièce jointe Build envoyée n'a pas été confirmée.")
            descriptor["parts"].append({"id": message.id, "attachment_id": message.attachments[0].id,
                                        "sha256": _sha(chunk), "size": len(chunk)})
        if await self._read_blob(descriptor) != raw:
            raise BuildError("Le snapshot Build n'a pas été confirmé dans #console.")
        return descriptor

    def _memory(self, key, payload=None):
        repo = MemoryRepository(self.quota)
        for kind, versions in self._root["snapshots"].items():
            for identifier in versions:
                repo.snapshots[kind, identifier] = ""
        data = payload if payload is not None else self._members.get(key)
        if data is None:
            return repo
        try:
            guild, owner = key.split(":")
            actor = Actor(guild_id=int(guild), user_id=int(owner))
            if (data["schema"] != 1 or data["guild_id"] != actor.guild_id
                    or data["owner_id"] != actor.user_id):
                raise ValueError("Identité de snapshot invalide")
            for item in data["builds"]:
                build = Build.model_validate(item)
                require_owner(build, actor)
                if build.revision < 1 or build.id in repo.builds:
                    raise ValueError("Build répété ou non engagé")
                self._require_dependencies(build)
                repo.builds[build.id] = build
            for identifier, rows in data["history"].items():
                if identifier not in repo.builds or not 1 <= len(rows) <= 20:
                    raise ValueError("Historique sans build")
                history = []
                for row in rows:
                    build = Build.model_validate(row["build"])
                    require_owner(build, actor)
                    self._require_dependencies(build)
                    if (build.id != identifier or not HASH.fullmatch(row["report_hash"])
                            or build.revision < 1 or (history and build.revision <= history[-1][0].revision)):
                        raise ValueError("Historique invalide")
                    history.append((build, row["report_hash"]))
                if history[-1][0] != repo.builds[identifier]:
                    raise ValueError("Dernière révision différente du build")
                repo.history[identifier] = history
            if repo.history.keys() != repo.builds.keys():
                raise ValueError("Historique manquant")
            for operation in data["operations"]:
                op = op_key(actor, operation["operation"], operation["request_hash"])
                _date(operation["created_at"])
                if op in repo.operations or not isinstance(operation["result"], dict):
                    raise ValueError("Reçu invalide")
                if "build" in operation["result"]:
                    received = Build.model_validate(operation["result"]["build"])
                    require_owner(received, actor)
                    self._require_dependencies(received)
                repo.operations[op] = operation["request_hash"], canonical(operation["result"])
                repo.operation_dates[op] = operation["created_at"]
            for token, row in data["shares"].items():
                if (not HASH.fullmatch(token) or type(row["revoked"]) is not bool
                        or row["state"] not in {"pending", "sending", "sent", "failed"}):
                    raise ValueError("Partage invalide")
                build = Build.model_validate(row["build"])
                require_owner(build, actor)
                self._require_dependencies(build)
                if build.id not in repo.builds:
                    raise ValueError("Partage d'un build supprimé")
                repo.shares[token] = {**deepcopy(row), "build": build, "expires": _date(row["expires"])}
            from .repository import validated_price
            prices = {}
            for row in data["prices"]:
                price = validated_price(row)
                if price["id"] in prices:
                    raise ValueError("Prix répété")
                prices[price["id"]] = price
            repo._prices[actor.guild_id, actor.user_id] = prices
            return repo
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            log.debug("build console invalid member snapshot member=%s type=%s", key, type(exc).__name__)
            raise BuildError("Le snapshot personnel Build est incohérent : restauration Staff requise.") from None

    def _require_dependencies(self, build):
        for kind, identifier in (("catalog", build.catalog_id), ("rules", build.rules_id)):
            if identifier not in self._root["snapshots"].get(kind, {}):
                raise ValueError("Version de calcul manquante")

    def _serialize(self, actor, repo):
        repo._prune_operations()
        return {
            "schema": 1, "guild_id": actor.guild_id, "owner_id": actor.user_id,
            "builds": [build.model_dump(mode="json") for build in repo.builds.values()],
            "history": {identifier: [{"build": build.model_dump(mode="json"), "report_hash": report}
                                      for build, report in rows] for identifier, rows in repo.history.items()},
            "operations": [{"operation": key[2], "request_hash": value[0],
                            "created_at": repo.operation_dates[key], "result": _json(value[1])}
                           for key, value in repo.operations.items()],
            "shares": {token: {**row, "build": row["build"].model_dump(mode="json"),
                                "expires": row["expires"].isoformat()} for token, row in repo.shares.items()},
            "prices": list(repo._prices.get((actor.guild_id, actor.user_id), {}).values()),
        }

    async def _refresh(self, *, force=False):
        current = await self._io(self.channel.fetch_message(self._root_message.id))
        if not current.pinned:
            raise BuildError("La racine Build n'est plus épinglée : stockage suspendu.")
        root = await self._read_root(current)
        if self._root is not None and root["revision"] < self._root["revision"]:
            raise BuildError("La révision Build a reculé : restauration explicite requise.")
        if not force and self._root == root:
            return
        previous = self._root
        if previous is not None and previous != root and previous["revision"] == root["revision"]:
            raise BuildError("La racine Build a changé sans nouvelle révision.")
        members, snapshots = {}, {}
        try:
            self._root = root
            for kind, versions in root["snapshots"].items():
                for identifier, descriptor in versions.items():
                    key = kind, identifier
                    old = previous and previous["snapshots"].get(kind, {}).get(identifier)
                    if not force and old == descriptor and key in self._snapshot_payloads:
                        snapshots[key] = self._snapshot_payloads[key]
                    else:
                        snapshots[key] = (await self._read_blob(descriptor)).decode("utf-8")
            for key, descriptor in root["members"].items():
                old = previous and previous["members"].get(key)
                if not force and old == descriptor and key in self._members:
                    members[key] = self._members[key]
                else:
                    payload = _json(await self._read_blob(descriptor))
                    self._memory(key, payload)
                    members[key] = payload
        except BaseException:
            self._root = previous
            raise
        self._root_message = current
        self._members, self._snapshot_payloads = members, snapshots

    @asynccontextmanager
    async def _guard(self, actor=None, *, write=False):
        async with self._gate:
            try:
                if not self._opened:
                    raise BuildError("Stockage Build fermé ou en cours d'initialisation.")
                await self._check_leadership()
                channel = self._resolve_channel()
                if channel.id != self.channel.id:
                    raise BuildError("La console Build a changé : stockage suspendu.")
                if actor is not None and actor.guild_id != self.channel.guild.id:
                    raise NotFound()
                await self._refresh()
                pending = self._pending_roots.get(self.channel.id)
                if pending == self._root:
                    self._pending_roots.pop(self.channel.id, None)
                elif pending is not None and write:
                    raise BuildError(
                        "Une sauvegarde Build reste incertaine : nouvelles écritures suspendues "
                        "jusqu'à sa confirmation dans #console."
                    )
                yield
            except BuildError:
                raise
            except Exception as exc:
                log.debug("build console operation failed type=%s", type(exc).__name__, exc_info=True)
                raise BuildError("Accès Build incertain dans #console ; relecture obligatoire avant confirmation.") from None

    def _active_ids(self, root):
        descriptors = list(root["members"].values())
        descriptors.extend(value for versions in root["snapshots"].values() for value in versions.values())
        return {part["id"] for value in descriptors for part in value["parts"]}

    async def _commit_root(self, target):
        await self._check_leadership()
        current = await self._io(self.channel.fetch_message(self._root_message.id))
        if not current.pinned or await self._read_root(current) != self._root:
            raise Conflict("L'index Build a changé : rouvre le build avant de continuer.")
        target["revision"] = self._root["revision"] + 1
        target["garbage"] = sorted(set(target["garbage"]) - self._deleted_garbage)
        self._validate_root(target)
        content, file = self._root_content(target)
        self._pending_roots[self.channel.id] = deepcopy(target)
        try:
            try:
                await self._io(current.edit(
                    content=content, attachments=[file] if file else [],
                    allowed_mentions=discord.AllowedMentions.none(),
                ))
            except Exception as exc:
                if isinstance(exc, discord.HTTPException) and exc.status in {400, 401, 403, 404, 413}:
                    self._pending_roots.pop(self.channel.id, None)
                log.debug("build console root acknowledgement uncertain revision=%s type=%s",
                          target["revision"], type(exc).__name__)
            confirmed_message = await self._io(self.channel.fetch_message(current.id))
            confirmed = await self._read_root(confirmed_message)
            if confirmed != target or not confirmed_message.pinned:
                raise BuildError("L'écriture Build n'a pas été confirmée ; consulte tes builds avant de réessayer.")
            await self._check_leadership()
            await self._refresh()
            self._pending_roots.pop(self.channel.id, None)
            log.debug("build console commit confirmed channel_id=%s revision=%s",
                      self.channel.id, target["revision"])
        finally:
            if file:
                file.close()
        await self._cleanup()

    async def _cleanup(self):
        active = self._active_ids(self._root) | {self._root_message.id}
        for message_id in self._root["garbage"]:
            if message_id in self._deleted_garbage or message_id in active:
                continue
            try:
                await self._check_leadership()
                message = await self._io(self.channel.fetch_message(message_id))
                if not self._owned(message) or not message.content.startswith(f"{MARKER} blob "):
                    log.debug("build console cleanup refused foreign message_id=%s", message_id)
                    continue
                await self._io(message.delete())
                self._deleted_garbage.add(message_id)
            except discord.NotFound:
                self._deleted_garbage.add(message_id)
            except Exception as exc:
                log.debug("build console cleanup deferred message_id=%s type=%s", message_id, type(exc).__name__)
                break

    async def _read(self, actor, method, *args):
        async with self._guard(actor):
            memory = await self._verified_memory(_member_key(actor))
            return await getattr(memory, method)(actor, *args)

    async def _verified_memory(self, key):
        descriptor = self._root["members"].get(key)
        if descriptor is None:
            return self._memory(key)
        payload = _json(await self._read_blob(descriptor))
        memory = self._memory(key, payload)
        self._members[key] = payload
        return memory

    async def _mutate(self, actor, method, *args):
        async with self._guard(actor, write=True):
            key = _member_key(actor)
            memory = await self._verified_memory(key)
            result = await getattr(memory, method)(actor, *args)
            dependencies = set()
            builds = list(memory.builds.values())
            builds.extend(build for rows in memory.history.values() for build, _ in rows)
            builds.extend(row["build"] for row in memory.shares.values())
            for build in builds:
                dependencies.update((("catalog", build.catalog_id), ("rules", build.rules_id)))
            for kind, identifier in dependencies:
                descriptor = self._root["snapshots"][kind][identifier]
                await self._blob_attachments(descriptor)
            payload = self._serialize(actor, memory)
            if payload == self._members.get(key):
                return result
            descriptor = await self._write_blob(canonical(payload).encode("utf-8"))
            root = deepcopy(self._root)
            previous = root["members"].get(key)
            if previous is not None:
                root["garbage"].extend(part["id"] for part in previous["parts"])
            root["members"][key] = descriptor
            await self._commit_root(root)
            return result

    async def get(self, actor, build_id):
        return await self._read(actor, "get", build_id)

    async def list(self, actor):
        return await self._read(actor, "list")

    async def revisions(self, actor, build_id):
        return await self._read(actor, "revisions", build_id)

    async def replay(self, actor, operation, request_hash):
        return await self._read(actor, "replay", operation, request_hash)

    async def commit(self, actor, candidate, expected, operation, request_hash, report_hash):
        return await self._mutate(actor, "commit", candidate, expected, operation, request_hash, report_hash)

    async def delete(self, actor, build_id, expected, operation, request_hash):
        return await self._mutate(actor, "delete", build_id, expected, operation, request_hash)

    async def share(self, actor, build, operation, request_hash):
        return await self._mutate(actor, "share", build, operation, request_hash)

    async def shared(self, actor, code):
        token = share_hash(code)
        async with self._guard(actor):
            for key, payload in self._members.items():
                if token in payload["shares"]:
                    return await (await self._verified_memory(key)).shared(actor, code)
            raise NotFound()

    async def revoke(self, actor, code):
        return await self._mutate(actor, "revoke", code)

    async def claim_publication(self, actor, code):
        return await self._mutate(actor, "claim_publication", code)

    async def publication(self, actor, code, state, message_id=None, channel_id=None):
        return await self._mutate(actor, "publication", code, state, message_id, channel_id)

    async def prices(self, actor):
        return await self._read(actor, "prices")

    async def set_price(self, actor, quote):
        return await self._mutate(actor, "set_price", quote)

    async def delete_price(self, actor, quote_id):
        return await self._mutate(actor, "delete_price", quote_id)

    async def put_snapshot(self, kind, identifier, payload):
        if (kind not in SNAPSHOT_KINDS or not isinstance(identifier, str)
                or not HASH.fullmatch(identifier) or not isinstance(payload, str)
                or not payload or len(payload.encode("utf-8")) > MAX_BLOB_BYTES):
            raise BuildError("Instantané de calcul invalide.")
        async with self._guard(write=True):
            previous = self._snapshot_payloads.get((kind, identifier))
            if previous is not None:
                if previous != payload:
                    raise BuildError("Collision d'empreinte d'instantané.")
                descriptor = self._root["snapshots"][kind][identifier]
                if (await self._read_blob(descriptor)).decode("utf-8") != payload:
                    raise BuildError("L'instantané de calcul archivé a été altéré.")
                return
            descriptor = await self._write_blob(payload.encode("utf-8"))
            root = deepcopy(self._root)
            root["snapshots"].setdefault(kind, {})[identifier] = descriptor
            root["latest"][kind] = identifier
            await self._commit_root(root)

    async def snapshot(self, kind, identifier):
        async with self._guard():
            descriptor = self._root["snapshots"].get(kind, {}).get(identifier)
            if descriptor is None:
                raise BuildError("Version de calcul absente du stockage #console.")
            return (await self._read_blob(descriptor)).decode("utf-8")

    async def latest_snapshot(self, kind):
        async with self._guard():
            identifier = self._root["latest"].get(kind)
            if identifier is None:
                return None
            return (await self._read_blob(self._root["snapshots"][kind][identifier])).decode("utf-8")
