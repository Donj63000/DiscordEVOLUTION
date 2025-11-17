#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
import sys
import types
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

import discord
from utils.openai_config import resolve_staff_model, build_async_openai_client, resolve_reasoning_effort


def _ensure_utils() -> None:
    """
    Renforce prudemment discord.utils SANS remplacer le module
    ni altérer des symboles critiques utilisés par discord.py.

    - N'ajoute que des fallbacks *si* ils manquent réellement.
    - N'opère qu'après l'import de discord.ext.commands.
    - Ne modifie pas sys.modules["discord.utils"].
    """
    try:
        utils = getattr(discord, "utils", None)
    except Exception:
        return
    if utils is None:
        return

    # Fallback très conservateur pour is_inside_class (si absent).
    if not hasattr(utils, "is_inside_class"):
        def _is_inside_class(obj: Any) -> bool:
            qn = getattr(obj, "__qualname__", "")
            return "." in qn and "<locals>" not in qn
        try:
            setattr(utils, "is_inside_class", _is_inside_class)  # type: ignore[attr-defined]
        except Exception:
            pass

    # Fallback très conservateur pour evaluate_annotation (si absent).
    if not hasattr(utils, "evaluate_annotation"):
        def _evaluate_annotation(annotation: Any,
                                 globalns: Optional[Dict[str, Any]] = None,
                                 localns: Optional[Dict[str, Any]] = None,
                                 cache: Optional[Dict[str, Any]] = None):
            if isinstance(annotation, str):
                try:
                    return eval(annotation, globalns or {}, localns or {})
                except Exception:
                    return annotation
            return annotation

        try:
            setattr(utils, "evaluate_annotation", _evaluate_annotation)  # type: ignore[attr-defined]
        except Exception:
            pass


from discord.ext import commands  # IMPORTANT : importer avant de patcher
_ensure_utils()  # Appliquer des fallbacks uniquement après l'import ci-dessus

try:
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - OpenAI SDK absent en tests
    AsyncOpenAI = None

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
ORGANISATION_CHANNEL_NAME = os.getenv("ORGANISATION_CHANNEL_NAME", "organisation")
DEFAULT_MODEL = resolve_staff_model()
SESSION_TIMEOUT = int(os.getenv("ORGANISATION_TIMEOUT", "240"))
PLANNER_TEMPERATURE = float(os.getenv("ORGANISATION_PLANNER_TEMP", "0.25"))
ANNOUNCE_TEMPERATURE = float(os.getenv("ORGANISATION_ANNOUNCE_TEMP", "0.5"))

# Backend IA : "auto" (défaut), "responses" ou "chat"
BACKEND_MODE = os.getenv("ORGANISATION_BACKEND", "auto").strip().lower()
if BACKEND_MODE not in {"auto", "responses", "chat"}:
    BACKEND_MODE = "auto"

PLANNER_SCHEMA = {
    "name": "OrganisationPlanner",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "collected"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["ask", "ready", "cancel"],
            },
            "next_question": {
                "type": ["string", "null"],
            },
            "collected": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "event_type": {"type": "string"},
                    "date_time": {"type": "string"},
                    "duration": {"type": "string"},
                    "location": {"type": "string"},
                    "objectives": {"type": "string"},
                    "requirements": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
            "summary": {"type": ["string", "null"]},
        },
    },
}

ANNOUNCE_SCHEMA = {
    "name": "OrganisationAnnouncement",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "body"],
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "cta": {"type": ["string", "null"]},
            "mentions": {"type": ["string", "null"]},
            "summary": {"type": ["string", "null"]},
        },
    },
}

CANCEL_KEYWORDS = {"annule", "annuler", "cancel", "stop", "fin", "abort", "stopper"}


def _extract_response_text(resp: Any) -> str:
    """Normalise les réponses de l'API Responses v1 en texte brut."""
    text = getattr(resp, "output_text", "") or ""
    if text:
        return text.strip()
    output = getattr(resp, "output", None)
    if output:
        for item in output or []:
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", "") in ("text", "output_text"):
                    value = getattr(content, "text", None) or getattr(content, "content", None)
                    if isinstance(value, str):
                        text += value
    return text.strip()


