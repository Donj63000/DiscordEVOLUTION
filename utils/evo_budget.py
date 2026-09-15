"""Registre en nano-USD, réservé dans #console avant chaque génération payante."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import logging
import re
import uuid

from utils.evo_config import EvoConfig, EvoError

log = logging.getLogger(__name__)
INPUT_NANO_PER_TOKEN = 250
OUTPUT_NANO_PER_TOKEN = 1200
SCOPE = "evolution-evo-v1"
SCHEMA_VERSION = 1


class BudgetLimitError(EvoError):
    """L'enveloppe ou le quota ne permet pas une nouvelle réservation."""


def quote(input_tokens: int, output_tokens: int) -> int:
    if any(type(n) is not int or not 0 <= n <= 100000 for n in (input_tokens, output_tokens)):
        raise EvoError("Comptage de tokens invalide : appel bloqué.")
    base = input_tokens * INPUT_NANO_PER_TOKEN + output_tokens * OUTPUT_NANO_PER_TOKEN
    return (base * 115 + 99) // 100


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("timezone")
    return stamp.astimezone(timezone.utc)


def _nonnegative(value):
    return type(value) is int and 0 <= value < 2**63


def _empty_bucket():
    return {"used": 0, "calls": 0, "blocked": False}


def validate_snapshot(payload, guild_id: int) -> dict:
    """Je recalcule chaque compteur depuis les réservations conservées."""
    try:
        required = {"schema_version", "scope", "guild_id", "ledger_id", "revision",
                    "created_at", "buckets", "reservations"}
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("schema")
        if (type(payload["schema_version"]) is not int
                or payload["schema_version"] != SCHEMA_VERSION
                or payload["scope"] != SCOPE or payload["guild_id"] != str(guild_id)
                or not isinstance(payload["ledger_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", payload["ledger_id"])
                or not _nonnegative(payload["revision"]) or payload["revision"] < 1):
            raise ValueError("identity")
        _timestamp(payload["created_at"])
        buckets, records = payload["buckets"], payload["reservations"]
        if not isinstance(buckets, dict) or not isinstance(records, dict):
            raise ValueError("collections")
        expected, requests = {}, set()
        record_fields = {"request_key", "month_bucket", "day_bucket", "user_bucket",
                         "maximum", "charged", "input_tokens", "output_tokens", "created_at"}
        for identifier, record in records.items():
            if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier)
                    or not isinstance(record, dict) or set(record) != record_fields):
                raise ValueError("reservation")
            request_key = record["request_key"]
            if (not isinstance(request_key, str) or not 1 <= len(request_key) <= 200
                    or request_key in requests):
                raise ValueError("duplicate")
            requests.add(request_key)
            stamp = _timestamp(record["created_at"])
            month, day = "month:" + stamp.strftime("%Y-%m"), "day:" + stamp.strftime("%Y-%m-%d")
            user = record["user_bucket"]
            if (record["month_bucket"] != month or record["day_bucket"] != day
                    or not isinstance(user, str)
                    or not re.fullmatch(re.escape(day) + r":user:[0-9a-f]{24}", user)
                    or not _nonnegative(record["maximum"]) or record["maximum"] < 1):
                raise ValueError("reservation values")
            charged = record["charged"]
            if charged is None:
                if record["input_tokens"] is not None or record["output_tokens"] is not None:
                    raise ValueError("pending usage")
                amount = record["maximum"]
            else:
                if (not _nonnegative(charged)
                        or charged != quote(record["input_tokens"], record["output_tokens"])):
                    raise ValueError("charged usage")
                amount = charged
            for bucket in (month, day, user):
                counter = expected.setdefault(bucket, _empty_bucket())
                counter["used"] += amount
                counter["calls"] += 1
            if charged is not None and charged > record["maximum"]:
                expected[month]["blocked"] = True
        for name, bucket in buckets.items():
            if (not isinstance(name, str) or not isinstance(bucket, dict)
                    or set(bucket) != {"used", "calls", "blocked"}
                    or not _nonnegative(bucket["used"]) or not _nonnegative(bucket["calls"])
                    or type(bucket["blocked"]) is not bool):
                raise ValueError("counter")
            if name.startswith("month:"):
                datetime.strptime(name, "month:%Y-%m")
            elif name not in expected or bucket["blocked"]:
                raise ValueError("unexpected counter")
            calculated = expected.get(name, _empty_bucket())
            if (bucket["used"] != calculated["used"] or bucket["calls"] != calculated["calls"]
                    or calculated["blocked"] and not bucket["blocked"]):
                raise ValueError("inconsistent counter")
        if not expected.keys() <= buckets.keys():
            raise ValueError("missing counter")
        return copy.deepcopy(payload)
    except (ValueError, TypeError, KeyError, OverflowError, EvoError):
        log.debug("evo budget rejected invalid snapshot guild_id=%s", guild_id)
        raise EvoError("Le registre budget de #console est incohérent. Aucun appel IA autorisé.") from None


