"""Actions personnelles explicites, déléguées aux registres métier du bot."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
from types import SimpleNamespace

from utils.activity_data import ActivityError
from utils.datetime_utils import PARIS
from utils.dofus_wiki import search_key
from utils.evo_config import EvoError
from utils.evo_safety import clean

log = logging.getLogger(__name__)
_POLITE = (r"^(?:(?:bonjour|salut|evo|stp|svp|s il te plait) )*"
           r"(?:(?:peux tu|tu peux|pourrais tu|tu pourrais|veux tu|je veux que tu) )?")
_INTENTS = {
    "join": r"(?:inscris moi|inscrit moi|m inscrire|ajoute moi|m ajouter|je veux m inscrire|je m inscris)\b",
    "leave": r"(?:retire moi|desinscris moi|me desinscrire|me retirer|je veux me retirer|annule mon inscription)\b",
    "set_job": r"(?:(?:m |me )?(?:ajoute|ajouter|rajoute|rajouter|mets|met|mettre|enregistre|enregistrer|definis|definir|passe|passer|monte|monter|corrige|corriger))\b",
    "remove_job": r"(?:(?:m |me )?(?:supprime|supprimer|retire|retirer|enleve|enlever|efface|effacer))\b",
}
_WEEKDAYS = {name: index for index, name in enumerate(
    ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
)}


def _request_key(ctx):
    trigger = getattr(ctx, "trigger_id", None)
    return f"evo:{ctx.guild.id}:{ctx.member.id}:{trigger}" if type(trigger) is int else None


def _authorize(ctx, action):
    log.debug("evo personal action authorization action=%s", action)
    ctx.check()
    raw = getattr(ctx, "request_text", "").strip()
    request = search_key(raw)
    if (raw[:1] in {"'", "‘", "’"} or re.search(r'["«»“”`>\n\r]', raw)
            or re.search(r"\b(?:si|sinon|quand|lorsque|apres|avant|pendant|hypothese|supposons|"
                         r"exemple|citation|cite|disait|disent|disait|rapporte)\b", request)):
        raise EvoError("Une citation ou une condition ne déclenche pas d'action. Formule une demande directe.")
    if (not re.search(_POLITE + _INTENTS[action], request)
            or re.search(r"\b(?:ne|n|pas|jamais)\b", request)):
        raise EvoError("Demande explicitement cette modification de tes propres données pour l'effectuer.")
    if action.endswith("job"):
        if not re.search(r"\b(?:mon|mes|moi|me|m)\b", request):
            raise EvoError("Je peux modifier tes propres métiers seulement. Précise « mon profil ».")
        mentions = re.findall(r"<@!?(\d+)>", getattr(ctx, "request_text", ""))
        if any(int(identifier) != ctx.member.id for identifier in mentions):
            raise EvoError("Je peux modifier ton propre profil, pas celui d'un autre membre.")
    if len(ctx.bot.guilds) != 1 or ctx.bot.guilds[0].id != ctx.guild.id:
        raise EvoError("Les actions personnelles attendent un registre identifié sur un serveur unique.")


async def _before_mutation(ctx, action):
    _authorize(ctx, action)
    callback = getattr(ctx, "before_mutation", None)
    if callback is not None:
        await callback()
    _authorize(ctx, action)


def _published_here(ctx, record):
    ctx.check()
    channel = ctx.guild.get_channel(int(record.get("channel_id") or 0))
    if not record.get("message_id") or not ctx.readable_here(channel):
        raise EvoError("Cette sortie n'est pas publiée dans un salon accessible ici.")


def _activity(ctx, cog, query):
    date_match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", query)
    key = search_key(query[:date_match.start()] + query[date_match.end():] if date_match else query)
    records = []
    for record in cog.events_for_guild(ctx.guild.id).values():
        try:
            _published_here(ctx, record)
        except EvoError:
            continue
        if record.get("cancelled"):
            continue
        if key.isdigit():
            matches = str(record["id"]) == key
        else:
            words = set(key.split())
            weekdays = words.intersection(_WEEKDAYS)
            words -= set(_WEEKDAYS) | {"le", "la", "les", "de", "du", "des", "au", "a", "sortie", "activite",
                                      "stp", "svp", "s", "il", "te", "plait"}
            matches = bool(words or weekdays or date_match) and words <= set(search_key(record.get("titre", "")).split())
            if matches and (weekdays or date_match):
                try:
                    starts = datetime.fromisoformat(record["starts_at"]).astimezone(PARIS)
                except (KeyError, ValueError):
                    continue
                if weekdays:
                    matches = starts.weekday() in {_WEEKDAYS[day] for day in weekdays}
                if date_match:
                    matches = matches and (starts.day, starts.month) == (int(date_match[1]), int(date_match[2]))
                    if date_match[3]:
                        matches = matches and starts.year == int(date_match[3])
        if matches:
            records.append(record)
    if len(records) != 1:
        return None, {"action_effectuee": False, "a_preciser": [
            {"id": row["id"], "titre": clean(row.get("titre"), 120), "date": row.get("starts_at")}
            for row in records[:6]
        ], "message": "Précise la sortie ou son numéro ; aucune inscription modifiée."}
    return records[0], None


async def _membership(ctx, activite, action):
    _authorize(ctx, action)
    cog = ctx.bot.get_cog("ActiviteCog")
    if cog is None or not cog.initialized:
        raise EvoError("Le calendrier des activités n'est pas encore disponible.")
    raw = ctx.request_text
    explicit_id = re.search(r"(?:#|n[°o]\s*|num[eé]ro\s+|(?:sortie|activit[eé])\s+)(\d+)\b", raw, re.I)
    request = search_key(raw)
    prefix = re.search(_POLITE + _INTENTS[action], request)
    query = explicit_id[1] if explicit_id else request[prefix.end():].strip()
    raw_date = re.search(r"\b\d{1,2}/\d{1,2}(?:/\d{4})?\b", raw)
    if raw_date and not explicit_id:
        query = query.replace(search_key(raw_date[0]), raw_date[0])
    record, ambiguity = _activity(ctx, cog, query)
    if ambiguity is not None:
        return ambiguity
    selected, selection_ambiguity = _activity(ctx, cog, activite)
    if selection_ambiguity is not None or selected["id"] != record["id"]:
        raise EvoError("La sortie choisie ne correspond pas exactement à ta demande. Précise son numéro.")
    actor = SimpleNamespace(guild=ctx.guild, author=ctx.member)

    async def before_mutation():
        await _before_mutation(ctx, action)
        actor.author = ctx.member

    try:
        result = await cog.change_membership(
            actor, record["id"], action, validate_record=lambda row: _published_here(ctx, row),
            before_mutation=before_mutation, request_key=_request_key(ctx),
        )
    except ActivityError as exc:
        raise EvoError(str(exc)) from None
    receipt = {"action_effectuee": True, "action": action, "activite": result["titre"],
               "id": result["id"], "liste_attente": result["liste_attente"],
               "message": "Tu es en liste d'attente." if result["liste_attente"] else
               "Tu es inscrit à cette sortie." if action == "join" else "Tu as quitté cette sortie.",
               "source": result["source"]}
    ctx.action_receipt = receipt
    try:
        await cog.sync_membership(ctx.guild, result["id"], ctx.member.id, result["promoted"])
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        receipt["synchronisation_en_attente"] = True
        log.debug("evo membership sync pending error=%s", type(exc).__name__)
    return receipt


async def join_activity(ctx, activite):
    return await _membership(ctx, activite, "join")


async def leave_activity(ctx, activite):
    return await _membership(ctx, activite, "leave")


async def _job(ctx, metier, niveau, action):
    from job import JobPersistenceError

    _authorize(ctx, action)
    cog = ctx.bot.get_cog("JobCog")
    if cog is None or not cog.initialized:
        raise EvoError("Le registre des métiers n'est pas encore disponible.")
    canonical = cog.resolve_job_name(metier)
    if canonical is None:
        return {"action_effectuee": False, "a_preciser": cog.suggest_similar_jobs(metier),
                "message": "Métier inconnu : précise son nom ; aucun profil modifié."}
    words = search_key(ctx.request_text).split()
    named_jobs = {
        match for start in range(len(words)) for size in range(1, min(7, len(words) - start + 1))
        if (match := cog.resolve_job_name(" ".join(words[start:start + size]))) is not None
    }
    if named_jobs != {canonical}:
        raise EvoError("Précise un seul métier dans ta demande ; aucun autre métier ne sera modifié.")
    if niveau is not None:
        request = search_key(ctx.request_text)
        levels = re.findall(r"\b(?:niveau|lvl|level|a)\s+(\d{1,3})\b", request)
        levels = levels or re.findall(r"\b\d{1,3}\b", request)
        if type(niveau) is not int or {int(value) for value in levels} != {niveau}:
            raise EvoError("Le niveau choisi ne correspond pas exactement à celui de ta demande.")
    try:
        result = await cog.update_member_job(
            ctx.guild, ctx.member.id, ctx.member.display_name, canonical, niveau,
            before_mutation=lambda: _before_mutation(ctx, action), request_key=_request_key(ctx),
        )
    except JobPersistenceError as exc:
        raise EvoError(str(exc)) from None
    if not result["modifie"]:
        return {"action_effectuee": False, "metier": canonical,
                "message": "Cette demande a déjà été traitée." if result.get("deja_traite") else
                "Ton profil contient déjà ce niveau." if niveau is not None else "Ce métier n'est pas dans ton profil."}
    receipt = {"action_effectuee": True, "action": action, "metier": canonical, "niveau": niveau,
               "message": f"Ton métier {canonical} est enregistré au niveau {niveau}." if niveau is not None else
               f"Le métier {canonical} a été retiré de ton profil."}
    ctx.action_receipt = receipt
    return receipt


async def set_own_job(ctx, metier, niveau):
    return await _job(ctx, metier, niveau, "set_job")


async def remove_own_job(ctx, metier):
    return await _job(ctx, metier, None, "remove_job")


async def create_activity(ctx, titre, quand, description, lieu, capacite, duree_minutes):
    """Create through the same validated, durable service as /activite creer."""
    from utils.evo_intents import creation_values

    ctx.check()
    if type(ctx.trigger_id) is not int:
        raise EvoError("La demande de création doit avoir un identifiant Discord.")
    if len(ctx.bot.guilds) != 1 or ctx.bot.guilds[0].id != ctx.guild.id:
        raise EvoError("Le registre des activités doit appartenir à un serveur identifié.")
    try:
        values = creation_values(
            ctx.request_text, title=titre, when=quand, description=description,
            location=lieu, capacity=capacite, duration=duree_minutes,
        )
    except ValueError as exc:
        raise EvoError(str(exc)) from None
    cog = ctx.bot.get_cog("ActiviteCog")
    if cog is None or not cog.initialized:
        raise EvoError("Le calendrier des activités n'est pas encore disponible.")
    actor = SimpleNamespace(guild=ctx.guild, author=ctx.member)

    async def validate_access():
        await ctx.ensure_access()
        actor.author = ctx.member
        cog._guard(actor)
        cog._require_validated(actor)
        channel = cog._resolve_organisation_channel(ctx.guild)
        if channel is None or not ctx.readable_here(channel):
            raise EvoError("Le salon de publication doit être accessible ici.")
        for subject in (ctx.member, ctx.guild.me):
            permissions = channel.permissions_for(subject)
            if not permissions.view_channel or not permissions.send_messages:
                raise EvoError("Permission de publication manquante dans le salon d'organisation.")

    async def before_mutation():
        await validate_access()
        if ctx.before_mutation is not None:
            await ctx.before_mutation()
        await validate_access()

    try:
        await validate_access()
        record, created = await cog.create_activity(
            actor, values, creation_key=_request_key(ctx), before_mutation=before_mutation,
        )
    except ActivityError as exc:
        raise EvoError(str(exc)) from None
    # Set a truthful receipt before any Discord side effect or model call.
    receipt = {
        "action_effectuee": True, "action": "creer_activite", "id": record["id"],
        "activite": clean(record["titre"], 85), "date": record["starts_at"],
        "capacite": record["capacity"], "duree_minutes": record["duration_minutes"],
        "deja_traite": not created, "publication_en_attente": True,
        "message": f"Sortie {clean(record['titre'], 85)} enregistrée (#{record['id']}). Tu es inscrit.",
    }
    ctx.action_receipt = receipt
    try:
        role_ok, published = await asyncio.gather(
            cog._sync_legacy_roles(ctx.guild, record["id"]),
            cog.sync_card(record["id"], ctx.guild, publish=True),
        )
        current = cog.events_for_guild(ctx.guild.id).get(record["id"], record)
        receipt["publication_en_attente"] = not bool(published)
        receipt["role_en_attente"] = not bool(role_ok)
        link = cog._card_link(current)
        if published and link:
            receipt["source"] = ctx.source(link)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        receipt["synchronisation_en_attente"] = True
        log.warning("evo activity publication pending type=%s", type(exc).__name__)
    return receipt
