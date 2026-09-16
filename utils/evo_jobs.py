"""Annuaire déclaratif : un métier enregistré ne prouve pas une identité Discord."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import re
import time
from typing import Literal

import aiohttp
import discord

from utils.dofus_wiki import search_key
from utils.evo_config import EvoError
from utils.evo_safety import ToolContext, clean

log = logging.getLogger(__name__)
MAX_MEMBER_FETCHES = 40
MEMBER_CONCURRENCY = 3
MEMBER_TIMEOUT = 4
LOOKUP_TIMEOUT = 8
SNAPSHOT_TIMEOUT = 8


def canonical_job(value: str) -> str:
    """Même catalogue que /job ; seuls les pluriels simples sont rapprochés."""
    from job import ALIAS_LOOKUP, CANON_LOOKUP

    key = search_key(value)
    singular = " ".join(word[:-1] if len(word) > 3 and word.endswith("s")
                        and not word.endswith("ss") else word for word in key.split())
    for candidate in (key, singular):
        canonical = ALIAS_LOOKUP.get(candidate) or CANON_LOOKUP.get(candidate)
        if canonical:
            return canonical
    # Les métiers personnalisés restent distincts ; aucune correction approximative.
    return value.strip()


def artisan_question(question: str) -> dict | None:
    """Reconnaissance étroite d'une consultation, jamais d'une modification.

    Les demandes composées, citées, négatives ou avec une contrainte supplémentaire
    restent dans la boucle IA. Le métier doit exister dans le catalogue du bot.
    """
    from job import CANONICAL_JOBS_ORDERED

    if any(mark in question for mark in ('"', "«", "»", ";", "\n", "<", ">")):
        return None
    key = search_key(question)
    match = re.fullmatch(
        r"(?:evo )?(?:(?:est ce qu |est ce que )?(?:il y a|y a t il|y a|on a) |"
        r"(?:cherche|recherche|trouve)(?: moi)? |qui (?:est|a le metier de) )?"
        r"(?:(?:un|une|des|les) )?([a-z ]+?)"
        r"(?: (?:(?:au moins|minimum) )?(?:(?:niveau|niv|nv|lvl|level) )?(\d{1,3}))?"
        r"(?: (?:dans la guilde|sur le serveur))?(?: (?:stp|s il te plait))?", key,
    )
    if match is None:
        return None
    name = canonical_job(match[1])
    if name not in CANONICAL_JOBS_ORDERED:
        return None
    minimum = int(match[2]) if match[2] else 1
    if not 1 <= minimum <= 100:
        return None
    return {"metier": name, "niveau_min": minimum}


@dataclass(frozen=True)
class Declaration:
    # L'identifiant de fiche sert au dédoublonnage, jamais à une mention Discord.
    record_key: int | str
    owner_id: int | None
    name: str | None
    job: str
    level: int


@dataclass(frozen=True)
class Membership:
    # "absent" exige une réponse Discord Unknown Member, jamais un cache manquant.
    state: Literal["unknown", "present", "absent", "bot"]
    name: str = ""


class JobDirectory:
    """Snapshot et vérifications partagés uniquement dans la demande en cours."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._snapshot: dict | None = None
        self._members: dict[int, Membership] = {}
        self._fetches = 0
        self._deadline: float | None = None

    @staticmethod
    async def guard(ctx: ToolContext) -> None:
        await ctx.ensure_access()
        if not (ctx.config.public_member_data or ctx.config.public_job_data):
            raise EvoError("L'annuaire des métiers n'est pas autorisé pour Evo dans cette configuration.")
        if len(ctx.bot.guilds) != 1 or ctx.bot.guilds[0].id != ctx.guild.id:
            raise EvoError("L'annuaire des métiers n'est pas autorisé hors de son serveur unique.")

    async def _load(self, ctx: ToolContext) -> None:
        if self._snapshot is not None:
            return
        jobs = ctx.bot.get_cog("JobCog")
        if not getattr(jobs, "initialized", False):
            raise EvoError("L'annuaire des métiers n'est pas encore chargé.")
        reader = getattr(jobs, "read_jobs_snapshot", None)
        if not callable(reader):
            raise EvoError("L'annuaire des métiers est indisponible : module JobCog à mettre à jour.")
        async with asyncio.timeout(SNAPSHOT_TIMEOUT):
            snapshot = await reader(ctx.guild)
        if not isinstance(snapshot, dict):
            raise EvoError("L'annuaire des métiers est indisponible.")
        self._snapshot = snapshot

    def _declarations(self, job: str | None, minimum: int) -> tuple[list[Declaration], int]:
        if self._snapshot is None:
            raise EvoError("Le snapshot des métiers n'a pas été chargé.")
        rows: dict[tuple[int | str, str], Declaration] = {}
        invalid = 0
        wanted = search_key(canonical_job(job)) if job is not None else None
        for uid, record in self._snapshot.items():
            if not isinstance(record, dict) or not isinstance(record.get("jobs"), dict):
                invalid += 1
                continue
            identifier = str(uid)
            owner = (int(identifier) if identifier.isascii() and identifier.isdigit()
                     and len(identifier) <= 20 else 0)
            owner = owner if 0 < owner < 2**64 else None
            # Le nom est un libellé déclaré dans /job, pas une preuve d'identité.
            # Ne jamais retrouver un propriétaire par pseudo ou par ressemblance.
            stored_name = record.get("name")
            declared_name = None
            if isinstance(stored_name, str):
                declared_name = " ".join(clean(stored_name, 100).split()) or None
            record_key = owner if owner is not None else identifier
            for name, level in record["jobs"].items():
                if not isinstance(name, str) or not search_key(name):
                    invalid += 1
                    continue
                canonical = canonical_job(name)
                if wanted is not None and search_key(canonical) != wanted:
                    continue
                if type(level) is not int or not 1 <= level <= 100:
                    invalid += 1
                    continue
                key = (record_key, search_key(canonical))
                previous = rows.get(key)
                # Dédoublonner avant de filtrer le niveau préserve le nom d'une
                # même fiche "2"/"02", même porté par sa ligne de niveau inférieur.
                # Deux fiches au même pseudo ne sont en revanche jamais fusionnées.
                candidate = Declaration(record_key, owner, declared_name, canonical, level)
                if previous is None:
                    rows[key] = candidate
                elif level > previous.level:
                    rows[key] = replace(candidate, name=declared_name or previous.name)
                elif not previous.name and declared_name:
                    rows[key] = replace(previous, name=declared_name)
        return [row for row in rows.values() if row.level >= minimum], invalid

    @staticmethod
    def _member(member, guild_id: int, owner: int) -> Membership:
        if (getattr(member, "id", None) != owner
                or getattr(getattr(member, "guild", None), "id", guild_id) != guild_id):
            return Membership("unknown")
        if getattr(member, "bot", False):
            return Membership("bot")
        name = clean(getattr(member, "display_name", ""), 100)
        return Membership("present", name) if name else Membership("unknown")

    async def _resolve(self, ctx: ToolContext, owners: list[int]) -> None:
        missing = []
        for owner in owners:
            cached = ctx.guild.get_member(owner)
            if cached is not None:
                self._members[owner] = self._member(cached, ctx.guild.id, owner)
            elif owner not in self._members:
                missing.append(owner)
        fetch = getattr(ctx.guild, "fetch_member", None)
        if not missing or not callable(fetch):
            return
        if self._deadline is None:
            self._deadline = time.monotonic() + LOOKUP_TIMEOUT
        remaining = self._deadline - time.monotonic()
        candidates = missing[:max(0, MAX_MEMBER_FETCHES - self._fetches)]
        if remaining <= 0 or not candidates:
            return
        queue = iter(candidates)

        async def worker():
            for owner in queue:
                self._fetches += 1
                # Un échec ne sera pas relancé par une autre lecture de la même demande.
                self._members[owner] = Membership("unknown")
                try:
                    async with asyncio.timeout(MEMBER_TIMEOUT):
                        member = await fetch(owner)
                    self._members[owner] = self._member(member, ctx.guild.id, owner)
                except discord.NotFound as exc:
                    if exc.code == 10007:  # Unknown Member ; Unknown Guild ne prouve rien.
                        self._members[owner] = Membership("absent")
                except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError) as exc:
                    log.debug("evo jobs member lookup unavailable type=%s", type(exc).__name__)

        tasks = [asyncio.create_task(worker()) for _ in range(min(MEMBER_CONCURRENCY, len(candidates)))]
        try:
            async with asyncio.timeout(remaining):
                await asyncio.gather(*tasks)
        except TimeoutError:
            log.debug("evo jobs member lookup deadline reached")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def read(self, ctx: ToolContext, *, job: str | None = None, minimum: int = 1):
        await self.guard(ctx)
        async with self._lock:
            await self.guard(ctx)
            await self._load(ctx)
            rows, invalid = self._declarations(job, minimum)
            owners = list(dict.fromkeys(row.owner_id for row in rows if row.owner_id is not None))
            await self._resolve(ctx, owners)
            await self.guard(ctx)
            verified, pending = {}, {}
            for row in rows:
                membership = self._members.get(row.owner_id, Membership("unknown"))
                key = (row.record_key, search_key(row.job))
                if membership.state == "present":
                    verified[key] = {"membre": membership.name, "metier": clean(row.job, 100),
                                     "niveau": row.level}
                elif membership.state == "unknown":
                    # Une lecture du registre public reste utile même sans compte
                    # Discord confirmé. Le schéma distinct interdit de présenter
                    # ce libellé comme un membre identifié ou d'en déduire une mention.
                    pending[key] = {
                        "nom_declare": row.name, "metier": clean(row.job, 100),
                        "niveau": row.level,
                        "raison": ("identifiant_discord_absent" if row.owner_id is None
                                   else "appartenance_discord_non_confirmee"),
                    }
                # Les bots et les départs confirmés restent exclus ; rien n'est
                # supprimé du stockage par cette consultation.
            unresolved = len(pending)
            complete = unresolved == 0 and invalid == 0
            log.info(
                "evo jobs read trigger_id=%s verified=%s unresolved=%s invalid=%s fetches=%s",
                ctx.trigger_id, len(verified), unresolved, invalid, self._fetches,
            )
            return verified, pending, {
                "verification_complete": complete,
                "declarations_non_verifiees": unresolved,
                "declarations_sans_nom": sum(row["nom_declare"] is None for row in pending.values()),
                "lignes_invalides": invalid,
                "limites": ("Métiers déclarés via /job ; disponibilité en jeu non vérifiée. "
                            "Les noms déclarés non vérifiés ne prouvent ni l'identité ni la présence Discord. "
                            "Une vérification incomplète interdit de conclure à l'absence d'artisans."),
            }


