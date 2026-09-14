"""Moteur d'enquête de modération, sans dépendance réseau ni SDK Discord.

Les identifiants constituent les preuves d'attribution. Un nom, même exact,
reste une référence textuelle ; une ressemblance n'est jamais une identité.
Aucun apprentissage, score de réputation, graphe de relations ou profil privé.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo

UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")
EPOCH = datetime(2015, 1, 1, tzinfo=UTC)
MARKER = "[ENQUETE:"
MAX_ALIASES = 32


class EnqueteError(Exception):
    """Erreur présentable au staff, sans contenu de message ni secret."""


def normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value).casefold())
    value = "".join(c for c in value if not unicodedata.combining(c)
                    and unicodedata.category(c) != "Cf")
    # Un tiret, point ou underscore n'est pas une frontière d'identité fiable.
    return " ".join(re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).split())


def clean_text(value: object, *, redact: bool = True) -> str:
    """Neutralise les caractères invisibles et masque quelques secrets évidents.

    Ce masquage est volontairement documenté comme imparfait. Il ne remplace
    ni la restriction d'accès ni la vérification humaine avant redistribution.
    """
    text = str(value if value is not None else "")
    text = "".join(
        c if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf", "Cs"}
        else f"[U+{ord(c):04X}]" for c in text
    )
    if redact:
        text = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "[EMAIL MASQUÉ]", text)
        text = re.sub(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", "[IP MASQUÉE]", text)
        text = re.sub(r"(?<!\w)\+\d{1,3}(?:[ .()-]*\d){8,12}(?!\d)", "[TÉLÉPHONE MASQUÉ]", text)
        text = re.sub(
            r"(?i)\b(?:mfa\.[\w-]{20,}|[\w-]{23,28}\.[\w-]{6,7}\.[\w-]{25,110})\b",
            "[JETON POTENTIEL MASQUÉ]", text,
        )
    return text


def line(value: object, *, redact: bool = True) -> str:
    return clean_text(value, redact=redact).replace("\n", " ↵ ").replace("\t", " ")


def quote(value: object, *, redact: bool = True) -> str:
    return "\n".join("    | " + x for x in clean_text(value, redact=redact).split("\n"))


def stamp(value: str | datetime | None) -> str:
    if not value:
        return "non disponible"
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        return f"{dt:%d/%m/%Y %H:%M:%S} (fuseau non enregistré)"
    return dt.astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S %Z (UTC%z)")


def snowflake_at(dt: datetime) -> int:
    return int((dt.astimezone(UTC) - EPOCH).total_seconds() * 1000) << 22


def parse_target_id(value: str) -> int | None:
    match = re.fullmatch(r"(?:<@!?(\d{17,19})>|(\d{17,19}))", value.strip())
    if not match:
        return None
    result = int(match.group(1) or match.group(2))
    if not 0 < result < 2**63:
        raise EnqueteError("Identifiant Discord invalide.")
    return result


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @classmethod
    def parse(cls, depuis: str = "", jusqua: str = "", *, now: datetime | None = None,
              default_days: int = 0) -> "Window":
        now = (now or datetime.now(UTC)).astimezone(UTC)

        def day(text: str) -> datetime:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
                try:
                    d = datetime.strptime(text.strip(), fmt).date()
                    return datetime.combine(d, time.min, tzinfo=PARIS)
                except ValueError:
                    pass
            raise EnqueteError("Date invalide : utilise JJ/MM/AAAA ou AAAA-MM-JJ.")

        start = day(depuis).astimezone(UTC) if depuis.strip() else (
            now - timedelta(days=default_days) if default_days else EPOCH
        )
        # L'option jusqua inclut toute la journée de Paris, même lors d'un changement d'heure.
        end = min((day(jusqua) + timedelta(days=1)).astimezone(UTC), now) if jusqua.strip() else now
        start = max(start, EPOCH)
        if start >= end:
            raise EnqueteError("La date de début doit précéder la fin de la période.")
        return cls(start, end)

    def contains(self, value: datetime) -> bool:
        return value.tzinfo is not None and self.start <= value.astimezone(UTC) < self.end


def env_int(env: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(env.get(key, str(default)))
    except (TypeError, ValueError) as exc:
        raise EnqueteError(f"Configuration {key} : un entier est attendu.") from exc
    if not low <= value <= high:
        raise EnqueteError(f"Configuration {key} : valeur attendue entre {low} et {high}.")
    return value


def id_set(value: str) -> frozenset[int]:
    values = [x.strip() for x in value.split(",") if x.strip()]
    if any(not x.isascii() or not x.isdigit() or not 0 < int(x) < 2**63 for x in values):
        raise EnqueteError("La configuration des identifiants doit contenir des nombres séparés par des virgules.")
    return frozenset(map(int, values))


@dataclass(frozen=True)
class Config:
    staff_channel: int = 0
    console_channel: int = 0
    role_ids: frozenset[int] = frozenset()
    role_name: str = "Staff"
    excluded: frozenset[int] = frozenset()
    max_messages: int = 0
    max_seconds: int = 7200
    disk_bytes: int = 512 * 1024**2
    part_bytes: int = 4 * 1024**2
    max_parts: int = 64
    max_threads: int = 5000
    audit_limit: int = 10000
    context_minutes: int = 60
    retention_days: int = 7
    cooldown: int = 60
    default_days: int = 0
    legacy_guild: int = 0
    legacy_timezone: str = ""

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = os.environ if env is None else env
        def number(key, default, low, high):
            return env_int(env, "ENQUETE_" + key, default, low, high)
        def single(key, fallback=""):
            ids = id_set(str(env.get(key) or fallback or ""))
            if len(ids) > 1:
                raise EnqueteError(f"{key} doit contenir un seul identifiant.")
            return next(iter(ids), 0)
        tz = env.get("ENQUETE_LEGACY_TIMEZONE", "").strip()
        if tz:
            try:
                ZoneInfo(tz)
            except (ValueError, KeyError) as exc:
                raise EnqueteError("ENQUETE_LEGACY_TIMEZONE n'est pas un fuseau IANA valide.") from exc
        return cls(
            staff_channel=single("ENQUETE_STAFF_CHANNEL_ID"),
            console_channel=single("ENQUETE_CONSOLE_CHANNEL_ID", env.get("CHANNEL_CONSOLE_ID", "")),
            role_ids=id_set(env.get("ENQUETE_STAFF_ROLE_IDS", "")),
            role_name=env.get("ENQUETE_STAFF_ROLE_NAME", env.get("IASTAFF_ROLE",
                              env.get("STAFF_ROLE_NAME", "Staff"))).strip() or "Staff",
            excluded=id_set(env.get("ENQUETE_EXCLUDED_CHANNEL_IDS", "")),
            max_messages=number("MAX_MESSAGES", 0, 0, 10000000),
            max_seconds=number("MAX_SECONDS", 7200, 60, 86400),
            disk_bytes=number("MAX_DISK_MIB", 512, 16, 8192) * 1024**2,
            part_bytes=number("PART_MIB", 4, 1, 8) * 1024**2,
            max_parts=number("MAX_PARTS", 64, 2, 256),
            max_threads=number("MAX_THREADS", 5000, 1, 100000),
            audit_limit=number("AUDIT_LIMIT", 10000, 0, 100000),
            context_minutes=number("CONTEXT_MINUTES", 60, 1, 1440),
            retention_days=number("RETENTION_DAYS", 7, 1, 30),
            cooldown=number("COOLDOWN_SECONDS", 60, 0, 3600),
            default_days=number("DEFAULT_DAYS", 0, 0, 3650),
            legacy_guild=single("ENQUETE_LEGACY_DATA_GUILD_ID"),
            legacy_timezone=tz,
        )


@dataclass(frozen=True)
class Alias:
    value: str
    source: str


def aliases_from_input(text: str) -> list[Alias]:
    parts = [p.strip() for p in re.split("[,;\n]", text) if p.strip()]
    if len(parts) > MAX_ALIASES or any(len(p) > 100 or len(normalise(p)) < 2 for p in parts):
        raise EnqueteError("Indique au plus 32 alias, de 2 à 100 caractères, séparés par des virgules.")
    return [Alias(p, "fourni par le staff ; attribution à vérifier") for p in parts]


def one_edit(a: str, b: str) -> bool:
    """Distance d'édition <= 1, sans dépendance ni coût quadratique."""
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    if len(a) > len(b):
        a, b = b, a
    i = j = errors = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            errors += 1
            j += 1
            if errors > 1:
                return False
        else:
            i += 1
            j += 1
    return True


