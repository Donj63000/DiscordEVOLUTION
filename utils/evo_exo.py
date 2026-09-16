"""Partage temporaire de l'atelier prive /exo dans le seul salon choisi, public ou prive."""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import logging
import time

from utils.evo_config import EvoError
from utils.evo_safety import clean
from utils.exo_advice import RUNE_NAMES, requested_rune, session_advice
from utils.exo_engine import DISCLAIMER, STATS, normalized, weight_text
from utils.exo_feedback import batch_text
from utils.exo_workshop import simulate_batch

log = logging.getLogger(__name__)
SHARE_TTL = 15 * 60
MAX_REQUESTS = 256


@dataclass
class SessionShare:
    view: object
    revision: int
    expires: float


def _cog(bot):
    cog = bot.get_cog("ExoCog")
    if cog is None or getattr(cog, "closed", True):
        raise EvoError("L'atelier /exo est momentanément indisponible.")
    return cog


def _key(ctx):
    return ctx.guild.id, ctx.member.id, ctx.channel.id


def _view(ctx, cog):
    view = cog.views.get((ctx.guild.id, ctx.member.id))
    if view is not None and view.evo_uncertain:
        raise EvoError("Le panneau privé doit être réactualisé ou rouvert avec /exo avant un nouveau partage.")
    if (
        view is None or not view.active or view.owner_id != ctx.member.id
        or view.guild_id != ctx.guild.id
        or not getattr(getattr(view.message, "flags", None), "ephemeral", False)
    ):
        raise EvoError("Ouvre ton atelier privé avec /exo avant de le partager avec Evo.")
    return view


def _shared(ctx, cog, view):
    ctx.check()
    grant = cog.evo_shares.get(_key(ctx))
    if (
        grant is None or grant.view is not view or not view.active or view.evo_uncertain
        or cog.views.get((ctx.guild.id, ctx.member.id)) is not view
        or view.owner_id != ctx.member.id or view.guild_id != ctx.guild.id
        or grant.expires <= time.monotonic() or grant.revision != view.session.revision
        or not getattr(getattr(view.message, "flags", None), "ephemeral", False)
    ):
        cog.evo_shares.pop(_key(ctx), None)
        log.debug("evo_exo: share_unavailable owner=%s channel=%s", ctx.member.id, ctx.channel.id)
        raise EvoError(
            "Aucun état d'atelier actuellement partagé ici. Utilise /evo-exo partager:True ; "
            "une modification depuis le panneau privé exige un nouveau partage."
        )
    return grant


async def share_session(ctx) -> dict:
    """Autorise l'etat actuel dans ce salon accessible au membre et au bot jusqu'a expiration."""
    ctx.check()
    cog = _cog(ctx.bot)
    view = _view(ctx, cog)
    async with view.lock:
        ctx.check()
        if _view(ctx, cog) is not view:
            raise EvoError("L'atelier a été remplacé. Relance le partage de son état actuel.")
        clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)
        cog.evo_shares[_key(ctx)] = SessionShare(
            view, view.session.revision, time.monotonic() + SHARE_TTL,
        )
        log.debug("evo_exo: shared owner=%s channel=%s revision=%s", ctx.member.id,
                  ctx.channel.id, view.session.revision)
        return {"partage": True, "salon_id": str(ctx.channel.id),
                "duree_max_secondes": SHARE_TTL, "expiration_avec_atelier": True}


def clear_member_shares(bot, guild_id: int, member_id: int) -> None:
    """Revoque immediatement tous les salons partages par ce membre."""
    cog = bot.get_cog("ExoCog")
    if cog is None:
        return
    for key in list(cog.evo_shares):
        if key[:2] == (guild_id, member_id):
            cog.evo_shares.pop(key, None)
    log.debug("evo_exo: member_shares_cleared guild=%s owner=%s", guild_id, member_id)


def clear_shares(bot) -> None:
    """Retire les consentements quand Evo oublie son contexte ou perd la connexion."""
    cog = bot.get_cog("ExoCog")
    if cog is not None:
        cog.evo_shares.clear()
    log.debug("evo_exo: all_shares_cleared")


async def revoke_session(ctx) -> dict:
    """La revocation reste possible sans atelier actif et ne divulgue aucun etat."""
    clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)
    return {"partage": False}


def _protect_publication(ctx, cog, view, grant) -> None:
    """Lie la redaction et la publication au consentement exact utilise pour la lecture."""
    revision = grant.revision

    def validate():
        try:
            current = _shared(ctx, cog, view)
            if current is not grant or current.revision != revision:
                raise EvoError("Le partage de l'atelier a changé. Partage à nouveau son état actuel.")
        except EvoError:
            if getattr(ctx, "action_receipt", None):
                ctx.action_receipt = {
                    "action_effectuee": True, "action": "poser_rune",
                    "panneau_prive_actualise": True,
                }
            raise

    ctx.before_publish = validate


