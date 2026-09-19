"""Transactions PostgreSQL réelles, sérialisation par acteur, CAS par révision.

Pas de repli en RAM en cas de panne. Le DSN n'est ni affiché ni journalisé.
Le verrou d'acteur sérialise aussi les quotas, reçus et partages entre processus.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
import json
from pathlib import Path
import secrets
from uuid import UUID
from .models import Build, BuildError, Conflict, NotFound, canonical, now, revised
from .repository import op_key, require_owner, share_hash


def unpack(value):
    return json.loads(value) if isinstance(value, str) else value


class PostgresRepository:
    durable = True

    def __init__(self, dsn, quota=20, *, migrate=False):
        self.dsn, self.quota, self.migrate = dsn, quota, migrate
        self.pool = None

    async def open(self):
        import asyncpg
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=3, timeout=10, command_timeout=15)
            async with self.pool.acquire() as con:
                if self.migrate:
                    async with con.transaction():
                        await con.execute("SELECT pg_advisory_xact_lock(824630219)")
                        # Refuser une version plus récente AVANT toute migration.
                        exists = await con.fetchval("SELECT to_regclass('public.evolution_build_schema')")
                        if exists:
                            version = await con.fetchval("SELECT max(version) FROM evolution_build_schema")
                            if version != 1:
                                raise BuildError("Version du schéma Build incompatible.")
                        sql = (Path(__file__).resolve().parents[2] / "migrations/build/001_initial.sql").read_text()
                        await con.execute(sql)
                version = await con.fetchval("SELECT max(version) FROM evolution_build_schema")
                if version != 1:
                    raise BuildError("Version du schéma Build incompatible.")
        except Exception as exc:
            await self.close()
            raise BuildError("Base Build indisponible ou migration non appliquée. Vérifier BUILD_DATABASE_URL et le guide d'installation.") from exc
        return self

    async def close(self):
        if self.pool:
            import asyncio
            pool, self.pool = self.pool, None
            try:
                async with asyncio.timeout(10):
                    await pool.close()
            except TimeoutError:
                pool.terminate()

    @asynccontextmanager
    async def connection(self):
        if self.pool is None:
            raise BuildError("Stockage Build fermé.")
        try:
            async with self.pool.acquire(timeout=10) as con:
                yield con
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError("Échec du stockage Build. Rien n'est annoncé comme sauvegardé ; réessayer après contrôle de la base.") from exc

    @asynccontextmanager
    async def transaction(self, actor):
        async with self.connection() as con:
            async with con.transaction():
                await con.execute("SET LOCAL lock_timeout = '5s'")
                await con.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"build:{actor.guild_id}:{actor.user_id}")
                yield con

    async def _owned(self, con, actor, build_id):
        try:
            uid = UUID(build_id)
        except (ValueError, TypeError):
            raise NotFound() from None
        row = await con.fetchrow("SELECT payload FROM evolution_builds WHERE id=$1 AND guild_id=$2 AND owner_id=$3", uid, actor.guild_id, actor.user_id)
        if row is None:
            raise NotFound()
        return Build.model_validate(unpack(row["payload"]))

    async def get(self, actor, build_id):
        async with self.connection() as con:
            return await self._owned(con, actor, build_id)

    async def list(self, actor):
        async with self.connection() as con:
            rows = await con.fetch("SELECT payload FROM evolution_builds WHERE guild_id=$1 AND owner_id=$2 ORDER BY updated_at DESC LIMIT 100", actor.guild_id, actor.user_id)
            return tuple(Build.model_validate(unpack(r["payload"])) for r in rows)

    async def revisions(self, actor, build_id):
        try:
            uid = UUID(build_id)
        except (ValueError, TypeError, AttributeError):
            raise NotFound() from None
        async with self.connection() as con:
            # Une seule requête avec contrôle de propriété : pas de fenêtre de
            # lecture d'historique non autorisée entre deux requêtes.
            rows = await con.fetch("""
                SELECT h.payload FROM evolution_build_history h
                JOIN evolution_builds b ON b.id=h.build_id
                WHERE b.id=$1 AND b.guild_id=$2 AND b.owner_id=$3
                ORDER BY h.revision DESC LIMIT 20
            """, uid, actor.guild_id, actor.user_id)
            if not rows:
                raise NotFound()
            return tuple(Build.model_validate(unpack(row["payload"])) for row in rows)

    async def _replay(self, con, actor, operation, request_hash):
        op_key(actor, operation, request_hash)
        row = await con.fetchrow("SELECT request_hash,result FROM evolution_build_operations WHERE guild_id=$1 AND actor_id=$2 AND operation_id=$3", actor.guild_id, actor.user_id, operation)
        if row:
            if row["request_hash"] != request_hash:
                raise Conflict("Cette opération a déjà été utilisée avec une autre demande.")
            return unpack(row["result"])
        return None

    async def replay(self, actor, operation, request_hash):
        async with self.connection() as con:
            return await self._replay(con, actor, operation, request_hash)

    async def _remember(self, con, actor, operation, request_hash, build_id, result):
        await con.execute("INSERT INTO evolution_build_operations(guild_id,actor_id,operation_id,request_hash,build_id,result) VALUES($1,$2,$3,$4,$5,$6::jsonb)", actor.guild_id, actor.user_id, operation, request_hash, UUID(build_id), canonical(result))
        # Idempotence conservée 30 jours. Pas de suppression des reçus récents.
        await con.execute("DELETE FROM evolution_build_operations WHERE guild_id=$1 AND actor_id=$2 AND created_at < now()-interval '30 days'", actor.guild_id, actor.user_id)

    async def commit(self, actor, candidate, expected, operation, request_hash, report_hash):
        require_owner(candidate, actor)
        op_key(actor, operation, request_hash)
        async with self.transaction(actor) as con:
            previous = await self._replay(con, actor, operation, request_hash)
            if previous is not None:
                return Build.model_validate(previous["build"])
            for kind, identifier in (("catalog", candidate.catalog_id), ("rules", candidate.rules_id)):
                if not await con.fetchval("SELECT 1 FROM evolution_build_snapshots WHERE kind=$1 AND id=$2", kind, identifier):
                    raise BuildError("Instantané de calcul manquant.")
            if expected is None:
                if candidate.revision != 0:
                    raise Conflict("Révision initiale incorrecte.")
                count = await con.fetchval("SELECT count(*) FROM evolution_builds WHERE guild_id=$1 AND owner_id=$2", actor.guild_id, actor.user_id)
                if count >= self.quota:
                    raise BuildError("Limite de builds atteinte.")
                saved = revised(candidate, revision=1, updated_at=now())
                result = await con.fetchval("INSERT INTO evolution_builds(id,guild_id,owner_id,revision,payload,catalog_id,rules_id) VALUES($1,$2,$3,1,$4::jsonb,$5,$6) ON CONFLICT(id) DO NOTHING RETURNING revision", UUID(saved.id), actor.guild_id, actor.user_id, canonical(saved), saved.catalog_id, saved.rules_id)
            else:
                current = await self._owned(con, actor, candidate.id)
                if current.revision != expected or candidate.revision != expected:
                    raise Conflict("Le build a changé : rouvre-le avant de modifier.")
                saved = revised(candidate, revision=expected + 1, updated_at=now())
                result = await con.fetchval("UPDATE evolution_builds SET revision=$1,payload=$2::jsonb,catalog_id=$3,rules_id=$4,updated_at=now() WHERE id=$5 AND guild_id=$6 AND owner_id=$7 AND revision=$8 RETURNING revision", saved.revision, canonical(saved), saved.catalog_id, saved.rules_id, UUID(saved.id), actor.guild_id, actor.user_id, expected)
            if result is None:
                raise Conflict("Une écriture concurrente a changé ce build.")
            await con.execute("INSERT INTO evolution_build_history(build_id,revision,payload,report_hash) VALUES($1,$2,$3::jsonb,$4)", UUID(saved.id), saved.revision, canonical(saved), report_hash)
            await con.execute("DELETE FROM evolution_build_history WHERE build_id=$1 AND revision <= $2", UUID(saved.id), saved.revision - 20)
            await self._remember(con, actor, operation, request_hash, saved.id, {"build": saved.model_dump(mode="json")})
            return saved

    async def delete(self, actor, build_id, expected, operation, request_hash):
        op_key(actor, operation, request_hash)
        async with self.transaction(actor) as con:
            if await self._replay(con, actor, operation, request_hash) is not None:
                return
            build = await self._owned(con, actor, build_id)
            if build.revision != expected:
                raise Conflict("Le build a changé : confirme à nouveau la suppression.")
            await con.execute("DELETE FROM evolution_build_operations WHERE build_id=$1", UUID(build_id))
            await con.execute("DELETE FROM evolution_builds WHERE id=$1 AND guild_id=$2 AND owner_id=$3", UUID(build_id), actor.guild_id, actor.user_id)
            await self._remember(con, actor, operation, request_hash, build_id, {"deleted": build_id})

    async def put_snapshot(self, kind, identifier, payload):
        if kind not in {"catalog", "rules"} or len(payload.encode()) > 24 * 1024 * 1024:
            raise BuildError("Instantané invalide.")
        async with self.connection() as con:
            await con.execute("INSERT INTO evolution_build_snapshots(kind,id,payload) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", kind, identifier, payload)
            previous = await con.fetchval("SELECT payload FROM evolution_build_snapshots WHERE kind=$1 AND id=$2", kind, identifier)
            if previous != payload:
                raise BuildError("Collision d'empreinte d'instantané.")

    async def snapshot(self, kind, identifier):
        async with self.connection() as con:
            value = await con.fetchval("SELECT payload FROM evolution_build_snapshots WHERE kind=$1 AND id=$2", kind, identifier)
            if value is None:
                raise BuildError("Version de calcul absente du stockage.")
            return value

    async def latest_snapshot(self, kind):
        async with self.connection() as con:
            return await con.fetchval("SELECT payload FROM evolution_build_snapshots WHERE kind=$1 ORDER BY created_at DESC,id DESC LIMIT 1", kind)

    async def share(self, actor, build, operation, request_hash):
        op_key(actor, operation, request_hash)
        async with self.transaction(actor) as con:
            previous = await self._replay(con, actor, operation, request_hash)
            if previous is not None:
                return previous
            current = await self._owned(con, actor, build.id)
            if current.revision != build.revision:
                raise Conflict("Le build a changé avant sa publication.")
            count = await con.fetchval("SELECT count(*) FROM evolution_build_shares WHERE guild_id=$1 AND owner_id=$2 AND revoked_at IS NULL AND expires_at>now()", actor.guild_id, actor.user_id)
            if count >= 20:
                raise BuildError("Limite de 20 partages actifs atteinte.")
            code = secrets.token_urlsafe(24)
            await con.execute("INSERT INTO evolution_build_shares(token_hash,build_id,guild_id,owner_id,revision,payload,expires_at) VALUES($1,$2,$3,$4,$5,$6::jsonb,now()+interval '7 days')", share_hash(code), UUID(build.id), actor.guild_id, actor.user_id, build.revision, canonical(build))
            result = {"code": code, "build_id": build.id, "revision": build.revision, "state": "pending"}
            await self._remember(con, actor, operation, request_hash, build.id, result)
            return result

    async def shared(self, actor, code):
        async with self.connection() as con:
            row = await con.fetchrow("SELECT payload FROM evolution_build_shares WHERE token_hash=$1 AND guild_id=$2 AND revoked_at IS NULL AND expires_at>now()", share_hash(code), actor.guild_id)
            if not row:
                raise NotFound()
            return Build.model_validate(unpack(row["payload"]))

    async def revoke(self, actor, code):
        async with self.transaction(actor) as con:
            result = await con.fetchval("UPDATE evolution_build_shares SET revoked_at=now() WHERE token_hash=$1 AND guild_id=$2 AND owner_id=$3 RETURNING 1", share_hash(code), actor.guild_id, actor.user_id)
            if result is None:
                raise NotFound()

    async def claim_publication(self, actor, code):
        async with self.transaction(actor) as con:
            row = await con.fetchrow("SELECT state FROM evolution_build_shares WHERE token_hash=$1 AND guild_id=$2 AND owner_id=$3 AND revoked_at IS NULL AND expires_at>now() FOR UPDATE", share_hash(code), actor.guild_id, actor.user_id)
            if row is None:
                raise NotFound()
            if row["state"] == "sent":
                return False
            if row["state"] != "pending":
                raise BuildError("Publication déjà tentée ou en cours. Vérifie le salon avant de créer un autre partage.")
            await con.execute("UPDATE evolution_build_shares SET state='sending' WHERE token_hash=$1", share_hash(code))
            return True

    async def publication(self, actor, code, state, message_id=None, channel_id=None):
        if state not in {"sent", "failed"}:
            raise BuildError("État de publication invalide.")
        async with self.transaction(actor) as con:
            result = await con.fetchval("UPDATE evolution_build_shares SET state=$1,message_id=$2,channel_id=$3 WHERE token_hash=$4 AND guild_id=$5 AND owner_id=$6 AND state='sending' RETURNING 1", state, message_id, channel_id, share_hash(code), actor.guild_id, actor.user_id)
            if result is None:
                raise NotFound()