class Matcher:
    def __init__(self, aliases: list[Alias], *, approximate: bool = True):
        unique = {}
        for alias in aliases:
            key = normalise(alias.value)
            if 2 <= len(key) <= 100:
                unique.setdefault(key, alias)
        # Les alias observés ont une borne indépendante des 32 alias manuels.
        self.aliases = dict(list(unique.items())[:128])
        self.approximate = approximate
        self.patterns = [
            (key, re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)"))
            for key in self.aliases
        ]
        self.words = [key for key in self.aliases if " " not in key and len(key) >= 6]
        # Cache par instance : aucune conservation d'alias après la fin du dossier.
        self._near = lru_cache(maxsize=8192)(self._near_uncached)

    def _near_uncached(self, token: str) -> tuple[str, ...]:
        result = []
        for name in self.words:
            if token == name:
                continue
            if 4 <= len(token) <= len(name) - 3 and name.startswith(token):
                result.append(f"PISTE_ABREVIATION:{token} → {name}")
            elif len(token) >= 5 and one_edit(token, name):
                result.append(f"PISTE_TYPO:{token} → {name}")
        return tuple(result)

    def match(self, text: str) -> list[str]:
        key = normalise(text)
        result = [f"CITATION:{name}" for name, pattern in self.patterns if pattern.search(key)]
        if self.approximate:
            for token in sorted(set(key.split())):
                if token not in self.aliases:
                    result.extend(self._near(token))
        return sorted(set(result))