def _snapshot(session) -> dict:
    state = session.state
    return {
        "objet": clean(session.item.name, 160), "mode": session.mode,
        "revision": session.revision,
        "jets": {STATS[key].name: value for key, value in state.jets.items()},
        "maxima_naturels": {STATS[key].name: high for key, (_, high) in session.item.bounds.items()},
        "puits": None if state.sink is None else weight_text(state.sink),
        "rune_selectionnee": session.rune.name,
        "objectifs": {STATS[key].name: value for key, value in session.requirements.items()},
        **session_advice(session),
        "avertissement": DISCLAIMER,
    }


async def read_shared_session(ctx) -> dict:
    """Expose seulement le jet partage ; graine, prix, export et historique restent prives."""
    ctx.check()
    cog = _cog(ctx.bot)
    view = _view(ctx, cog)
    async with view.lock:
        grant = _shared(ctx, cog, view)
        result = _snapshot(view.session)
        _protect_publication(ctx, cog, view, grant)
        log.debug("evo_exo: read owner=%s revision=%s", ctx.member.id, view.session.revision)
        return result


async def apply_shared_rune(ctx, rune: str) -> dict:
    """Applique une seule demande actuelle a sa propre simulation puis confirme le prive."""
    log.debug("evo_exo: rune_requested owner=%s", ctx.member.id)
    ctx.check()
    requested = requested_rune(getattr(ctx, "request_text", ""))
    selected = RUNE_NAMES.get(normalized(rune)) if isinstance(rune, str) else None
    trigger = str(getattr(ctx, "trigger_id", "") or "")
    if requested is None or selected != requested or not trigger or len(trigger) > 100:
        raise EvoError("Demande explicitement une seule rune, par exemple : « pose une Ra Fo ».")
    cog = _cog(ctx.bot)
    view = _view(ctx, cog)
    async with view.lock:
        grant = _shared(ctx, cog, view)
        if trigger in view.evo_requests or getattr(ctx, "action_receipt", None):
            raise EvoError("Cette demande a déjà été traitée. Aucun nouvel essai n'a été lancé.")
        if len(view.evo_requests) >= MAX_REQUESTS:
            raise EvoError("La limite d'actions de cet atelier est atteinte. Ouvre un nouvel atelier /exo.")
        if view.session.mode != "simulation" or view.search_entries:
            raise EvoError("La pose par Evo exige le panneau de ta simulation, hors recherche d'objet.")
        view.evo_requests.add(trigger)
        before_mutation = getattr(ctx, "before_mutation", None)
        if before_mutation is not None:
            await before_mutation()
        _shared(ctx, cog, view)
        candidate = copy.deepcopy(view.session)
        candidate.rune = selected
        candidate.tab = "atelier"
        try:
            batch = simulate_batch(candidate, 1)
            candidate.notice = batch_text(batch)
        except ValueError as exc:
            log.debug("evo_exo: simulation_rejected owner=%s", ctx.member.id, exc_info=True)
            raise EvoError("Cette rune ne peut pas être simulée dans l'état partagé. Consulte ton panneau /exo.") from exc
        try:
            await view.commit_private(candidate, revision=grant.revision)
        except (Exception, asyncio.CancelledError) as exc:
            clear_member_shares(ctx.bot, ctx.guild.id, ctx.member.id)
            log.debug("evo_exo: private_commit_failed owner=%s", ctx.member.id, exc_info=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise EvoError(
                "La mise à jour du panneau privé n'a pas pu être confirmée. Aucun nouvel essai "
                "automatique : vérifie /exo puis partage à nouveau son état."
            ) from exc
        ctx.action_receipt = {
            "action_effectuee": True, "action": "poser_rune", "panneau_prive_actualise": True,
        }
        if cog.evo_shares.get(_key(ctx)) is grant:
            grant.revision = candidate.revision
        _shared(ctx, cog, view)
        _protect_publication(ctx, cog, view, grant)
        row = batch.rows[0]
        result = {
            **ctx.action_receipt, "rune": selected.name, "resultat": row["outcome"],
            "revision": candidate.revision,
            "variations": {STATS[key].name: values for key, values in batch.changes.items()},
            "puits_avant": weight_text(batch.sink_before),
            "puits_apres": weight_text(batch.sink_after),
            "avertissement": DISCLAIMER,
        }
        ctx.action_receipt = copy.deepcopy(result)
        log.debug("evo_exo: rune_committed owner=%s revision=%s", ctx.member.id, candidate.revision)
        return result