def _flatten_messages_for_chat(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Convertit la structure 'responses' (blocs) en messages texte standards
    pour Chat Completions.
    """
    flat: List[Dict[str, str]] = []
    for m in messages:
        role = str(m.get("role") or "user").strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        content = m.get("content")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    txt = str(item.get("text") or "").strip()
                    if txt:
                        parts.append(txt)
                elif isinstance(item, str):
                    txt = item.strip()
                    if txt:
                        parts.append(txt)
            text = "\n".join(parts).strip()
        elif isinstance(content, dict) and content.get("type") == "text":
            text = str(content.get("text") or "").strip()
        elif content is not None:
            text = str(content).strip()
        else:
            text = ""
        if text:
            flat.append({"role": role, "content": text})
    return flat


@dataclass
class OrganisationSession:
    user_id: int
    guild_id: int
    channel_id: int
    context: Dict[str, Any]
    messages: List[Dict[str, Any]] = field(default_factory=list)
    collected: Dict[str, Any] = field(default_factory=dict)
    summary: Optional[str] = None
    last_question: Optional[str] = None


class OrganisationCog(commands.Cog):
    """Assistant IA pour organiser rapidement des événements."""

    def __init__(self, bot: commands.Bot) -> None:
        _ensure_utils()
        self.bot = bot
        self.model = DEFAULT_MODEL
        self._sessions: Dict[Tuple[int, int], OrganisationSession] = {}
        self._client: Optional[AsyncOpenAI] = build_async_openai_client(AsyncOpenAI)

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _planner_system_prompt(self, guild_name: str) -> str:
        return (
            "Tu aides le staff de la guilde Evolution a preparer une sortie. "
            "Tu mènes l'entretien en posant UNE question claire a la fois pour recolter toutes les informations utiles. "
            "Questions prioritaires : type d'evenement (donjon, drop, PvP, autre), date/heure, point de rendez-vous, objectifs, restrictions (niveau, classes), ressources a prevoir. "
            "Tu reponds toujours en JSON respectant le schema fourni. "
            "Quand toutes les donnees sont suffisantes pour rediger l'annonce, renvoie status=\"ready\" et resume l'essentiel. "
            "Si l'utilisateur veut arreter, renvoie status=\"cancel\"."
        )

    def _announcement_system_prompt(self) -> str:
        return (
            "Genere une annonce Discord en francais pour la guilde Evolution. "
            "L'annonce doit etre concise, dynamique et facile a publier telle quelle. "
            "Format: un titre accrocheur, un corps structure (listes si besoin), un appel a l'action si utile. "
            "Propose un champ mentions (par exemple '@here') et un resume tres court pour le staff."
        )

    # ------------------------------------------------------------------
    # Appels OpenAI robustes (Responses -> fallback Chat Completions)
    # ------------------------------------------------------------------

    def _normalise_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convertit un historique role/content en format Responses v1."""
        normalised: List[Dict[str, Any]] = []
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "user").strip().lower()
            if role not in {"system", "user", "assistant"}:
                role = "user"
            content = raw.get("content")
            blocks: List[Dict[str, Any]] = []
            if isinstance(content, str):
                text = content.strip()
                if text:
                    blocks.append({"type": "text", "text": text})
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type"):
                        blocks.append(item)
                    elif isinstance(item, str):
                        text = item.strip()
                        if text:
                            blocks.append({"type": "text", "text": text})
            elif isinstance(content, dict) and content.get("type"):
                blocks.append(content)
            elif content is not None:
                text = str(content).strip()
                if text:
                    blocks.append({"type": "text", "text": text})
            if blocks:
                normalised.append({"role": role, "content": blocks})
        return normalised

    async def _call_openai_json_via_responses(
        self,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        *,
        temperature: float,
    ) -> Dict[str, Any]:
        """Chemin principal via Responses API avec JSON Schema."""
        if not self._client:
            raise RuntimeError("OPENAI_API_KEY absent - fonctionnalite indisponible.")
        payload = self._normalise_responses_input(messages)
        if not payload:
            raise RuntimeError("Prompt vide pour OpenAI.")
        request = {
            "model": self.model,
            "input": payload,
            "response_format": {"type": "json_schema", "json_schema": schema},
            "temperature": temperature,
        }
        reasoning = resolve_reasoning_effort(self.model)
        if reasoning:
            request["reasoning"] = reasoning
        resp = await self._client.responses.create(**request)
        text = _extract_response_text(resp)
        if not text:
            raise RuntimeError("Reponse OpenAI vide.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON invalide retourne par le modele: {exc}\nTexte: {text!r}") from exc

    async def _call_openai_json_via_chat(
        self,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        *,
        temperature: float,
    ) -> Dict[str, Any]:
        """Fallback via Chat Completions + function calling garantissant du JSON valide."""
        if not self._client:
            raise RuntimeError("OPENAI_API_KEY absent - fonctionnalite indisponible.")

        chat_messages = _flatten_messages_for_chat(messages)

        # Injection minimale pour forcer l'appel d'outil.
        sys_prompt = (
            "Tu dois appeler la fonction 'submit' avec un unique argument JSON "
            "respectant exactement le schema fourni. Ne renvoie aucun autre texte."
        )
        chat_messages.insert(0, {"role": "system", "content": sys_prompt})

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "submit",
                    "description": "Retourne la sortie finale en respectant le schema impose.",
                    "parameters": schema["schema"],  # JSON Schema entier passé en paramètres
                },
            }
        ]

        # tool_choice="required" est compatible avec les versions larges du SDK v1.
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=chat_messages,
            tools=tools,
            tool_choice="required",
            temperature=temperature,
        )

        # Extraction robuste
        try:
            choice = resp.choices[0]
        except Exception as exc:
            raise RuntimeError(f"Reponse ChatCompletion invalide: {exc}") from exc

        message = getattr(choice, "message", None)
        tool_calls = getattr(message, "tool_calls", None) if message else None

        # Cas attendu : un appel d'outil 'submit' avec des arguments JSON
        if tool_calls:
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                if fn and getattr(fn, "name", "") == "submit":
                    args = getattr(fn, "arguments", "") or ""
                    try:
                        return json.loads(args)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(f"Arguments d'outil non-JSON: {exc}\nArguments: {args!r}") from exc

        # Si pas d'appel d'outil, on tente contenu texte JSON strict
        text_content = (getattr(message, "content", "") or "").strip() if message else ""
        if text_content:
            try:
                return json.loads(text_content)
            except json.JSONDecodeError:
                pass

        raise RuntimeError("Le modele n'a pas renvoye de JSON exploitable (ni tool_call ni contenu JSON).")

    async def _call_openai_json(
        self,
        messages: List[Dict[str, Any]],
        schema: Dict[str, Any],
        *,
        temperature: float,
    ) -> Dict[str, Any]:
        """
        Essaie Responses API puis retombe sur Chat Completions si le paramètre
        'response_format' n'est pas supporté (TypeError) ou si BACKEND_MODE l'impose.
        """
        if BACKEND_MODE == "chat":
            return await self._call_openai_json_via_chat(messages, schema, temperature=temperature)

        if BACKEND_MODE in {"auto", "responses"}:
            try:
                return await self._call_openai_json_via_responses(messages, schema, temperature=temperature)
            except TypeError as exc:
                # Cas rencontré : "got an unexpected keyword argument 'response_format'"
                if "response_format" in str(exc):
                    return await self._call_openai_json_via_chat(messages, schema, temperature=temperature)
                raise
            except Exception as exc:
                # En mode auto, si Responses échoue pour d'autres raisons (ex: version serveur),
                # on tente le fallback chat.
                if BACKEND_MODE == "auto":
                    return await self._call_openai_json_via_chat(messages, schema, temperature=temperature)
                raise

        # Sécurité : fallback final
        return await self._call_openai_json_via_chat(messages, schema, temperature=temperature)

    # ------------------------------------------------------------------
    # Logique de session
    # ------------------------------------------------------------------

    async def _planner_step(
        self,
        session: OrganisationSession,
        *,
        initial: bool = False,
        user_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        if initial and not session.messages:
            session.messages.append({"role": "system", "content": self._planner_system_prompt(session.context.get("guild", "Evolution"))})
            session.messages.append({
                "role": "user",
                "content": (
                    "Demarre l'entretien et pose d'abord une question sur le type d'evenement a organiser. "
                    "Rappelle que l'utilisateur peut taper 'annule' pour arreter."
                ),
            })
        elif user_message is not None:
            session.messages.append({"role": "user", "content": user_message})

        payload = await self._call_openai_json(session.messages, PLANNER_SCHEMA, temperature=PLANNER_TEMPERATURE)

        collected = payload.get("collected") or {}
        for key, value in collected.items():
            if isinstance(value, str) and value.strip():
                session.collected[key] = value.strip()

        session.summary = payload.get("summary") or session.summary
        question = payload.get("next_question")

        if question:
            session.last_question = question.strip()
            session.messages.append({"role": "assistant", "content": session.last_question})

        return payload

    async def _generate_announcement(
        self,
        session: OrganisationSession,
        *,
        organiser: str,
        channel: discord.TextChannel,
    ) -> Dict[str, Any]:
        context_blob = json.dumps(
            {
                "organiser": organiser,
                "channel": channel.name,
                "collected": session.collected,
                "summary": session.summary,
            },
            ensure_ascii=False,
        )
        messages = [
            {"role": "system", "content": self._announcement_system_prompt()},
            {
                "role": "user",
                "content": (
                    "Redige une annonce parfaitement exploitable pour Discord. "
                    "Respecte le schema JSON fourni. Voici les informations: " + context_blob
                ),
            },
        ]
        return await self._call_openai_json(messages, ANNOUNCE_SCHEMA, temperature=ANNOUNCE_TEMPERATURE)

    def _format_announcement(
        self,
        ctx: commands.Context,
        payload: Dict[str, Any],
    ) -> Tuple[Optional[str], discord.Embed]:
        title = (payload.get("title") or "Organisation Evolution").strip()
        body = (payload.get("body") or "").strip()
        cta = (payload.get("cta") or "").strip()
        mentions = (payload.get("mentions") or "").strip() or None
        embed = discord.Embed(title=title, description=body, color=discord.Color.blurple())
        if cta:
            embed.add_field(name="Action", value=cta, inline=False)
        embed.set_footer(text=f"Pilote par {ctx.author.display_name}")
        return mentions, embed

    # ------------------------------------------------------------------
    # Commande principale
    # ------------------------------------------------------------------

    @commands.command(name="organisation")
    @commands.has_role(STAFF_ROLE_NAME)
    async def organisation_cmd(self, ctx):
        if not self._client:
            await ctx.reply(
                "[! ] `OPENAI_API_KEY` n'est pas configure. Impossible de lancer l'assistant d'organisation.",
                mention_author=False,
            )
            return
        if not ctx.guild:
            await ctx.reply("[! ] Commande uniquement disponible sur un serveur.", mention_author=False)
            return

        key = (ctx.guild.id, ctx.author.id)
        if key in self._sessions:
            await ctx.reply("[! ] Tu as deja une organisation en cours. Termine-la ou attends son expiration.", mention_author=False)
            return

        session = OrganisationSession(
            user_id=ctx.author.id,
            guild_id=ctx.guild.id,
            channel_id=ctx.channel.id,
            context={"guild": ctx.guild.name, "organiser": ctx.author.display_name},
        )
        self._sessions[key] = session

        if getattr(ctx.channel, "name", None) != ORGANISATION_CHANNEL_NAME:
            await ctx.send(
                f"[info] Session lancee ici, mais pense a partager le resultat dans #{ORGANISATION_CHANNEL_NAME}.",
                delete_after=20,
            )

        try:
            payload = await self._planner_step(session, initial=True)
        except Exception as exc:
            self._sessions.pop(key, None)
            await ctx.reply(f"[! ] Impossible de demarrer la conversation IA : {exc}", mention_author=False)
            return

        await ctx.send(
            f"[assistant] Assistant organisation pour {ctx.author.mention}. Reponds dans ce salon ou tape `annule` pour stopper.",
            mention_author=False,
        )

        next_question = payload.get("next_question") or session.last_question
        if next_question:
            await ctx.send(next_question, mention_author=False)

        try:
            while True:
                try:
                    user_msg = await self.bot.wait_for(
                        "message",
                        timeout=SESSION_TIMEOUT,
                        check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    )
                except asyncio.TimeoutError:
                    await ctx.send("[timeout] Temps d'attente depasse, organisation annulee.")
                    break

                content = (user_msg.content or "").strip()
                if content.lower() in CANCEL_KEYWORDS:
                    await ctx.send("[stop] Organisation annulee.")
                    break

                try:
                    payload = await self._planner_step(session, user_message=content)
                except Exception as exc:
                    await ctx.send(f"[! ] Erreur OpenAI : {exc}")
                    break

                status = payload.get("status")
                if status == "cancel":
                    await ctx.send("[stop] L'assistant a mis fin a la session.")
                    break
                if status == "ready":
                    try:
                        announcement = await self._generate_announcement(
                            session,
                            organiser=ctx.author.display_name,
                            channel=ctx.channel,
                        )
                    except Exception as exc:
                        await ctx.send(f"[! ] Impossible de generer l'annonce : {exc}")
                        break
                    mentions, embed = self._format_announcement(ctx, announcement)
                    await ctx.send(content=mentions, embed=embed)
                    summary = announcement.get("summary") or session.summary
                    if summary:
                        await ctx.send(f"[note] Recap : {summary}", mention_author=False)
                    await ctx.send("[ok] Organisation prete ! Ajuste l'annonce si besoin puis publie-la.", mention_author=False)
                    break

                next_question = payload.get("next_question") or session.last_question
                if next_question:
                    await ctx.send(next_question, mention_author=False)
                else:
                    await ctx.send("[?] Merci de preciser davantage.")
        finally:
            self._sessions.pop(key, None)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrganisationCog(bot))