@dataclass
class Record:
    mid: int
    cid: int
    aid: int
    name: str
    created: str
    content: str
    extra: dict = field(default_factory=dict)
    edited: str | None = None
    mentions: list[int] = field(default_factory=list)
    reply_id: int | None = None
    reply_channel: int | None = None
    reply_author: int | None = None


class Workspace:
    """Index jetable, sur disque privé. Rien n'est ajouté aux bases métier du bot."""
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(str(path))
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA cache_size=-8192;
            PRAGMA temp_store=MEMORY;
            CREATE TABLE channels (
                cid INTEGER PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', scanned INTEGER DEFAULT 0,
                title TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE messages (
                mid INTEGER PRIMARY KEY, cid INTEGER NOT NULL, aid INTEGER NOT NULL,
                name TEXT NOT NULL, created TEXT NOT NULL, content TEXT NOT NULL,
                extra TEXT NOT NULL, edited TEXT, mentions TEXT NOT NULL,
                rid INTEGER, rcid INTEGER, raid INTEGER
            );
            CREATE INDEX messages_channel ON messages(cid, mid);
            CREATE INDEX messages_author ON messages(aid);
            CREATE TABLE selected (mid INTEGER PRIMARY KEY, reasons TEXT NOT NULL);
            CREATE TABLE events (
                eid TEXT PRIMARY KEY, source TEXT NOT NULL, created TEXT NOT NULL,
                payload TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def channel(self, cid: int, label: str, kind: str, status: str, detail: str = "", *, title: str = ""):
        self.db.execute(
            "INSERT INTO channels(cid,label,kind,status,detail,title) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(cid) DO UPDATE SET label=excluded.label,kind=excluded.kind,"
            "status=excluded.status,detail=excluded.detail,title=excluded.title",
            (cid, label, kind, status, detail, title),
        )

    def status(self, cid: int, status: str, detail: str = ""):
        self.db.execute("UPDATE channels SET status=?,detail=? WHERE cid=?", (status, detail, cid))

    def add(self, record: Record) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (record.mid, record.cid, record.aid, record.name, record.created, record.content,
             json.dumps(record.extra, ensure_ascii=False), record.edited, json.dumps(record.mentions),
             record.reply_id, record.reply_channel, record.reply_author),
        )
        if cur.rowcount:
            self.db.execute("UPDATE channels SET scanned=scanned+1 WHERE cid=?", (record.cid,))
        return bool(cur.rowcount)

    def event(self, eid: str, source: str, created: str, payload: dict):
        self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                        (eid, source, created, json.dumps(payload, ensure_ascii=False)))

    def commit(self):
        self.db.commit()

    @property
    def size(self):
        # Inclut journal et exports voisins ; la limite est un garde-fou disque global.
        return sum(p.stat().st_size for p in self.path.parent.iterdir() if p.is_file())

    @property
    def total(self):
        return self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def observed_names(self, target_id: int) -> list[Alias]:
        names = {}
        for row in self.db.execute("SELECT name,extra FROM messages WHERE aid=?", (target_id,)):
            for value in [row["name"], *json.loads(row["extra"]).get("names", [])]:
                if value and len(names) < 96:
                    names.setdefault(normalise(value), Alias(value, "nom renvoyé par Discord lors de la collecte"))
        return list(names.values())

    async def select(self, target_id: int, matcher: Matcher, *, context: int, context_minutes: int):
        if not 0 <= context <= 10 or not 1 <= context_minutes <= 1440:
            raise EnqueteError("Bornes du contexte invalides.")
        self.db.execute("DELETE FROM selected")
        query = """
            SELECT m.*, p.aid AS parent_author FROM messages m
            LEFT JOIN messages p ON p.mid=m.rid AND p.cid=m.rcid
            ORDER BY m.mid
        """
        cursor = self.db.execute(query)
        total = 0
        while rows := cursor.fetchmany(200):
            for row in rows:
                extra = json.loads(row["extra"])
                reasons = []
                if row["aid"] == target_id and not extra.get("webhook_id"):
                    reasons.append("AUTEUR_ID")
                if target_id in json.loads(row["mentions"]):
                    reasons.append("MENTION_DISCORD_ID")
                if row["rcid"] == row["cid"] and (
                    row["parent_author"] == target_id or row["raid"] == target_id
                ):
                    reasons.append("REPONSE_A_LA_CIBLE_ID")
                if extra.get("target_reactions"):
                    reasons.append("REACTION_ACTUELLE_DE_LA_CIBLE_ID")
                if re.search(r"(?<!\d)" + str(target_id) + r"(?!\d)", row["content"]):
                    reasons.append("IDENTIFIANT_DANS_TEXTE")
                reasons.extend(matcher.match(row["content"] + "\n" + extra.get("search_text", "")))
                if reasons:
                    self.db.execute("INSERT INTO selected VALUES(?,?)",
                                    (row["mid"], json.dumps(reasons, ensure_ascii=False)))
                total += 1
            self.commit()
            await asyncio.sleep(0)

        # Titres de fils : leur correspondance ne prouve pas que chaque message parle de la cible.
        for channel in self.db.execute("SELECT cid,title FROM channels WHERE kind='fil'").fetchall():
            matches = matcher.match(channel["title"])
            if matches:
                first = self.db.execute("SELECT MIN(mid) FROM messages WHERE cid=?", (channel["cid"],)).fetchone()[0]
                if first is not None:
                    current = self.db.execute("SELECT reasons FROM selected WHERE mid=?", (first,)).fetchone()
                    reasons = json.loads(current[0]) if current else []
                    reasons.extend("TITRE_FIL:" + m for m in matches)
                    self.db.execute("INSERT OR REPLACE INTO selected VALUES(?,?)",
                                    (first, json.dumps(reasons, ensure_ascii=False)))
            await asyncio.sleep(0)

        # On ne lit jamais les lignes de contexte comme de nouveaux points de départ :
        # cela empêcherait une expansion involontaire vers tout le serveur.
        cursor = self.db.execute(
            "SELECT m.* FROM messages m JOIN selected s ON s.mid=m.mid WHERE s.reasons!='[]' ORDER BY m.mid"
        )
        while rows := cursor.fetchmany(200):
            for row in rows:
                lower = (datetime.fromisoformat(row["created"]) - timedelta(minutes=context_minutes)).isoformat()
                upper = (datetime.fromisoformat(row["created"]) + timedelta(minutes=context_minutes)).isoformat()
                nearby = []
                if context:
                    nearby.extend(self.db.execute(
                        "SELECT mid FROM messages WHERE cid=? AND mid<? AND created>=? "
                        "ORDER BY mid DESC LIMIT ?", (row["cid"], row["mid"], lower, context),
                    ).fetchall())
                    nearby.extend(self.db.execute(
                        "SELECT mid FROM messages WHERE cid=? AND mid>? AND created<=? "
                        "ORDER BY mid LIMIT ?", (row["cid"], row["mid"], upper, context),
                    ).fetchall())
                # L'original d'une réponse, seulement s'il a été lu dans ce même salon autorisé.
                if row["rid"] and row["rcid"] == row["cid"]:
                    nearby.extend(self.db.execute("SELECT mid FROM messages WHERE mid=? AND cid=?",
                                                  (row["rid"], row["cid"])).fetchall())
                for item in nearby:
                    self.db.execute("INSERT OR IGNORE INTO selected VALUES(?, '[]')", (item[0],))
            self.commit()
            await asyncio.sleep(0)
        return total

    def statistics(self, target_id: int) -> dict:
        result = {
            "scanned": self.total, "authored": 0, "edited_snapshot": 0,
            "attachments": 0, "reactions_received_snapshot": 0, "matching": 0, "context": 0,
            "reasons": Counter(), "months": Counter(), "channels": Counter(),
            "first": None, "last": None,
        }
        for row in self.db.execute("SELECT * FROM messages WHERE aid=? ORDER BY mid", (target_id,)):
            extra = json.loads(row["extra"])
            if extra.get("webhook_id"):
                continue
            result["authored"] += 1
            result["edited_snapshot"] += bool(row["edited"])
            result["attachments"] += len(extra.get("attachments", []))
            result["reactions_received_snapshot"] += sum(x.get("count", 0) for x in extra.get("reactions", []))
            result["first"] = result["first"] or row["created"]
            result["last"] = row["created"]
            result["months"][datetime.fromisoformat(row["created"]).astimezone(PARIS).strftime("%Y-%m")] += 1
            result["channels"][row["cid"]] += 1
        for row in self.db.execute("SELECT reasons FROM selected"):
            reasons = json.loads(row[0])
            result["matching" if reasons else "context"] += 1
            result["reasons"].update(set(r.split(":", 1)[0] for r in reasons))
        return result