class Budget:
    def __init__(self, config: EvoConfig, store, *, clock=None):
        self.config, self.store = config, store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()
        self._state = None
        self._previous = None
        self._uncertain = True
        self._closed = False
        self._epoch = 0
        self._safety = store.safety
        self._unsubmitted: set[str] = set()
        self._release_attempts: dict[str, dict] = {}

    def invalidate(self):
        """Je suspends le cache sans verrou, y compris pendant une vérification de leadership."""
        if self._state is not None:
            self._previous = self._state
        self._state = None
        self._uncertain = True
        self._epoch += 1

    def _check_continuity(self, restored):
        previous = self._state or self._previous
        if previous is None:
            return
        if (restored["ledger_id"] != previous["ledger_id"]
                or restored["created_at"] != previous["created_at"]
                or restored["revision"] < previous["revision"]
                or restored["revision"] == previous["revision"] and restored != previous):
            raise EvoError("Le registre budget a été remplacé ou reculé : vérification Staff requise.")
        for identifier, record in previous["reservations"].items():
            current = restored["reservations"].get(identifier)
            if current is None:
                if self._release_attempts.get(identifier) == record:
                    continue
                raise EvoError("Des réservations ont disparu de #console : appels IA bloqués.")
            immutable = set(record) - {"charged", "input_tokens", "output_tokens"}
            if (any(record[key] != current[key] for key in immutable)
                    or record["charged"] is not None and record != current):
                raise EvoError("Une réservation confirmée a changé dans #console : appels IA bloqués.")
        for name, counter in previous["buckets"].items():
            if counter["blocked"] and not restored["buckets"].get(name, {}).get("blocked"):
                raise EvoError("Le blocage budget a disparu de #console : contrôle Staff requis.")

    async def _restore(self):
        self._uncertain = True
        epoch = self._epoch
        payload = await self.store.load()
        if self._closed or self._epoch != epoch:
            raise EvoError("Le registre budget a été suspendu pendant sa restauration.")
        if payload is None:
            raise EvoError("Registre budget absent de #console. Initialisation Staff explicite requise.")
        restored = validate_snapshot(payload, self.config.guild_id)
        self._check_continuity(restored)
        self._state = restored
        self._uncertain = False
        pending = {identifier for identifier, record in restored["reservations"].items()
                   if record["charged"] is None}
        self._unsubmitted.intersection_update(pending)
        self._release_attempts = {
            identifier: record for identifier, record in self._release_attempts.items()
            if identifier in self._unsubmitted
        }
        log.debug("evo budget restored guild_id=%s revision=%s", self.config.guild_id, restored["revision"])

    async def _ensure_ready(self):
        if self._closed:
            raise EvoError("Le registre budget est fermé.")
        epoch = self._epoch
        try:
            await self.store.check_ready()
            if self._closed or self._epoch != epoch:
                raise EvoError("Le registre budget a été suspendu.")
            if self._uncertain or self._state is None:
                await self._restore()
            await self._flush_safety()
        except BaseException:
            self._uncertain = True
            raise

    async def check_ready(self):
        async with self._lock:
            await self._ensure_ready()

    def _remember_safety(self, month, identifier=None, usage=None):
        self._safety["ledger_id"] = self._state["ledger_id"]
        self._safety["months"].add(month)
        if identifier is not None:
            self._safety["usage"][identifier] = usage

    def _clear_safety(self):
        self._safety["ledger_id"] = None
        self._safety["months"].clear()
        self._safety["usage"].clear()

    async def _flush_safety(self):
        """Je confirme les anomalies connues avant de permettre une reprise."""
        if not self._safety["months"]:
            return
        state = self._state
        if self._safety["ledger_id"] != state["ledger_id"]:
            raise EvoError("Une anomalie fournisseur reste à sauvegarder : registre remplacé, IA bloquée.")
        candidate = copy.deepcopy(state)
        for identifier, usage in self._safety["usage"].items():
            record = candidate["reservations"].get(identifier)
            if record is None:
                raise EvoError("La réservation d'une anomalie fournisseur a disparu : IA bloquée.")
            actual = quote(*usage)
            if record["charged"] is not None:
                if (record["input_tokens"], record["output_tokens"]) != usage:
                    raise EvoError("Une anomalie fournisseur contredit le registre : IA bloquée.")
                continue
            for name in (record["month_bucket"], record["day_bucket"], record["user_bucket"]):
                candidate["buckets"][name]["used"] += actual - record["maximum"]
            record.update(charged=actual, input_tokens=usage[0], output_tokens=usage[1])
        for month in self._safety["months"]:
            candidate["buckets"].setdefault(month, _empty_bucket())["blocked"] = True
        if candidate != state:
            await self._commit(candidate, expected_revision=state["revision"])
        self._clear_safety()

    async def open(self):
        async with self._lock:
            if self._closed:
                raise EvoError("Le registre budget est fermé.")
            await self.store.check_ready()
            await self._restore()
            await self._flush_safety()

    async def initialize(self):
        """Je crée uniquement un premier registre demandé explicitement par le Staff."""
        async with self._lock:
            if self._closed:
                raise EvoError("Le registre budget est fermé.")
            if self._safety["months"]:
                raise EvoError("Une anomalie budget attend sa sauvegarde : initialisation interdite.")
            await self.store.check_ready()
            if self._state is not None or self._previous is not None or await self.store.load() is not None:
                raise EvoError("Le registre budget existe déjà : aucune remise à zéro autorisée.")
            payload = {
                "schema_version": SCHEMA_VERSION, "scope": SCOPE,
                "guild_id": str(self.config.guild_id), "ledger_id": uuid.uuid4().hex,
                "revision": 1, "created_at": self.clock().astimezone(timezone.utc).isoformat(),
                "buckets": {}, "reservations": {},
            }
            await self._commit(payload, expected_revision=None)

    async def _commit(self, candidate, *, expected_revision):
        candidate["revision"] = 1 if expected_revision is None else expected_revision + 1
        validate_snapshot(candidate, self.config.guild_id)
        epoch = self._epoch
        try:
            confirmed = await self.store.save(candidate, expected_revision=expected_revision)
            if self._closed or self._epoch != epoch:
                raise EvoError("Le registre budget a été suspendu pendant la sauvegarde.")
            if validate_snapshot(confirmed, self.config.guild_id) != candidate:
                raise EvoError("La révision budget n'a pas été confirmée dans #console.")
        except BaseException as exc:
            self._uncertain = True
            log.debug("evo budget write uncertain guild_id=%s revision=%s error=%s",
                      self.config.guild_id, candidate["revision"], type(exc).__name__)
            raise
        self._state = candidate
        self._uncertain = False
        log.debug("evo budget committed guild_id=%s revision=%s",
                  self.config.guild_id, candidate["revision"])

    def buckets(self, user_key: str):
        now = self.clock().astimezone(timezone.utc)
        month = "month:" + now.strftime("%Y-%m")
        day = "day:" + now.strftime("%Y-%m-%d")
        user = day + ":user:" + hashlib.sha256(user_key.encode()).hexdigest()[:24]
        return now, month, day, user

    async def reserve(self, request_key: str, user_key: str, maximum: int) -> str:
        if type(maximum) is not int or not 0 < maximum <= self.config.request_nano:
            raise EvoError("Cette demande dépasse le plafond de coût par requête.")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
            raise EvoError("Identifiant de demande invalide.")
        async with self._lock:
            await self._ensure_ready()
            now, month, day, user = self.buckets(user_key)
            state = self._state
            if any(r["request_key"] == request_key for r in state["reservations"].values()):
                raise EvoError("Cette demande a déjà été traitée : aucun double appel IA.")
            monthly = state["buckets"].get(month, _empty_bucket())
            daily = state["buckets"].get(day, _empty_bucket())
            personal = state["buckets"].get(user, _empty_bucket())
            if monthly["blocked"] or monthly["used"] + maximum > self.config.monthly_nano:
                raise BudgetLimitError("Le budget IA du mois est atteint ou mis en sécurité.")
            if daily["used"] + maximum > self.config.daily_nano:
                raise BudgetLimitError("Le petit budget IA de la journée est atteint.")
            if personal["calls"] >= self.config.user_daily_calls:
                raise BudgetLimitError("Tu as atteint ton quota IA du jour. Il se renouvelle à minuit UTC.")
            candidate = copy.deepcopy(state)
            for name in (month, day, user):
                bucket = candidate["buckets"].setdefault(name, _empty_bucket())
                bucket["used"] += maximum
                bucket["calls"] += 1
            identifier = uuid.uuid4().hex
            candidate["reservations"][identifier] = {
                "request_key": request_key, "month_bucket": month, "day_bucket": day,
                "user_bucket": user, "maximum": maximum, "charged": None,
                "input_tokens": None, "output_tokens": None, "created_at": now.isoformat(),
            }
            await self._commit(candidate, expected_revision=state["revision"])
            self._unsubmitted.add(identifier)
            return identifier

    async def mark_submitted(self, identifier: str) -> None:
        """Je retire la preuve locale avant tout départ HTTP, sans la recréer à la relecture."""
        async with self._lock:
            await self._ensure_ready()
            record = self._state["reservations"].get(identifier)
            if (record is None or record["charged"] is not None
                    or identifier not in self._unsubmitted):
                log.debug("evo budget submission refused identifier=%s", identifier)
                raise EvoError("Cette réservation ne permet pas un nouvel appel IA.")
            self._unsubmitted.remove(identifier)
            self._release_attempts.pop(identifier, None)
            log.debug("evo budget marked submitted identifier=%s", identifier)

    async def release_unsubmitted(self, identifier: str) -> int:
        """Je libère seulement une réservation locale dont aucun appel HTTP n'a commencé."""
        async with self._lock:
            await self._ensure_ready()
            state = self._state
            record = state["reservations"].get(identifier)
            if (record is None or record["charged"] is not None
                    or identifier not in self._unsubmitted):
                log.debug("evo budget unused release refused identifier=%s", identifier)
                raise EvoError("L'absence d'appel payant n'est pas prouvée : réservation conservée.")
            candidate = copy.deepcopy(state)
            del candidate["reservations"][identifier]
            for name in (record["month_bucket"], record["day_bucket"], record["user_bucket"]):
                bucket = candidate["buckets"][name]
                bucket["used"] -= record["maximum"]
                bucket["calls"] -= 1
                if bucket == _empty_bucket():
                    del candidate["buckets"][name]
            self._release_attempts[identifier] = copy.deepcopy(record)
            await self._commit(candidate, expected_revision=state["revision"])
            self._unsubmitted.remove(identifier)
            self._release_attempts.pop(identifier, None)
            log.debug("evo budget unused reservation released identifier=%s maximum=%s",
                      identifier, record["maximum"])
            return record["maximum"]

    async def settle(self, identifier: str, input_tokens: int, output_tokens: int) -> None:
        async with self._lock:
            self._unsubmitted.discard(identifier)
            self._release_attempts.pop(identifier, None)
            actual = quote(input_tokens, output_tokens)
            await self._ensure_ready()
            state = self._state
            record = state["reservations"].get(identifier)
            if record is None:
                raise EvoError("Réservation introuvable : génération suivante bloquée.")
            if record["charged"] is not None:
                return
            candidate = copy.deepcopy(state)
            delta = actual - record["maximum"]
            for name in (record["month_bucket"], record["day_bucket"], record["user_bucket"]):
                candidate["buckets"][name]["used"] += delta
            candidate["reservations"][identifier].update(
                charged=actual, input_tokens=input_tokens, output_tokens=output_tokens,
            )
            anomaly = actual > record["maximum"]
            if anomaly:
                candidate["buckets"][record["month_bucket"]]["blocked"] = True
                self._remember_safety(record["month_bucket"], identifier, (input_tokens, output_tokens))
            await self._commit(candidate, expected_revision=state["revision"])
            if anomaly:
                self._clear_safety()
                raise EvoError("Le comptage fournisseur a dépassé la réservation : IA mise en sécurité.")

    async def block_current_month(self) -> None:
        async with self._lock:
            await self._ensure_ready()
            _, month, _, _ = self.buckets("")
            candidate = copy.deepcopy(self._state)
            counter = candidate["buckets"].setdefault(month, _empty_bucket())
            if counter["blocked"]:
                return
            counter["blocked"] = True
            self._remember_safety(month)
            await self._commit(candidate, expected_revision=self._state["revision"])
            self._clear_safety()

    async def can_reserve(self, user_key: str, maximum: int) -> bool:
        """Je vérifie l'enveloppe facultative sans modifier les compteurs."""
        async with self._lock:
            await self._ensure_ready()
            _, month, day, user = self.buckets(user_key)
            buckets = self._state["buckets"]
            monthly = buckets.get(month, _empty_bucket())
            daily = buckets.get(day, _empty_bucket())
            personal = buckets.get(user, _empty_bucket())
            return (
                0 < maximum <= self.config.request_nano and not monthly["blocked"]
                and monthly["used"] + maximum <= self.config.monthly_nano
                and daily["used"] + maximum <= self.config.daily_nano
                and personal["calls"] < self.config.user_daily_calls
            )

    async def status(self) -> dict:
        async with self._lock:
            await self._ensure_ready()
            _, month, day, _ = self.buckets("")
            monthly = self._state["buckets"].get(month, _empty_bucket())
            daily = self._state["buckets"].get(day, _empty_bucket())
            records = [r for r in self._state["reservations"].values() if r["month_bucket"] == month]
            return {
                "month": month[6:], "used_nano": monthly["used"], "day_nano": daily["used"],
                "calls": monthly["calls"], "blocked": monthly["blocked"],
                "pending_nano": sum(r["maximum"] for r in records if r["charged"] is None),
                "input_tokens": sum(r["input_tokens"] or 0 for r in records),
                "output_tokens": sum(r["output_tokens"] or 0 for r in records),
                "limit_nano": self.config.monthly_nano,
            }

    async def close(self):
        self._closed = True
        self._unsubmitted.clear()
        self._release_attempts.clear()
        self.invalidate()
        await self.store.close()