def render_artisans(result: dict) -> str:
    """La réponse à une consultation simple recopie les données, sans génération."""
    if "erreur" in result:
        return clean(result["erreur"], 350) + "\nJe ne peux pas conclure à l'absence d'artisans."

    def label(value):
        # Une déclaration ou un pseudo ne peut créer un lien, titre ou mention active.
        text = " ".join(clean(value, 100).split())
        return re.sub(r"([\\`*_{}\[\]()#+.!|~<>])", r"\\\1", text).replace("@", "@\u200b")

    rows = result.get("artisans", [])
    pending = result.get("declarations_a_verifier", [])
    total = result["total"]
    total_declarations = result["total_declarations"]
    heading = f"**{label(result['metier'])} — niveau minimum {result['niveau_min']}**"
    lines = [heading]
    if rows:
        lines.append(f"{total} artisan(s) identifié(s) sur Discord :")
        lines.extend(f"• **{label(row['membre'])}** : {row['niveau']}" for row in rows)
    if pending:
        lines.append("Déclaration(s) enregistrée(s), compte Discord non vérifié :")
        lines.extend(
            f"• **{label(row['nom_declare']) if row['nom_declare'] else 'Nom non renseigné'}**"
            f" : {row['niveau']}"
            for row in pending
        )
    if not rows and not pending:
        if result.get("absence_confirmee"):
            lines.append("Aucun artisan correspondant parmi les membres vérifiés de cet annuaire.")
        else:
            lines.append("La lecture est incomplète ; aucune absence d'artisan ne peut être confirmée.")
    displayed = len(rows) + len(pending)
    if total_declarations > displayed:
        lines.append(
            f"{displayed} sur {total_declarations} déclaration(s) affichée(s) ; "
            "la suite est consultable avec /job rechercher."
        )
    if result.get("declarations_non_verifiees"):
        lines.append(
            "Les noms déclarés ne prouvent pas l'identité ni la présence actuelle sur Discord."
        )
    if result.get("lignes_invalides"):
        lines.append(
            f"{result['lignes_invalides']} ligne(s) invalide(s) : recherche incomplète, "
            "à faire vérifier par le Staff."
        )
    lines.append("Métiers déclarés via /job. Disponibilité en jeu non vérifiée.")
    return "\n".join(lines)