class Parts:
    """Découpage UTF-8 sans casser un caractère ; aucun bloc supprimé silencieusement."""
    def __init__(self, directory: Path, prefix: str, limit: int, max_parts: int):
        if limit < 4096 or max_parts < 1 or not re.fullmatch(r"[a-zA-Z0-9_-]+", prefix):
            raise ValueError("Paramètres d'export invalides.")
        self.directory, self.prefix, self.limit, self.max_parts = directory, prefix, limit, max_parts
        self.paths: list[Path] = []
        self.handle = None
        self.used = 0
        self.truncated = False
        self.closed = False
        self.truncation_reason = "limite de fichiers atteinte"

    def truncate(self, reason: str):
        self.truncated = True
        self.truncation_reason = reason

    def _new(self) -> bool:
        if self.handle:
            self.handle.close()
        if len(self.paths) >= self.max_parts:
            self.handle = None
            self.truncated = True
            return False
        path = self.directory / f"{self.prefix}_{len(self.paths) + 1:03d}.txt"
        self.handle = path.open("wb")
        os.chmod(path, 0o600)
        self.paths.append(path)
        header = f"ENQUÊTE — {self.prefix} — PARTIE {len(self.paths):03d}\nLire aussi la synthèse et ses limites.\n\n".encode()
        self.handle.write(header)
        self.used = len(header)
        return True

    def write(self, text: str) -> bool:
        if self.closed:
            raise ValueError("Export déjà fermé.")
        if self.truncated:
            return False
        data = text.encode("utf-8")
        while data:
            if self.handle is None and not self._new():
                return False
            room = self.limit - self.used - 256  # réserve pour une fin explicite
            if room <= 4:
                self.handle.write(b"\n[SUITE DANS LA PARTIE SUIVANTE, SI PRESENTE DANS LA SYNTHESE]\n")
                self.handle.close()
                self.handle = None
                continue
            chunk = data[:room]
            # Ne jamais partager un caractère multi-octets entre deux fichiers.
            while chunk:
                try:
                    chunk.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    chunk = chunk[:-1]
            self.handle.write(chunk)
            self.used += len(chunk)
            data = data[len(chunk):]
        return True

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handle:
            self.handle.close()
            self.handle = None
        if self.truncated and self.paths:
            with self.paths[-1].open("ab") as stream:
                stream.write(("\n[EXPORT PARTIEL : " + line(self.truncation_reason) +
                              ". Consulter la synthèse.]\n").encode())


