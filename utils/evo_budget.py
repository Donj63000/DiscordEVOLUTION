"""Registre transactionnel : réservation AVANT génération, jamais remise à zéro au boot.

PostgreSQL en production. SQLite uniquement pour le développement hors Render.
Les montants entiers sont des nano-USD (1 USD = 10**9), jamais des floats.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import uuid

from utils.evo_config import EvoConfig, EvoError

# Tarifs standard vérifiés le 15/09/2026. Toutes les entrées sont comptées au
# tarif conservateur d'écriture du cache : 0,25 USD/M ; sortie : 1,20 USD/M.
# +15 % de marge. Aucun outil OpenAI payant ni modèle alternatif n'est exposé.
INPUT_NANO_PER_TOKEN = 250
OUTPUT_NANO_PER_TOKEN = 1200
SCOPE = "evolution-evo-v1"

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS evo_budget_buckets (
        scope TEXT NOT NULL, bucket TEXT NOT NULL,
        used BIGINT NOT NULL DEFAULT 0 CHECK (used >= 0),
        calls BIGINT NOT NULL DEFAULT 0 CHECK (calls >= 0),
        blocked BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (scope, bucket)
    )""",
    """CREATE TABLE IF NOT EXISTS evo_budget_reservations (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_key TEXT NOT NULL,
        month_bucket TEXT NOT NULL, day_bucket TEXT NOT NULL, user_bucket TEXT NOT NULL,
        maximum BIGINT NOT NULL, charged BIGINT, input_tokens BIGINT, output_tokens BIGINT,
        created_at TEXT NOT NULL,
        UNIQUE (scope, request_key)
    )""",
    """CREATE INDEX IF NOT EXISTS evo_budget_month_index
       ON evo_budget_reservations(scope, month_bucket)""",
)


def quote(input_tokens: int, output_tokens: int) -> int:
    if any(type(n) is not int or not 0 <= n <= 100000 for n in (input_tokens, output_tokens)):
        raise EvoError("Comptage de tokens invalide : appel bloqué.")
    base = input_tokens * INPUT_NANO_PER_TOKEN + output_tokens * OUTPUT_NANO_PER_TOKEN
    return (base * 115 + 99) // 100


class _SQLiteConnection:
    def __init__(self, connection):
        self.connection = connection

    def _query(self, sql, args, fetch):
        sql = sql.replace(" FOR UPDATE", "")
        sql = re.sub(r"\$(\d+)", r":p\1", sql)
        cursor = self.connection.execute(sql, {f"p{i}": arg for i, arg in enumerate(args, 1)})
        row = cursor.fetchone() if fetch else None
        return dict(row) if row is not None else None

    async def execute(self, sql, *args):
        return await asyncio.to_thread(self._query, sql, args, False)

    async def fetchrow(self, sql, *args):
        return await asyncio.to_thread(self._query, sql, args, True)


class SQLiteBackend:
    """Fichier déjà initialisé ; mode=rw refuse de recréer un compteur perdu."""
    def __init__(self, path: str):
        self.path = Path(path).resolve()
        self.lock = asyncio.Lock()

    async def open(self):
        if not self.path.is_file():
            raise EvoError("Fichier budget absent : création automatique interdite.")
        # Refuse aussi un fichier quelconque ou une base sans le registre.
        async with self.transaction() as conn:
            await conn.fetchrow("SELECT used FROM evo_budget_buckets LIMIT 1")

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            connection = await asyncio.to_thread(
                sqlite3.connect, self.path.as_uri() + "?mode=rw",
                uri=True, timeout=5, check_same_thread=False, isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            try:
                await asyncio.to_thread(connection.execute, "BEGIN IMMEDIATE")
                yield _SQLiteConnection(connection)
                await asyncio.to_thread(connection.commit)
            except BaseException:
                await asyncio.to_thread(connection.rollback)
                raise
            finally:
                await asyncio.to_thread(connection.close)

    async def close(self):
        pass


class PostgresBackend:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = None

    async def open(self):
        import asyncpg  # Déjà déclaré dans requirements.txt ; aucune connexion globale.
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn, min_size=1, max_size=2, timeout=8, command_timeout=5,
        )
        async with self.transaction() as conn:
            await conn.fetchrow("SELECT used FROM evo_budget_buckets LIMIT 1")
            await conn.fetchrow("SELECT maximum FROM evo_budget_reservations LIMIT 1")

    @asynccontextmanager
    async def transaction(self):
        if self.pool is None:
            raise EvoError("Compteur PostgreSQL non connecté.")
        async with self.pool.acquire(timeout=5) as conn:
            async with conn.transaction():
                yield conn

    async def close(self):
        if self.pool is not None:
            try:
                await asyncio.wait_for(self.pool.close(), 5)
            except TimeoutError:
                self.pool.terminate()


