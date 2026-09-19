"""Contrat et backend de test/démonstration EXPLICITEMENT non durable."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import re
from typing import Protocol
from .models import Actor, Build, BuildError, Conflict, NotFound, canonical, digest, revised, now


def op_key(actor, operation, request_hash):
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,120}", operation) or not re.fullmatch(r"[a-f0-9]{64}", request_hash):
        raise BuildError("Identifiant d'opération invalide.")
    return actor.guild_id, actor.user_id, operation


def share_hash(code):
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", code):
        raise NotFound()
    return hashlib.sha256(code.encode()).hexdigest()


def require_owner(build, actor):
    if (build.guild_id, build.owner_id) != (actor.guild_id, actor.user_id):
        raise NotFound()


class Repository(Protocol):
    durable: bool
    async def open(self): ...
    async def close(self): ...
    async def get(self, actor: Actor, build_id: str) -> Build: ...
    async def list(self, actor: Actor) -> tuple[Build, ...]: ...
    async def revisions(self, actor: Actor, build_id: str) -> tuple[Build, ...]: ...
    async def replay(self, actor: Actor, operation: str, request_hash: str): ...
    async def commit(self, actor, candidate, expected, operation, request_hash, report_hash): ...
    async def put_snapshot(self, kind, identifier, payload): ...
    async def snapshot(self, kind, identifier): ...
    async def latest_snapshot(self, kind): ...
    async def delete(self, actor, build_id, expected, operation, request_hash): ...
    async def share(self, actor, build, operation, request_hash): ...
    async def shared(self, actor, code): ...
    async def revoke(self, actor, code): ...
    async def claim_publication(self, actor, code) -> bool: ...
    async def publication(self, actor, code, state, message_id=None, channel_id=None): ...


class MemoryRepository:
    """Même contrôle de révision que SQL, aucune garantie après redémarrage."""
    durable = False

    def __init__(self, quota=20):
        self.quota = quota
        self.lock = asyncio.Lock()
        self.builds, self.snapshots, self.shares = {}, OrderedDict(), {}
        self.operations = OrderedDict()
        self.history = {}

    async def open(self):
        return self

    async def close(self):
        pass

    async def get(self, actor, build_id):
        build = self.builds.get(build_id)
        if build is None:
            raise NotFound()
        require_owner(build, actor)
        return build

    async def list(self, actor):
        return tuple(sorted((b for b in self.builds.values() if (b.guild_id, b.owner_id) == (actor.guild_id, actor.user_id)), key=lambda b: b.updated_at, reverse=True))

    async def revisions(self, actor, build_id):
        # Lire la propriété AVANT de révéler même l'existence d'une révision.
        async with self.lock:
            await self.get(actor, build_id)
            return tuple(row[0] for row in reversed(self.history.get(build_id, ())))

    async def replay(self, actor, operation, request_hash):
        key = op_key(actor, operation, request_hash)
        previous = self.operations.get(key)
        if previous:
            if previous[0] != request_hash:
                raise Conflict("Cette opération a déjà été utilisée avec une autre demande.")
            import json
            return json.loads(previous[1])
        return None

    def _remember(self, key, request_hash, result):
        self.operations[key] = request_hash, canonical(result)
        while len(self.operations) > 10000:
            self.operations.popitem(last=False)

    async def commit(self, actor, candidate, expected, operation, request_hash, report_hash):
        require_owner(candidate, actor)
        key = op_key(actor, operation, request_hash)
        async with self.lock:
            previous = await self.replay(actor, operation, request_hash)
            if previous is not None:
                return Build.model_validate(previous["build"])
            current = self.builds.get(candidate.id)
            if expected is None:
                if current is not None or candidate.revision != 0:
                    raise Conflict("Le build existe déjà.")
                if len(await self.list(actor)) >= self.quota:
                    raise BuildError("Limite de builds atteinte ; exporte puis supprime un ancien build.")
                if len(self.builds) >= 2000:
                    raise BuildError("Capacité du mode d'essai atteinte.")
                revision = 1
            else:
                current = await self.get(actor, candidate.id)
                if current.revision != expected or candidate.revision != expected:
                    raise Conflict("Une autre modification a changé le build. Rouvre-le avant de continuer.")
                revision = expected + 1
            if ("catalog", candidate.catalog_id) not in self.snapshots or ("rules", candidate.rules_id) not in self.snapshots:
                raise BuildError("Dépendance de calcul non sauvegardée.")
            saved = revised(candidate, revision=revision, updated_at=now())
            self.builds[saved.id] = saved
            hist = self.history.setdefault(saved.id, [])
            hist.append((saved, report_hash))
            del hist[:-20]
            self._remember(key, request_hash, {"build": saved.model_dump(mode="json")})
            return saved

    async def delete(self, actor, build_id, expected, operation, request_hash):
        key = op_key(actor, operation, request_hash)
        async with self.lock:
            if await self.replay(actor, operation, request_hash) is not None:
                return
            build = await self.get(actor, build_id)
            if build.revision != expected:
                raise Conflict("Le build a changé. Confirme à nouveau sa suppression.")
            del self.builds[build_id]
            self.history.pop(build_id, None)
            self.shares = {k: v for k, v in self.shares.items() if v["build"].id != build_id}
            # Purger aussi les anciens reçus qui contenaient le build supprimé.
            self.operations = OrderedDict((k, v) for k, v in self.operations.items() if build_id not in v[1])
            self._remember(key, request_hash, {"deleted": build_id})

    async def put_snapshot(self, kind, identifier, payload):
        if kind not in {"catalog", "rules"} or len(payload.encode()) > 24 * 1024 * 1024:
            raise BuildError("Instantané invalide.")
        key = kind, identifier
        previous = self.snapshots.get(key)
        if previous is not None and previous != payload:
            raise BuildError("Collision d'empreinte d'instantané.")
        if previous is None and sum(len(x.encode()) for x in self.snapshots.values()) + len(payload.encode()) > 96 * 1024 * 1024:
            raise BuildError("Mémoire des instantanés saturée : actualisation refusée sans effacer les builds.")
        self.snapshots[key] = payload

    async def snapshot(self, kind, identifier):
        data = self.snapshots.get((kind, identifier))
        if data is None:
            raise BuildError("Version de catalogue/règles indisponible.")
        return data

    async def latest_snapshot(self, kind):
        return next((v for (k, _), v in reversed(self.snapshots.items()) if k == kind), None)

    async def share(self, actor, build, operation, request_hash):
        key = op_key(actor, operation, request_hash)
        async with self.lock:
            previous = await self.replay(actor, operation, request_hash)
            if previous is not None:
                return previous
            current = await self.get(actor, build.id)
            if current.revision != build.revision:
                raise Conflict("Le build a changé avant le partage.")
            current_time = datetime.now(timezone.utc)
            self.shares = {k: v for k, v in self.shares.items() if not v["revoked"] and v["expires"] > current_time}
            live = [s for s in self.shares.values() if s["build"].owner_id == actor.user_id and s["build"].guild_id == actor.guild_id and not s["revoked"] and s["expires"] > datetime.now(timezone.utc)]
            if len(live) >= 20:
                raise BuildError("Limite de 20 partages actifs atteinte. Révoque un partage.")
            code = secrets.token_urlsafe(24)
            self.shares[share_hash(code)] = {"build": build, "expires": datetime.now(timezone.utc) + timedelta(days=7), "revoked": False, "state": "pending"}
            result = {"code": code, "build_id": build.id, "revision": build.revision, "state": "pending"}
            self._remember(key, request_hash, result)
            return result

    async def shared(self, actor, code):
        row = self.shares.get(share_hash(code))
        if not row or row["revoked"] or row["expires"] <= datetime.now(timezone.utc) or row["build"].guild_id != actor.guild_id:
            raise NotFound()
        return row["build"]

    async def revoke(self, actor, code):
        async with self.lock:
            build = await self.shared(actor, code)
            require_owner(build, actor)
            self.shares[share_hash(code)]["revoked"] = True

    async def claim_publication(self, actor, code):
        """Réserver l'envoi une seule fois. Une réponse réseau perdue n'autorise pas un nouvel envoi."""
        async with self.lock:
            build = await self.shared(actor, code)
            require_owner(build, actor)
            row = self.shares[share_hash(code)]
            if row["state"] == "sent":
                return False
            if row["state"] != "pending":
                raise BuildError("Publication déjà tentée ou en cours. Vérifie le salon avant de créer un autre partage.")
            row["state"] = "sending"
            return True

    async def publication(self, actor, code, state, message_id=None, channel_id=None):
        if state not in {"sent", "failed"}:
            raise BuildError("État de publication invalide.")
        async with self.lock:
            build = await self.shared(actor, code)
            require_owner(build, actor)
            row = self.shares[share_hash(code)]
            if row["state"] == "sent" and state == "sent" and row.get("message_id") == message_id:
                return
            if row["state"] != "sending":
                raise Conflict("La réservation de publication n'est plus active.")
            row.update(state=state, message_id=message_id, channel_id=channel_id)