@dataclass
class ReportMeta:
    job_id: str
    guild_id: int
    guild_name: str
    target_id: int
    requester_id: int
    reason: str
    window: Window
    identity: dict
    aliases: list[Alias]
    approximate: bool
    context: int
    deep_reactions: bool
    notes: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    partial: bool = False


def reason_label(reason: str) -> str:
    labels = {
        "AUTEUR_ID": "message écrit par le compte ciblé, identifié par son ID Discord",
        "MENTION_DISCORD_ID": "mention Discord explicite du compte ciblé",
        "REPONSE_A_LA_CIBLE_ID": "réponse à un message du compte ciblé",
        "REACTION_ACTUELLE_DE_LA_CIBLE_ID": "réaction du compte ciblé encore présente lors de la lecture",
        "IDENTIFIANT_DANS_TEXTE": "ID du compte écrit dans le texte ; pas nécessairement une mention",
        "CITATION": "nom ou alias cité dans le texte ; attribution à vérifier",
        "PISTE_ABREVIATION": "abréviation possible ; ce rapprochement n'est pas confirmé",
        "PISTE_TYPO": "faute de frappe possible ; ce rapprochement n'est pas confirmé",
        "TITRE_FIL": "nom ou variante trouvé dans le titre du fil ; pas dans tous ses messages",
    }
    key, _, detail = reason.partition(":")
    return labels.get(key, key) + (f" : {detail}" if detail else "")