def initialize_sqlite(path: str) -> None:
    """Utilitaire explicite pour tests/dev ; ne remplace jamais un fichier existant."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(target)) as conn, conn:
            for statement in SCHEMA:
                conn.execute(statement)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


async def initialize_postgres(dsn: str) -> None:
    """Initialisation administrative explicite. Ne modifie jamais un registre existant."""
    import asyncpg
    connection = await asyncpg.connect(dsn=dsn, timeout=8, command_timeout=8)
    try:
        async with connection.transaction():
            existing = await connection.fetchrow(
                "SELECT to_regclass('evo_budget_buckets') AS buckets,"
                " to_regclass('evo_budget_reservations') AS reservations"
            )
            if existing["buckets"] is not None or existing["reservations"] is not None:
                raise EvoError("Le registre existe déjà : aucune réinitialisation autorisée.")
            for statement in SCHEMA:
                await connection.execute(statement)
    finally:
        await connection.close()


class Budget:
    def __init__(self, config: EvoConfig, backend=None, *, clock=None):
        self.config = config
        self.backend = backend or (
            PostgresBackend(config.database_url) if config.database_url
            else SQLiteBackend(config.sqlite_path)
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def open(self):
        try:
            await self.backend.open()
        except Exception:
            raise EvoError("Compteur durable indisponible. Aucun appel IA ne sera lancé.") from None

    def buckets(self, user_key: str):
        now = self.clock().astimezone(timezone.utc)
        month = "month:" + now.strftime("%Y-%m")
        day = "day:" + now.strftime("%Y-%m-%d")
        user = day + ":user:" + hashlib.sha256(user_key.encode()).hexdigest()[:24]
        return now, month, day, user

    async def reserve(self, request_key: str, user_key: str, maximum: int) -> str:
        if type(maximum) is not int or not 0 < maximum <= self.config.request_nano:
            raise EvoError("Cette demande dépasse le plafond de coût par requête.")
        now, month, day, user = self.buckets(user_key)
        identifier = uuid.uuid4().hex
        try:
            async with self.backend.transaction() as conn:
                # Toutes les instances prennent d'abord le même verrou mensuel.
                await conn.execute(
                    "INSERT INTO evo_budget_buckets(scope,bucket) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    SCOPE, month,
                )
                monthly = await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2 FOR UPDATE",
                    SCOPE, month,
                )
                duplicate = await conn.fetchrow(
                    "SELECT id FROM evo_budget_reservations WHERE scope=$1 AND request_key=$2",
                    SCOPE, request_key,
                )
                if duplicate:
                    raise EvoError("Cette demande a déjà été traitée : aucun double appel IA.")
                if monthly["blocked"] or monthly["used"] + maximum > self.config.monthly_nano:
                    raise EvoError("Le budget IA du mois est atteint ou mis en sécurité. Les commandes classiques restent disponibles.")
                for bucket in (day, user):
                    await conn.execute(
                        "INSERT INTO evo_budget_buckets(scope,bucket) VALUES($1,$2) ON CONFLICT DO NOTHING",
                        SCOPE, bucket,
                    )
                daily = await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2", SCOPE, day,
                )
                personal = await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2", SCOPE, user,
                )
                if daily["used"] + maximum > self.config.daily_nano:
                    raise EvoError("Le petit budget IA de la journée est atteint. Les recherches classiques restent disponibles.")
                if personal["calls"] >= self.config.user_daily_calls:
                    raise EvoError("Tu as atteint ton quota IA du jour. Il se renouvelle à minuit UTC.")
                for bucket in (month, day, user):
                    await conn.execute(
                        "UPDATE evo_budget_buckets SET used=used+$3, calls=calls+1 WHERE scope=$1 AND bucket=$2",
                        SCOPE, bucket, maximum,
                    )
                await conn.execute(
                    """INSERT INTO evo_budget_reservations
                    (id,scope,request_key,month_bucket,day_bucket,user_bucket,maximum,created_at)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8)""",
                    identifier, SCOPE, request_key, month, day, user, maximum, now.isoformat(),
                )
            return identifier
        except EvoError:
            raise
        except Exception:
            raise EvoError("Le compteur budget est indisponible : appel IA bloqué par sécurité.") from None

    async def settle(self, identifier: str, input_tokens: int, output_tokens: int) -> None:
        actual = quote(input_tokens, output_tokens)
        try:
            async with self.backend.transaction() as conn:
                # Même ordre de verrous que reserve(). Le mois de la réservation
                # est conservé même si la réponse arrive après minuit.
                record = await conn.fetchrow(
                    "SELECT * FROM evo_budget_reservations WHERE id=$1 AND scope=$2", identifier, SCOPE,
                )
                if record is None:
                    raise EvoError("Réservation introuvable : génération suivante bloquée.")
                await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2 FOR UPDATE",
                    SCOPE, record["month_bucket"],
                )
                record = await conn.fetchrow(
                    "SELECT * FROM evo_budget_reservations WHERE id=$1 AND scope=$2 FOR UPDATE", identifier, SCOPE,
                )
                if record["charged"] is not None:
                    return  # Idempotence, notamment après un retour de connexion incertain.
                delta = actual - record["maximum"]
                for bucket in (record["month_bucket"], record["day_bucket"], record["user_bucket"]):
                    await conn.execute(
                        "UPDATE evo_budget_buckets SET used=used+$3 WHERE scope=$1 AND bucket=$2",
                        SCOPE, bucket, delta,
                    )
                await conn.execute(
                    """UPDATE evo_budget_reservations SET charged=$2,input_tokens=$3,output_tokens=$4
                    WHERE id=$1""", identifier, actual, input_tokens, output_tokens,
                )
                if actual > record["maximum"]:
                    await conn.execute(
                        "UPDATE evo_budget_buckets SET blocked=TRUE WHERE scope=$1 AND bucket=$2",
                        SCOPE, record["month_bucket"],
                    )
            if actual > record["maximum"]:
                raise EvoError("Le comptage fournisseur a dépassé la réservation : IA mise en sécurité.")
        except EvoError:
            raise
        except Exception:
            # Pas de remboursement spéculatif : la réservation initiale reste consommée.
            raise EvoError("La réponse a été reçue, mais le compteur est indisponible. IA suspendue pour cette demande.") from None

    async def block_current_month(self) -> None:
        """Anomalie de fournisseur : verrou persistant, aucune remise à zéro Discord."""
        _, month, _, _ = self.buckets("")
        try:
            async with self.backend.transaction() as conn:
                await conn.execute(
                    "INSERT INTO evo_budget_buckets(scope,bucket) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    SCOPE, month,
                )
                await conn.execute(
                    "UPDATE evo_budget_buckets SET blocked=TRUE WHERE scope=$1 AND bucket=$2",
                    SCOPE, month,
                )
        except Exception:
            raise EvoError("Anomalie fournisseur et compteur indisponible : arrêt de cette demande.") from None

    async def status(self) -> dict:
        _, month, day, _ = self.buckets("")
        try:
            async with self.backend.transaction() as conn:
                monthly = await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2", SCOPE, month,
                )
                daily = await conn.fetchrow(
                    "SELECT * FROM evo_budget_buckets WHERE scope=$1 AND bucket=$2", SCOPE, day,
                )
                totals = await conn.fetchrow(
                    """SELECT COALESCE(SUM(CASE WHEN charged IS NULL THEN maximum ELSE 0 END),0) AS pending,
                    COALESCE(SUM(input_tokens),0) AS inputs, COALESCE(SUM(output_tokens),0) AS outputs
                    FROM evo_budget_reservations WHERE scope=$1 AND month_bucket=$2""", SCOPE, month,
                )
                return {
                    "month": month[6:], "used_nano": monthly["used"] if monthly else 0,
                    "day_nano": daily["used"] if daily else 0,
                    "calls": monthly["calls"] if monthly else 0,
                    "blocked": bool(monthly["blocked"]) if monthly else False,
                    "pending_nano": totals["pending"], "input_tokens": totals["inputs"],
                    "output_tokens": totals["outputs"], "limit_nano": self.config.monthly_nano,
                }
        except Exception:
            raise EvoError("Impossible de consulter le compteur durable.") from None

    async def close(self):
        await self.backend.close()