def record_block(row, guild_id: int, index: int) -> str:
    extra, reasons = json.loads(row["extra"]), json.loads(row["reasons"])
    label = "CORRESPONDANCE" if reasons else "CONTEXTE UNIQUEMENT — aucune attribution à la cible"
    lines = [
        f"\n--- MESSAGE {index:06d} | {label} ---",
        f"Créé : {stamp(row['created'])}",
        f"Auteur affiché : {line(row['name'])} | ID : {row['aid']}",
        f"Message : {row['mid']} | Salon : {row['cid']}",
        f"Source : https://discord.com/channels/{guild_id}/{row['cid']}/{row['mid']}",
    ]
    if reasons:
        lines.append("Pourquoi retenu : " + line(" ; ".join(
            reason_label(r) + f" [{r.split(':', 1)[0]}]" for r in reasons
        )))
    if row["edited"]:
        lines.append(f"Dernière modification visible : {stamp(row['edited'])} (pas l'historique des versions)")
    if row["rid"]:
        lines.append(f"Référence de réponse : message {row['rid']} ; salon {row['rcid'] or 'inconnu'}")
    if extra.get("webhook_id"):
        lines.append(f"WEBHOOK {extra['webhook_id']} : son nom affiché ne prouve pas une identité humaine.")
    elif extra.get("bot"):
        lines.append("COMPTE BOT : ce texte peut être automatisé ou reprendre les propos d'un tiers.")
    lines.append("Texte, cité sans interprétation :\n" + quote(row["content"] or "[aucun texte renvoyé]"))
    if extra.get("embed_text"):
        lines.append("Encarts Discord (peuvent être générés automatiquement) :\n" + quote(extra["embed_text"]))
    for attachment in extra.get("attachments", []):
        lines.append(
            "Pièce jointe : " + line(attachment.get("name", "")) +
            f" | {attachment.get('size', 0)} octets | " + line(attachment.get("url", ""))
        )
    if extra.get("stickers"):
        lines.append("Autocollants : " + line(", ".join(extra["stickers"])))
    if extra.get("poll"):
        lines.append("Sondage (état courant, pas l'historique des votes) :\n" + quote(extra["poll"]))
    if extra.get("reactions"):
        lines.append("Réactions actuellement présentes : " + line("; ".join(
            f"{r['emoji']} × {r['count']}" for r in extra["reactions"]
        )))
    if extra.get("target_reactions"):
        lines.append("Réactions de la cible vérifiées par ID : " + line(", ".join(extra["target_reactions"])) +
                     " (date d'ajout inconnue ; la date ci-dessus est celle du message)")
    if extra.get("pinned"):
        lines.append("Message actuellement épinglé : oui (date d'épinglage non collectée)")
    if extra.get("forwarded"):
        lines.append("Message transféré détecté : contenu d'origine non importé hors périmètre.")
    return "\n".join(lines) + "\n"


async def build_report(work: Workspace, meta: ReportMeta, cfg: Config, *, file_limit: int) -> list[Path]:
    limit = min(cfg.part_bytes, file_limit - 4096)
    if limit < 4096:
        raise EnqueteError("Limite de pièces jointes trop faible pour publier le rapport.")
    prefix = f"enquete_{meta.job_id}"
    history = Parts(work.path.parent, prefix + "_historique", limit, cfg.max_parts - 1)
    count = 0
    last_cid = None
    try:
        cursor = work.db.execute(
            "SELECT m.*,s.reasons,c.label FROM messages m JOIN selected s ON s.mid=m.mid "
            "JOIN channels c ON c.cid=m.cid ORDER BY c.label COLLATE NOCASE,m.cid,m.mid"
        )
        while rows := cursor.fetchmany(100):
            for row in rows:
                if work.size >= cfg.disk_bytes - 2 * 1024**2:
                    history.truncate("budget de stockage temporaire atteint")
                    break
                if last_cid != row["cid"]:
                    if not history.write("\n" + "=" * 76 + "\nSALON / FIL : " + line(row["label"]) +
                                         f" | ID {row['cid']}\n" + "=" * 76 + "\n"):
                        break
                    last_cid = row["cid"]
                if not history.write(record_block(row, meta.guild_id, count + 1)):
                    break
                count += 1
            if history.truncated:
                break
            await asyncio.sleep(0)
        history.write("\n\n" + "=" * 76 + "\nÉVÉNEMENTS / JOURNAUX EXISTANTS (sources séparées)\n" + "=" * 76 +
                      "\nmessages_created = création ; messages_edited = modification ; messages_deleted = suppression."
                      "\nreactions = ajout/retrait d'une réaction ; voice = connexion/départ/déplacement vocal."
                      "\nold_content / new_content = ancien / nouveau texte déjà conservé par le bot."
                      "\n*_id = identifiant Discord ; un compteur non daté n'est pas une chronologie."
                      "\nCes traces sont distinctes des messages encore disponibles et ne s'ajoutent pas à leurs compteurs.\n")
        for i, event in enumerate(work.db.execute("SELECT * FROM events ORDER BY created,eid"), 1):
            if work.size >= cfg.disk_bytes - 2 * 1024**2:
                history.truncate("budget de stockage temporaire atteint")
            if not history.write(
                f"\nSource : {line(event['source'])} | Référence : {line(event['eid'])}\n"
                f"Date : {stamp(event['created'])}\n" +
                quote(json.dumps(json.loads(event["payload"]), ensure_ascii=False, indent=2)) + "\n"
            ):
                break
            if i % 100 == 0:
                await asyncio.sleep(0)
    finally:
        history.close()

    stats = work.statistics(meta.target_id)
    coverage = work.db.execute("SELECT * FROM channels ORDER BY label,cid").fetchall()
    unfinished = [r for r in coverage if r["status"] not in {"LU", "EXCLU", "CONTENEUR"}]
    summary_lines = [
        "=" * 76, "RAPPORT FACTUEL DE MODÉRATION — CONFIDENTIEL STAFF", "=" * 76,
        f"Dossier : {meta.job_id}",
        f"Serveur : {line(meta.guild_name)} | ID {meta.guild_id}",
        f"Compte recherché : {meta.target_id} | Demandeur : {meta.requester_id}",
        f"Motif déclaré : {line(meta.reason)}",
        f"Rapport généré : {stamp(datetime.now(UTC))}",
        f"Période : {stamp(meta.window.start)} inclus → {stamp(meta.window.end)} exclu",
        "Dates affichées en Europe/Paris, avec décalage UTC. Exceptions anciennes signalées.",
        "",
        "1. À LIRE EN PREMIER",
        "Ce document n'est ni un verdict ni un profil personnel.",
        "Une citation peut parler d'un homonyme. Une abréviation/typo est une piste, pas une preuve.",
        "Les propos de tiers sont des propos rapportés, jamais des faits validés sur la personne.",
        "Aucun score de dangerosité, de culpabilité ou de réputation n'est calculé.",
        "La collecte n'est pas un instantané atomique : les messages peuvent changer pendant le travail.",
        "",
        "COUVERTURE DE LA COLLECTE : " + ("PARTIELLE / INCIDENTS À LIRE" if unfinished or meta.partial else
                                          "PARCOURS TERMINÉ DANS LE PÉRIMÈTRE AUTORISÉ"),
        "EXPORT TXT : " + ("PARTIEL — " + history.truncation_reason.upper() if history.truncated else "TERMINÉ"),
        f"Messages retenus/contextuels effectivement écrits : {count}",
        "« Parcours terminé » ne signifie jamais « toute l'activité de cette personne sur Discord ».",
        "",
        "2. IDENTITÉ DISCORD ET ÉTAT OBSERVÉ",
    ]
    summary_lines.extend(f"{line(k)} : {line(v)}" for k, v in meta.identity.items())
    summary_lines.extend(["", "3. NOMS ET ALIAS RECHERCHÉS"])
    summary_lines.extend(f"- {line(a.value)} | origine : {line(a.source)}" for a in meta.aliases)
    summary_lines.append("Recherche approximative : " + ("activée, résultats étiquetés PISTE" if meta.approximate else "désactivée"))
    summary_lines.extend("Ambiguïté : " + line(c) for c in meta.collisions)
    summary_lines.extend([
        "",
        "4. STATISTIQUES MESURÉES — UNIQUEMENT DANS LES MESSAGES LUS",
        f"Messages lus et dédupliqués, tous auteurs : {stats['scanned']}",
        f"Messages de la cible attribués par ID : {stats['authored']}",
        f"Messages correspondants, toutes méthodes confondues : {stats['matching']}",
        f"Messages ajoutés seulement pour le contexte : {stats['context']}",
        f"Messages de la cible portant une date de modification : {stats['edited_snapshot']}",
        f"Pièces jointes sur les messages de la cible : {stats['attachments']}",
        f"Réactions actuellement présentes sur ses messages : {stats['reactions_received_snapshot']}",
        f"Premier message de la cible retrouvé : {stamp(stats['first'])}",
        f"Dernier message de la cible retrouvé : {stamp(stats['last'])}",
        "Ces bornes ne prouvent ni la première ni la dernière activité réelle de la personne.",
        "Les catégories suivantes se recouvrent : leur somme peut dépasser le nombre de messages.",
    ])
    summary_lines.extend(f"  {reason_label(k)} [{k}] : {v}" for k, v in sorted(stats["reasons"].items()))
    summary_lines.append("Messages de la cible par mois (Paris) :")
    summary_lines.extend(f"  {k} : {v}" for k, v in sorted(stats["months"].items()))
    summary_lines.append("Messages de la cible par salon / fil :")
    labels = {r["cid"]: r["label"] for r in coverage}
    summary_lines.extend(f"  {line(labels.get(k, str(k)))} [{k}] : {v}" for k, v in stats["channels"].most_common())
    summary_lines.extend(["", "5. COUVERTURE DÉTAILLÉE PAR SALON / FIL",
                          "LU = pagination terminée ; EXCLU = hors périmètre ; autres états = limites/incidents.",
                          "Les noms des espaces non autorisés ne sont pas divulgués."])
    summary_lines.extend(
        f"{r['status']:12} | {r['scanned']:8} messages | {line(r['label'])} [{r['cid']}] | {line(r['detail'])}"
        for r in coverage
    )
    summary_lines.extend(["", "6. MÉTHODE, LIMITES ET AVERTISSEMENTS",
        f"Contexte : au plus {meta.context} messages avant/après, dans {cfg.context_minutes} minutes de chaque côté.",
        "L'original d'une réponse est aussi joint lorsqu'il a été lu dans le même salon autorisé.",
        "Messages classés par salon puis par date croissante ; chaque message n'apparaît qu'une fois.",
        "Mentions explicites/réponses/auteurs : rattachement par identifiant, pas par nom affiché.",
        "Noms : casse, accents, espaces et séparateurs normalisés ; frontières de mots conservées.",
        "Pistes : préfixes d'au moins 4 lettres ou une édition de distance pour un nom assez long.",
        "Les noms renvoyés avec un ancien message ne constituent pas un historique fiable des pseudos.",
        "Pas de lecture des MP, d'autres serveurs, de comptes liés, d'adresses personnelles ou de présence.",
        "Pas d'écoute, d'enregistrement ni de transcription du vocal ; pas d'historique vocal reconstitué.",
        "Pas de téléchargement, OCR ou exécution des pièces jointes ; leurs liens peuvent expirer.",
        "Le contenu d'un transfert n'est pas importé depuis un autre espace.",
        "Messages supprimés et anciennes versions : absents sauf traces déjà disponibles et autorisées.",
        "Les journaux Discord ont leur propre rétention ; leur absence ne prouve pas l'absence d'événement.",
        "Réactions : état lors de la lecture, sans date d'ajout. Totaux ≠ nombre de personnes distinctes.",
        "Recherche des réactions données : " + ("activée" if meta.deep_reactions else
                                               "non demandée ; seuls compteurs et traces existantes sont inclus"),
        "Comptes bots/webhooks signalés ; leurs textes peuvent citer des tiers ou recopier des événements.",
        "Les fichiers de sauvegarde internes et les précédents rapports du bot ne sont pas réingérés.",
        "Les secrets évidents sont masqués au mieux (emails/IP/téléphones internationaux/jetons).",
        "Ce masquage n'est pas exhaustif. Ne pas redistribuer le rapport hors des destinataires autorisés.",
    ])
    summary_lines.extend("- " + line(n) for n in meta.notes)
    summary_lines.extend(["", "7. FICHIERS ET CONTRÔLE D'INTÉGRITÉ",
                          "Lire les fichiers historique dans l'ordre numérique. SHA-256 contrôle la copie, pas la véracité."])
    for path in history.paths:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        summary_lines.append(f"{path.name} | {path.stat().st_size} octets | SHA-256 {digest}")
    summary_lines.extend([
        "", f"Suppression automatique des publications demandée après {cfg.retention_days} jours.",
        "Les copies téléchargées par les destinataires ne peuvent pas être supprimées par le bot.",
        f"/enquete-purger identifiant:{meta.job_id} permet une suppression anticipée.",
        "=" * 76, "FIN DE LA SYNTHÈSE",
    ])
    # La couverture de milliers de fils peut elle-même dépasser un fichier.
    summary = Parts(work.path.parent, prefix + "_synthese", limit, 256)
    try:
        for index, text in enumerate(summary_lines):
            if not summary.write(text + "\n"):
                raise EnqueteError("La synthèse dépasse la limite de sécurité ; publication refusée.")
            if index % 200 == 0:
                await asyncio.sleep(0)
    finally:
        summary.close()
    if work.size > cfg.disk_bytes:
        raise EnqueteError("Budget disque dépassé pendant la synthèse ; publication refusée et fichiers temporaires retirés.")
    return summary.paths + history.paths


class Receipts:
    """Journal persistant minimal : IDs de publications et échéances, aucun extrait."""
    def __init__(self, path: Path):
        if path.is_symlink():
            raise EnqueteError("Le journal des publications ne doit pas être un lien symbolique.")
        self.db = sqlite3.connect(str(path), timeout=10)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                jid TEXT PRIMARY KEY, gid INTEGER NOT NULL, uid INTEGER NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                mid INTEGER PRIMARY KEY, jid TEXT NOT NULL, cid INTEGER NOT NULL
            );
        """)

    def create(self, jid: str, gid: int, uid: int, now: float, days: int):
        self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (jid, gid, uid, now, now + days * 86400, "en cours"))
        self.db.commit()

    def update(self, jid: str, status: str):
        self.db.execute("UPDATE jobs SET status=? WHERE jid=?", (status, jid))
        self.db.commit()

    def sent(self, jid: str, cid: int, mid: int):
        self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?)", (mid, jid, cid))
        self.db.commit()

    def get(self, jid: str, gid: int):
        return self.db.execute("SELECT * FROM jobs WHERE jid=? AND gid=?", (jid, gid)).fetchone()

    def deliveries(self, jid: str):
        return self.db.execute("SELECT * FROM deliveries WHERE jid=? ORDER BY mid", (jid,)).fetchall()

    def due(self, now: float):
        return self.db.execute("SELECT * FROM jobs WHERE expires<=?", (now,)).fetchall()

    def forget_delivery(self, mid: int):
        self.db.execute("DELETE FROM deliveries WHERE mid=?", (mid,))
        self.db.commit()

    def forget_job(self, jid: str):
        if not self.deliveries(jid):
            self.db.execute("DELETE FROM jobs WHERE jid=?", (jid,))
            self.db.commit()

    def close(self):
        self.db.close()
