from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from utils.channel_resolver import resolve_text_channel
from utils.datetime_utils import parse_fr_datetime
from utils.discord_history import fetch_channel_history
from utils.openai_config import build_async_openai_client, normalise_staff_model, resolve_reasoning_effort, resolve_staff_model

def _ensure_utils() -> None:
    try:
        utils = getattr(discord, 'utils', None)
    except Exception:
        return
    if utils is None:
        return
    if not hasattr(utils, 'is_inside_class'):

        def _is_inside_class(obj: Any) -> bool:
            qn = getattr(obj, '__qualname__', '')
            return '.' in qn and '<locals>' not in qn
        try:
            setattr(utils, 'is_inside_class', _is_inside_class)
        except Exception:
            pass
    if not hasattr(utils, 'evaluate_annotation'):

        def _evaluate_annotation(annotation: Any, globalns: Optional[Dict[str, Any]]=None, localns: Optional[Dict[str, Any]]=None, cache: Optional[Dict[str, Any]]=None):
            if isinstance(annotation, str):
                try:
                    return eval(annotation, globalns or {}, localns or {})
                except Exception:
                    return annotation
            return annotation
        try:
            setattr(utils, 'evaluate_annotation', _evaluate_annotation)
        except Exception:
            pass
_ensure_utils()
try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None
log = logging.getLogger('organisation')
STAFF_ROLE_ENV = os.getenv('IASTAFF_ROLE', 'Staff')
ORGANISATION_CHANNEL_NAME = os.getenv('ORGANISATION_CHANNEL_NAME', 'organisation')
CONSOLE_CHANNEL_NAME = os.getenv('CHANNEL_CONSOLE', os.getenv('CONSOLE_CHANNEL_NAME', 'console'))
ORGANISATION_DB_BLOCK = os.getenv('ORGANISATION_DB_BLOCK', 'organisation')
VIEW_TIMEOUT = int(os.getenv('ORGANISATION_VIEW_TIMEOUT', '900'))
MODAL_TIMEOUT = int(os.getenv('ORGANISATION_MODAL_TIMEOUT', '300'))
DEFAULT_MODEL = resolve_staff_model()
OPENAI_TIMEOUT = float(os.getenv('ORGANISATION_OPENAI_TIMEOUT', os.getenv('IASTAFF_TIMEOUT', '120')))
MAX_OUTPUT_TOKENS = int(os.getenv('ORGANISATION_MAX_OUTPUT_TOKENS', '1200'))
TEMPERATURE = float(os.getenv('ORGANISATION_TEMPERATURE', '0.35'))
ORGANISATION_MAX_TURNS = int(os.getenv('ORGANISATION_MAX_TURNS', '8'))
ORGANISATION_PLANNER_TEMP = float(os.getenv('ORGANISATION_PLANNER_TEMP', '0.25'))
BACKEND_MODE = (os.getenv('ORGANISATION_BACKEND', 'auto') or 'auto').strip().lower()
if BACKEND_MODE not in {'auto', 'responses', 'chat'}:
    BACKEND_MODE = 'auto'
AI_ENABLED = (os.getenv('ORGANISATION_AI_ENABLED', '1') or '1').strip().lower() not in {'0', 'false', 'no', 'off'}
DEFAULT_MENTIONS = (os.getenv('ORGANISATION_DEFAULT_MENTIONS', '') or '').strip()
EMOJI_GOING = '✅'
EMOJI_MAYBE = '❔'
EMOJI_NO = '❌'
OUTING_EMOJIS: Tuple[str, str, str] = (EMOJI_GOING, EMOJI_MAYBE, EMOJI_NO)
MAX_FIELD_CHARS = 1000
ANNOUNCE_SCHEMA = {'name': 'OrganisationAnnouncement', 'schema': {'type': 'object', 'additionalProperties': False, 'required': ['title', 'body'], 'properties': {'title': {'type': 'string'}, 'body': {'type': 'string'}, 'cta': {'type': ['string', 'null']}, 'summary': {'type': ['string', 'null']}, 'mentions': {'type': ['string', 'null']}}}}
PLANNER_SCHEMA = {'name': 'OrganisationPlanner', 'schema': {'type': 'object', 'additionalProperties': False, 'required': ['status', 'next_question', 'collected', 'summary'], 'properties': {'status': {'type': 'string'}, 'next_question': {'type': ['string', 'null']}, 'collected': {'type': 'object'}, 'summary': {'type': ['string', 'null']}}}}
_JSON_BLOCK_RE = re.compile('```(?:json)?\\s*(?P<body>.+?)```', re.DOTALL | re.IGNORECASE)
_ID_RE = re.compile('\\d+')

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()

def _take_digits(value: str | None) -> Optional[int]:
    if not value:
        return None
    m = _ID_RE.search(value)
    if not m:
        return None
    try:
        return int(m.group(0))
    except Exception:
        return None

def _parse_staff_roles(raw: str) -> Tuple[List[int], List[str]]:
    ids: List[int] = []
    names: List[str] = []
    for part in (raw or '').split(','):
        entry = part.strip()
        if not entry:
            continue
        if entry.isdigit():
            ids.append(int(entry))
        else:
            names.append(entry.lower())
    return (ids, names)

def _extract_response_text(resp: Any) -> str:
    if not resp:
        return ''
    if isinstance(resp, str):
        return resp.strip()
    direct = getattr(resp, 'output_text', None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    output = getattr(resp, 'output', None)
    if output is None and isinstance(resp, dict):
        output = resp.get('output')
    pieces: List[str] = []
    for msg in output or []:
        content_list = getattr(msg, 'content', None)
        if content_list is None and isinstance(msg, dict):
            content_list = msg.get('content')
        for content in content_list or []:
            c_type = getattr(content, 'type', None)
            if c_type is None and isinstance(content, dict):
                c_type = content.get('type')
            if c_type in ('output_text', 'text'):
                candidate = getattr(content, 'text', None)
                if candidate is None and isinstance(content, dict):
                    candidate = content.get('text') or content.get('content')
                if isinstance(candidate, str):
                    pieces.append(candidate)
            elif c_type in ('output_json', 'json_schema', 'json'):
                payload = getattr(content, 'json', None) or getattr(content, 'json_schema', None) or getattr(content, 'content', None)
                if payload is None and isinstance(content, dict):
                    payload = content.get('json') or content.get('json_schema') or content.get('content')
                if isinstance(payload, (dict, list)):
                    pieces.append(json.dumps(payload, ensure_ascii=False))
                elif isinstance(payload, str):
                    pieces.append(payload)
    return ''.join(pieces).strip()

def _extract_json_payload(text: str) -> Optional[str]:
    raw = (text or '').strip()
    if not raw:
        return None
    m = _JSON_BLOCK_RE.search(raw)
    if m:
        raw = (m.group('body') or '').strip()
        if not raw:
            return None
    decoder = json.JSONDecoder()
    for match in re.finditer('[\\{\\[]', raw):
        start = match.start()
        candidate = raw[start:].lstrip()
        try:
            _, end = decoder.raw_decode(candidate)
            return candidate[:end].strip()
        except json.JSONDecodeError:
            continue
    return None

def _coerce_int(value: str) -> int:
    value = (value or '').strip()
    if not value:
        return 0
    m = re.search('\\d+', value)
    if not m:
        return 0
    try:
        return max(0, int(m.group(0)))
    except Exception:
        return 0

def _truncate_list_mentions(user_ids: Set[int], limit_chars: int=MAX_FIELD_CHARS) -> str:
    if not user_ids:
        return '*(aucun)*'
    ids_sorted = sorted(user_ids)
    parts: List[str] = []
    total = 0
    for uid in ids_sorted:
        chunk = f'<@{uid}>'
        projected = total + len(chunk) + (2 if parts else 0)
        if projected > limit_chars:
            break
        parts.append(chunk)
        total = projected
    remaining = len(ids_sorted) - len(parts)
    s = ', '.join(parts)
    if remaining > 0:
        suffix = f' … (+{remaining})'
        if len(s) + len(suffix) <= 1024:
            s += suffix
    return s

def _parse_mentions(raw: str, guild: discord.Guild) -> List[str]:
    tokens: List[str] = []
    seen = set()
    raw = (raw or '').strip()
    if not raw:
        return []
    for part in re.split('[\\s,]+', raw):
        item = part.strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered in ('@everyone', 'everyone'):
            if '@everyone' not in seen:
                tokens.append('@everyone')
                seen.add('@everyone')
            continue
        if lowered in ('@here', 'here'):
            if '@here' not in seen:
                tokens.append('@here')
                seen.add('@here')
            continue
        if item.startswith('<@&') and item.endswith('>'):
            if item not in seen:
                tokens.append(item)
                seen.add(item)
            continue
        role_name = item.lstrip('@')
        if role_name:
            role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), guild.roles)
            if role:
                mention = f'<@&{role.id}>'
                if mention not in seen:
                    tokens.append(mention)
                    seen.add(mention)
    return tokens

def _build_allowed_mentions(guild: discord.Guild, tokens: List[str]) -> discord.AllowedMentions:
    allow_everyone = any((t in ('@everyone', '@here') for t in tokens))
    roles: List[discord.Role] = []
    for t in tokens:
        if t.startswith('<@&') and t.endswith('>'):
            rid = _take_digits(t)
            if rid:
                role = guild.get_role(rid)
                if role:
                    roles.append(role)
    return discord.AllowedMentions(everyone=allow_everyone, roles=roles, users=False)

@dataclass
class OrganisationDraft:
    id: str
    author_id: int
    guild_id: int
    activity: str
    date_time: str
    location: str
    seats: int
    details: str
    mentions_raw: str = ''
    channel_override: str = ''
    title: str = ''
    body: str = ''
    cta: str = ''
    summary: str = ''
    date_ts: Optional[int] = None

    def to_context_dict(self) -> Dict[str, Any]:
        return {'activity': self.activity, 'date_time': self.date_time, 'location': self.location, 'seats': self.seats, 'details': self.details}

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

@dataclass
class OrganisationEvent:
    id: str
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int
    created_at_iso: str
    activity: str
    date_time: str
    date_ts: Optional[int]
    location: str
    seats: int
    details: str
    title: str
    body: str
    cta: str = ''
    mentions: List[str] = field(default_factory=list)
    going: Set[int] = field(default_factory=set)
    maybe: Set[int] = field(default_factory=set)
    status: str = 'active'
    schema_version: int = 1
    console_message_id: Optional[int] = None

    def to_json(self) -> Dict[str, Any]:
        return {'schema_version': self.schema_version, 'id': self.id, 'guild_id': self.guild_id, 'channel_id': self.channel_id, 'message_id': self.message_id, 'author_id': self.author_id, 'created_at_iso': self.created_at_iso, 'activity': self.activity, 'date_time': self.date_time, 'date_ts': self.date_ts, 'location': self.location, 'seats': self.seats, 'details': self.details, 'title': self.title, 'body': self.body, 'cta': self.cta, 'mentions': list(self.mentions or []), 'going': sorted(self.going), 'maybe': sorted(self.maybe), 'status': self.status, 'console_message_id': self.console_message_id}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> 'OrganisationEvent':
        return cls(id=str(data.get('id') or ''), guild_id=int(data.get('guild_id') or 0), channel_id=int(data.get('channel_id') or 0), message_id=int(data.get('message_id') or 0), author_id=int(data.get('author_id') or 0), created_at_iso=str(data.get('created_at_iso') or ''), activity=str(data.get('activity') or ''), date_time=str(data.get('date_time') or ''), date_ts=int(data['date_ts']) if data.get('date_ts') is not None else None, location=str(data.get('location') or ''), seats=int(data.get('seats') or 0), details=str(data.get('details') or ''), title=str(data.get('title') or ''), body=str(data.get('body') or ''), cta=str(data.get('cta') or '') if data.get('cta') is not None else '', mentions=list(data.get('mentions') or []), going=set((int(x) for x in data.get('going') or [] if str(x).isdigit())), maybe=set((int(x) for x in data.get('maybe') or [] if str(x).isdigit())), status=str(data.get('status') or 'active'), schema_version=int(data.get('schema_version') or 1), console_message_id=int(data['console_message_id']) if data.get('console_message_id') else None)

class OrganisationCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        _ensure_utils()
        self.bot = bot
        self._client: Optional[AsyncOpenAI] = build_async_openai_client(AsyncOpenAI, timeout=OPENAI_TIMEOUT)
        self._model: str = DEFAULT_MODEL
        self._drafts: Dict[int, OrganisationDraft] = {}
        self._events: Dict[int, OrganisationEvent] = {}
        self._staff_role_ids, self._staff_role_names = _parse_staff_roles(STAFF_ROLE_ENV)
        self._supports_response_format = True
        self._supports_temperature = True
        self._temperature_mode = 'inference_config'
        self._pending_update_tasks: Dict[int, asyncio.Task] = {}
        self._restore_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._restore_task = asyncio.create_task(self._restore_when_ready())

    def cog_unload(self) -> None:
        if self._restore_task:
            self._restore_task.cancel()
        for t in list(self._pending_update_tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
        self._pending_update_tasks.clear()

    def _is_staff(self, member: discord.Member) -> bool:
        try:
            if member.guild_permissions.administrator:
                return True
        except Exception:
            pass
        for role in getattr(member, 'roles', []) or []:
            try:
                if role.id in self._staff_role_ids:
                    return True
            except Exception:
                pass
            try:
                if role.name and role.name.lower() in self._staff_role_names:
                    return True
            except Exception:
                pass
        return False

    def _find_organisation_channel(self, guild: discord.Guild, override: str='') -> Optional[discord.TextChannel]:
        if override:
            override = override.strip()
            cid = _take_digits(override)
            if cid:
                ch = guild.get_channel(cid)
                if isinstance(ch, discord.TextChannel):
                    return ch
            if override.startswith('#'):
                override = override[1:].strip()
            ch = discord.utils.get(guild.text_channels, name=override)
            if isinstance(ch, discord.TextChannel):
                return ch
            resolved = resolve_text_channel(guild, default_name=override)
            if resolved:
                return resolved
        return resolve_text_channel(guild, id_env='ORGANISATION_CHANNEL_ID', name_env='ORGANISATION_CHANNEL_NAME', default_name=ORGANISATION_CHANNEL_NAME)

    async def _console_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return resolve_text_channel(guild, id_env='CHANNEL_CONSOLE_ID', name_env='CHANNEL_CONSOLE', default_name=CONSOLE_CHANNEL_NAME)

    async def _fetch_history(self, channel: discord.TextChannel, limit: int=200) -> List[discord.Message]:
        return await fetch_channel_history(
            channel,
            limit=limit,
            reason='organisation.console',
        )

    async def _save_event_to_console(self, event: OrganisationEvent) -> bool:
        guild = self.bot.get_guild(event.guild_id)
        if not guild:
            return False
        chan = await self._console_channel(guild)
        if not chan:
            return False
        payload = json.dumps(event.to_json(), ensure_ascii=False, indent=2)
        content = f'```{ORGANISATION_DB_BLOCK}\n{payload}\n```'
        if event.console_message_id:
            try:
                msg = await chan.fetch_message(event.console_message_id)
                await msg.edit(content=content)
                return True
            except discord.NotFound:
                event.console_message_id = None
            except Exception:
                return False
        try:
            msg = await chan.send(content)
            event.console_message_id = msg.id
            return True
        except Exception:
            return False

    async def _load_events_from_console(self, guild: discord.Guild) -> int:
        chan = await self._console_channel(guild)
        if not chan:
            return 0
        pattern = re.compile(f'^```{re.escape(ORGANISATION_DB_BLOCK)}\\n(?P<body>.+?)\\n```$', re.S)
        messages = await self._fetch_history(chan, limit=300)
        loaded = 0
        for msg in messages:
            if msg.author != self.bot.user:
                continue
            m = pattern.match(msg.content or '')
            if not m:
                continue
            try:
                data = json.loads(m.group('body'))
                event = OrganisationEvent.from_json(data)
                if not event.message_id:
                    continue
                if not event.console_message_id:
                    event.console_message_id = msg.id
                self._events[event.message_id] = event
                loaded += 1
            except Exception:
                continue
        return loaded

    async def _restore_when_ready(self) -> None:
        try:
            await self.bot.wait_until_ready()
        except Exception:
            return
        total = 0
        for guild in list(getattr(self.bot, 'guilds', []) or []):
            try:
                total += await self._load_events_from_console(guild)
            except Exception as exc:
                log.warning('Organisation restore failed for guild %s: %s', getattr(guild, 'id', '?'), exc)
        if total:
            log.info('Organisation: %s événement(s) restauré(s) depuis #console.', total)

    def _build_event_embed(self, event: OrganisationEvent) -> discord.Embed:
        title = (event.title or '').strip() or f'📅 Sortie guilde — {event.activity}'.strip()
        title = title[:256]
        desc_lines: List[str] = []
        body = (event.body or '').strip()
        if body:
            desc_lines.append(body)
            desc_lines.append('')
        if event.date_ts:
            ts = int(event.date_ts)
            desc_lines.append(f'**Quand :** <t:{ts}:F> • <t:{ts}:R>')
        else:
            desc_lines.append(f'**Quand :** {event.date_time}')
        if event.location:
            desc_lines.append(f'**Rendez-vous :** {event.location}')
        if event.seats:
            desc_lines.append(f'**Places :** {event.seats}')
        else:
            desc_lines.append('**Places :** ∞')
        if event.details:
            desc_lines.append('')
            desc_lines.append('**Détails / Pré-requis :**')
            desc_lines.append(event.details)
        desc_lines.append('')
        if event.cta:
            desc_lines.append(event.cta)
        desc_lines.append(f'Inscription : réagis avec {EMOJI_GOING} (présent), {EMOJI_MAYBE} (peut-être), {EMOJI_NO} (non).')
        embed = discord.Embed(title=title, description='\n'.join(desc_lines)[:4096], color=discord.Color.blurple())
        seats_label = str(event.seats) if event.seats else '∞'
        embed.add_field(name=f'Inscrits {EMOJI_GOING} ({len(event.going)}/{seats_label})', value=_truncate_list_mentions(event.going), inline=False)
        embed.add_field(name=f'Peut-être {EMOJI_MAYBE} ({len(event.maybe)})', value=_truncate_list_mentions(event.maybe), inline=False)
        embed.set_footer(text=f'Organisateur: {event.author_id} • id: {event.id}')
        return embed

    def _announcement_system_prompt(self) -> str:
        return "Tu es un assistant pour organiser des sorties de guilde sur Dofus Retro (1.29). Les sorties typiques: donjons (boss), captures (arène), sessions drop (ressources), XP, défense percepteur, PvP alignement, quêtes/guilde. Tu aides le staff à produire une annonce Discord claire, concise, motivante et immédiatement publiable. Contraintes: français uniquement; pas de mentions Discord (@everyone/@here/@role); pas d'emojis en spam; ne pas inventer de date/heure/lieu; ne pas inventer de récompenses. Style: 1 accroche courte + puces si pertinent."

    def _announcement_user_prompt(self, draft: OrganisationDraft) -> str:
        data = draft.to_context_dict()
        blob = json.dumps(data, ensure_ascii=False)
        return 'Rédige une annonce pour une sortie guilde. Tu dois fournir un titre court et un corps structuré. Tu peux utiliser des puces, et une formulation Dofus (sobre). Les inscriptions se font via réactions: ✅ présent, ❔ peut-être, ❌ non. Données brutes: ' + blob

    async def _call_openai_json_via_responses(self, *, system: str, user: str, schema: Dict[str, Any], temperature: Optional[float]=None) -> Dict[str, Any]:
        if not self._client:
            raise RuntimeError('OPENAI_API_KEY manquant ou client OpenAI indisponible.')
        request_kwargs: Dict[str, Any] = {'model': self._model, 'instructions': system, 'input': user, 'max_output_tokens': MAX_OUTPUT_TOKENS}
        temp_value = TEMPERATURE if temperature is None else temperature
        reasoning = resolve_reasoning_effort(self._model)
        if reasoning:
            request_kwargs['reasoning'] = reasoning
        if self._supports_temperature:
            if self._temperature_mode == 'inference_config':
                request_kwargs['inference_config'] = {'temperature': temp_value}
            elif self._temperature_mode == 'legacy':
                request_kwargs['temperature'] = temp_value
        if self._supports_response_format:
            request_kwargs['response_format'] = {'type': 'json_schema', 'json_schema': schema}
        while True:
            try:
                resp = await self._client.responses.create(**request_kwargs)
                break
            except TypeError as exc:
                handled = False
                msg = str(exc).lower()
                if self._supports_response_format and 'response_format' in msg:
                    self._supports_response_format = False
                    request_kwargs.pop('response_format', None)
                    handled = True
                if self._supports_temperature and self._temperature_mode == 'inference_config' and ('inference_config' in msg):
                    self._temperature_mode = 'legacy'
                    request_kwargs.pop('inference_config', None)
                    request_kwargs['temperature'] = temp_value
                    handled = True
                if handled:
                    continue
                raise
            except Exception as exc:
                msg = str(exc).lower()
                handled = False
                if self._supports_temperature and 'temperature' in msg and ('not supported' in msg):
                    self._supports_temperature = False
                    self._temperature_mode = 'disabled'
                    request_kwargs.pop('temperature', None)
                    request_kwargs.pop('inference_config', None)
                    handled = True
                if self._supports_response_format and 'response_format' in msg and ('not supported' in msg or 'unsupported' in msg or 'unrecognized' in msg):
                    self._supports_response_format = False
                    request_kwargs.pop('response_format', None)
                    handled = True
                if handled:
                    continue
                raise
        text = _extract_response_text(resp)
        if not text:
            raise RuntimeError('Réponse OpenAI vide.')
        raw = text.strip()
        try:
            return json.loads(raw)
        except Exception:
            payload = _extract_json_payload(raw)
            if payload:
                return json.loads(payload)
            raise RuntimeError('Réponse IA invalide: JSON introuvable.')

    async def _call_openai_json_via_chat(self, *, system: str, user: str, schema: Dict[str, Any], temperature: Optional[float]=None) -> Dict[str, Any]:
        if not self._client:
            raise RuntimeError('OPENAI_API_KEY manquant ou client OpenAI indisponible.')
        messages = [{'role': 'system', 'content': system + "\n\nIMPORTANT: Tu dois appeler la fonction 'submit' et ne renvoyer aucun autre texte."}, {'role': 'user', 'content': user}]
        tools = [{'type': 'function', 'function': {'name': 'submit', 'description': 'Retourne le JSON final.', 'parameters': schema['schema']}}]
        temp_value = TEMPERATURE if temperature is None else temperature
        resp = await self._client.chat.completions.create(model=self._model, messages=messages, tools=tools, tool_choice='required', temperature=temp_value, max_tokens=MAX_OUTPUT_TOKENS)
        try:
            choice = resp.choices[0]
            msg = choice.message
        except Exception as exc:
            raise RuntimeError(f'Réponse ChatCompletion invalide: {exc}') from exc
        tool_calls = getattr(msg, 'tool_calls', None)
        if tool_calls:
            for tc in tool_calls:
                fn = getattr(tc, 'function', None)
                if fn and getattr(fn, 'name', '') == 'submit':
                    args = getattr(fn, 'arguments', '') or ''
                    try:
                        return json.loads(args)
                    except Exception as exc:
                        raise RuntimeError(f'Arguments tool_call non-JSON: {exc}\nargs={args!r}') from exc
        content = (getattr(msg, 'content', '') or '').strip()
        if content:
            payload = _extract_json_payload(content) or content
            return json.loads(payload)
        raise RuntimeError("Le modèle n'a renvoyé aucun JSON exploitable.")

    def _messages_to_prompt(self, messages: List[Dict[str, Any]]) -> Tuple[str, str]:
        system_parts: List[str] = []
        user_parts: List[str] = []
        for msg in messages:
            role = str(msg.get('role') or '').lower()
            content = str(msg.get('content') or '').strip()
            if not content:
                continue
            if role == 'system':
                system_parts.append(content)
            else:
                user_parts.append(f'{role}: {content}')
        system_text = '\n'.join(system_parts).strip()
        if not system_text:
            system_text = 'Tu es EvolutionBOT.'
        user_text = '\n'.join(user_parts).strip()
        return (system_text, user_text)

    async def _call_openai_json(self, *, system: Optional[str]=None, user: Optional[str]=None, messages: Optional[List[Dict[str, Any]]]=None, schema: Dict[str, Any], temperature: Optional[float]=None) -> Dict[str, Any]:
        if messages is not None:
            system_text, user_text = self._messages_to_prompt(messages)
        else:
            system_text = system or ''
            user_text = user or ''
        temp_value = TEMPERATURE if temperature is None else temperature
        if BACKEND_MODE == 'chat':
            return await self._call_openai_json_via_chat(system=system_text, user=user_text, schema=schema, temperature=temp_value)
        if BACKEND_MODE in {'auto', 'responses'}:
            try:
                return await self._call_openai_json_via_responses(system=system_text, user=user_text, schema=schema, temperature=temp_value)
            except Exception as exc:
                if BACKEND_MODE == 'responses':
                    raise
                log.warning('Organisation: Responses failed, fallback chat. err=%s', exc)
                return await self._call_openai_json_via_chat(system=system_text, user=user_text, schema=schema, temperature=temp_value)
        return await self._call_openai_json_via_chat(system=system_text, user=user_text, schema=schema, temperature=temp_value)

    def _planner_system_prompt(self, session: OrganisationSession) -> str:
        context = session.context or {}
        return 'Tu aides le staff a rassembler un brief pour une sortie guilde. Contexte: ' + json.dumps(context, ensure_ascii=False)

    async def _planner_step(self, session: OrganisationSession, *, initial: bool=False, user_message: Optional[str]=None) -> Dict[str, Any]:
        messages = list(session.messages or [])
        if not any(str(m.get('role') or '').lower() == 'system' for m in messages):
            messages.insert(0, {'role': 'system', 'content': self._planner_system_prompt(session)})
        if user_message or initial:
            prompt = user_message if user_message else 'Demarrage de la planification.'
            messages.append({'role': 'user', 'content': prompt})
        system_msgs = [m for m in messages if str(m.get('role') or '').lower() == 'system']
        non_system = [m for m in messages if str(m.get('role') or '').lower() != 'system']
        if ORGANISATION_MAX_TURNS > 0 and len(non_system) > ORGANISATION_MAX_TURNS:
            summary_text = 'Contexte precedent compresse.'
            if session.summary:
                summary_text = f'{summary_text} {session.summary}'
            tail_size = max(ORGANISATION_MAX_TURNS - 1, 0)
            tail = non_system[-tail_size:] if tail_size else []
            non_system = [{'role': 'assistant', 'content': summary_text}] + tail
        messages = system_msgs + non_system
        payload = await self._call_openai_json(messages=messages, schema=PLANNER_SCHEMA, temperature=ORGANISATION_PLANNER_TEMP)
        collected = payload.get('collected') or {}
        if isinstance(collected, dict):
            session.collected.update(collected)
        summary = payload.get('summary')
        if summary is not None:
            session.summary = str(summary) if summary else None
        next_question = payload.get('next_question')
        session.last_question = str(next_question) if next_question else None
        session.messages = messages
        status = str(payload.get('status') or '').lower()
        if status == 'ask' and session.last_question:
            session.messages.append({'role': 'assistant', 'content': session.last_question})
        log.debug('Organisation planner step status=%s user_id=%s', payload.get('status'), session.user_id)
        return payload

    def _format_announcement(self, ctx: commands.Context, payload: Dict[str, Any]) -> Tuple[str, discord.Embed]:
        title = str(payload.get('title') or '').strip() or 'Sortie guilde'
        body = str(payload.get('body') or '').strip()
        cta = str(payload.get('cta') or '').strip()
        mentions = str(payload.get('mentions') or '').strip()
        description = body
        if cta:
            description = f'{description}\n\n{cta}' if description else cta
        embed = discord.Embed(title=title[:256], description=description[:4096] if description else None, color=discord.Color.blurple())
        author = getattr(getattr(ctx, 'author', None), 'display_name', None)
        if author:
            embed.set_footer(text=f'Organisateur: {author}')
        return (mentions, embed)

    async def _generate_announcement(self, draft: OrganisationDraft | OrganisationSession, organiser: Optional[str]=None, channel: Optional[discord.TextChannel]=None) -> Optional[Dict[str, Any]]:
        if isinstance(draft, OrganisationDraft):
            dt = parse_fr_datetime(draft.date_time) if draft.date_time else None
            draft.date_ts = int(dt.timestamp()) if dt else None
            if not AI_ENABLED or not self._client:
                self._fill_template(draft)
                return None
            system = self._announcement_system_prompt()
            user = self._announcement_user_prompt(draft)
            try:
                payload = await self._call_openai_json(system=system, user=user, schema=ANNOUNCE_SCHEMA)
            except Exception as exc:
                log.warning('Organisation: IA generation failed -> template. err=%s', exc, exc_info=True)
                self._fill_template(draft)
                return None
            title = str(payload.get('title') or '').strip()
            body = str(payload.get('body') or '').strip()
            cta = str(payload.get('cta') or '').strip()
            summary = str(payload.get('summary') or '').strip()
            title = title.replace('@everyone', 'everyone').replace('@here', 'here')
            body = body.replace('@everyone', 'everyone').replace('@here', 'here')
            cta = cta.replace('@everyone', 'everyone').replace('@here', 'here')
            draft.title = title[:256] if title else f'Sortie guilde - {draft.activity}'[:256]
            draft.body = body[:3500] if body else ''
            draft.cta = cta[:500] if cta else ''
            draft.summary = summary[:200] if summary else ''
            return None
        if not AI_ENABLED or not self._client:
            payload = {
                'title': 'Sortie guilde',
                'body': 'Annonce non disponible.',
                'cta': '',
                'mentions': '',
                'summary': '',
            }
            return payload
        system = self._announcement_system_prompt()
        context = {
            'organiser': organiser or '',
            'channel': channel.name if channel else '',
            'context': draft.context,
            'collected': draft.collected,
            'summary': draft.summary,
        }
        user = json.dumps(context, ensure_ascii=False)
        payload = await self._call_openai_json(system=system, user=user, schema=ANNOUNCE_SCHEMA)
        summary = payload.get('summary')
        if summary is not None:
            draft.summary = str(summary) if summary else None
        return payload

    def _fill_template(self, draft: OrganisationDraft) -> None:
        draft.title = f'📅 Sortie guilde — {draft.activity}'[:256]
        lines: List[str] = []
        lines.append('Sortie proposée, rejoins-nous pour cette session.')
        dt = parse_fr_datetime(draft.date_time) if draft.date_time else None
        draft.date_ts = int(dt.timestamp()) if dt else None
        if draft.location:
            lines.append(f'• Rendez-vous: {draft.location}')
        if draft.seats:
            lines.append(f'• Places: {draft.seats}')
        if draft.details:
            lines.append('')
            lines.append('Détails / Pré-requis:')
            lines.append(draft.details)
        draft.body = '\n'.join(lines)[:3500]
        draft.cta = "Réagis pour t'inscrire."
        draft.summary = ''

    class _StartView(discord.ui.View):

        def __init__(self, parent: 'OrganisationCog', author_id: int):
            super().__init__(timeout=VIEW_TIMEOUT)
            self.parent = parent
            self.author_id = author_id
            self.message: Optional[discord.Message] = None

        async def on_timeout(self) -> None:
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass

        @discord.ui.button(label='Ouvrir le formulaire', style=discord.ButtonStyle.primary)
        async def open_form(self, interaction: discord.Interaction, _):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("❌ Réservé à l'auteur de la commande.", ephemeral=True)
                return
            if interaction.guild is None:
                await interaction.response.send_message('❌ Action disponible uniquement sur le serveur.', ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member) or not self.parent._is_staff(interaction.user):
                await interaction.response.send_message('❌ Réservé au staff.', ephemeral=True)
                return
            org_channel = self.parent._find_organisation_channel(interaction.guild)
            await interaction.response.send_modal(self.parent._OrganisationModal(self.parent, pref_channel=org_channel))

    class _OrganisationModal(discord.ui.Modal, title='📅 Organiser une sortie (Dofus Retro)'):

        def __init__(self, parent: 'OrganisationCog', pref_channel: Optional[discord.TextChannel], defaults: Optional[Dict[str, str]]=None):
            super().__init__(timeout=MODAL_TIMEOUT)
            self.parent = parent
            self.pref_channel = pref_channel
            defaults = defaults or {}
            self.activity = discord.ui.TextInput(label='Activité (donjon, captures, drop…)', style=discord.TextStyle.short, required=True, max_length=80, default=defaults.get('activity') or '', placeholder='Ex: Donjon Dragon Cochon / Captures Blop / Drop Gelée')
            self.date_time = discord.ui.TextInput(label='Date & heure', style=discord.TextStyle.short, required=True, max_length=64, default=defaults.get('date_time') or '', placeholder='Ex: samedi 21h, demain 20:30, 27/09 19h')
            self.location = discord.ui.TextInput(label='Rendez-vous / Lieu', style=discord.TextStyle.short, required=False, max_length=80, default=defaults.get('location') or '', placeholder='Ex: Zaap Astrub (5,-18) / Entrée donjon / Arène Bonta')
            self.seats = discord.ui.TextInput(label='Places max (optionnel)', style=discord.TextStyle.short, required=False, max_length=16, default=defaults.get('seats') or '', placeholder='Ex: 8 (laisser vide = illimité)')
            self.details = discord.ui.TextInput(label='Détails / objectifs / prérequis', style=discord.TextStyle.paragraph, required=False, max_length=1500, default=defaults.get('details') or '', placeholder="Ex: niveau mini, stuff à prévoir, pierre d'âme, PP, consignes...")
            for comp in (self.activity, self.date_time, self.location, self.seats, self.details):
                self.add_item(comp)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            if interaction.guild is None:
                await interaction.response.send_message('❌ Serveur requis.', ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member) or not self.parent._is_staff(interaction.user):
                await interaction.response.send_message('❌ Réservé au staff.', ephemeral=True)
                return
            activity = str(self.activity.value).strip()
            date_time = str(self.date_time.value).strip()
            location = str(self.location.value).strip()
            seats = _coerce_int(str(self.seats.value))
            details = str(self.details.value).strip()
            draft = OrganisationDraft(id=str(uuid.uuid4())[:8], author_id=interaction.user.id, guild_id=interaction.guild_id, activity=activity, date_time=date_time, location=location, seats=seats, details=details, mentions_raw=DEFAULT_MENTIONS, channel_override=self.pref_channel.name if self.pref_channel else '')
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.parent._generate_announcement(draft)
            self.parent._drafts[interaction.user.id] = draft
            await self.parent._send_preview(interaction, draft)

    class _OptionsModal(discord.ui.Modal, title='⚙️ Options sortie'):

        def __init__(self, parent: 'OrganisationCog', user_id: int, defaults: Optional[Dict[str, str]]=None):
            super().__init__(timeout=MODAL_TIMEOUT)
            self.parent = parent
            self.user_id = user_id
            defaults = defaults or {}
            self.mentions = discord.ui.TextInput(label='Mentions (optionnel)', style=discord.TextStyle.short, required=False, max_length=120, default=defaults.get('mentions') or DEFAULT_MENTIONS, placeholder='Ex: @here, @everyone, @Sorties, <@&ID>')
            self.channel = discord.ui.TextInput(label='Salon de publication (optionnel)', style=discord.TextStyle.short, required=False, max_length=80, default=defaults.get('channel') or ORGANISATION_CHANNEL_NAME, placeholder=f'Ex: #{ORGANISATION_CHANNEL_NAME}')
            self.add_item(self.mentions)
            self.add_item(self.channel)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message('Brouillon introuvable/expiré.', ephemeral=True)
                return
            draft.mentions_raw = str(self.mentions.value).strip()
            draft.channel_override = str(self.channel.value).strip()
            await interaction.response.send_message('✅ Options mises à jour.', ephemeral=True)

    class _PreviewView(discord.ui.View):

        def __init__(self, parent: 'OrganisationCog', user_id: int):
            super().__init__(timeout=VIEW_TIMEOUT)
            self.parent = parent
            self.user_id = user_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return interaction.user.id == self.user_id

        @discord.ui.button(label='✏️ Ajuster', style=discord.ButtonStyle.secondary)
        async def adjust(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message('Brouillon introuvable.', ephemeral=True)
                return
            defaults = {'activity': draft.activity, 'date_time': draft.date_time, 'location': draft.location, 'seats': str(draft.seats) if draft.seats else '', 'details': draft.details}
            org_channel = None
            if interaction.guild:
                org_channel = self.parent._find_organisation_channel(interaction.guild, override=draft.channel_override)
            await interaction.response.send_modal(self.parent._OrganisationModal(self.parent, pref_channel=org_channel, defaults=defaults))

        @discord.ui.button(label='⚙️ Options', style=discord.ButtonStyle.secondary)
        async def options(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message('Brouillon introuvable.', ephemeral=True)
                return
            defaults = {'mentions': draft.mentions_raw, 'channel': draft.channel_override or ORGANISATION_CHANNEL_NAME}
            await interaction.response.send_modal(self.parent._OptionsModal(self.parent, self.user_id, defaults=defaults))

        @discord.ui.button(label='🔁 Régénérer', style=discord.ButtonStyle.secondary)
        async def regenerate(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message('Brouillon introuvable.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            await self.parent._generate_announcement(draft)
            await self.parent._send_preview(interaction, draft, replace=True)

        @discord.ui.button(label='📣 Publier', style=discord.ButtonStyle.success)
        async def publish(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message('Brouillon introuvable.', ephemeral=True)
                return
            if interaction.guild is None:
                await interaction.response.send_message('❌ Serveur requis.', ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            ok, reason = await self.parent._publish_draft(interaction, draft)
            if not ok:
                await interaction.followup.send(f'❌ Publication impossible: {reason}', ephemeral=True)
                return
            self.parent._drafts.pop(self.user_id, None)
            await interaction.followup.send('✅ Sortie publiée dans #organisation.', ephemeral=True)

        @discord.ui.button(label='🗑️ Annuler', style=discord.ButtonStyle.danger)
        async def cancel(self, interaction: discord.Interaction, _):
            self.parent._drafts.pop(self.user_id, None)
            await interaction.response.edit_message(content='Brouillon annulé.', embed=None, view=None)

    async def _send_preview(self, interaction: discord.Interaction, draft: OrganisationDraft, *, replace: bool=False) -> None:
        fake_event = OrganisationEvent(id=draft.id, guild_id=draft.guild_id, channel_id=0, message_id=0, author_id=draft.author_id, created_at_iso=_now_iso(), activity=draft.activity, date_time=draft.date_time, date_ts=draft.date_ts, location=draft.location, seats=draft.seats, details=draft.details, title=draft.title, body=draft.body, cta=draft.cta, mentions=_parse_mentions(draft.mentions_raw, interaction.guild) if interaction.guild else [], going=set(), maybe=set())
        embed = self._build_event_embed(fake_event)
        view = self._PreviewView(self, interaction.user.id)
        mention_text = draft.mentions_raw.strip() or '(aucune)'
        channel_text = draft.channel_override.strip() or ORGANISATION_CHANNEL_NAME
        msg = f'✅ **Brouillon `{draft.id}` prêt.**\n• Salon: `{channel_text}`\n• Mentions: `{mention_text}`\nTu peux **Publier**, **Ajuster**, **Options**, ou **Régénérer**.'
        await interaction.followup.send(msg, embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def _publish_draft(self, interaction: discord.Interaction, draft: OrganisationDraft) -> Tuple[bool, str]:
        guild = interaction.guild
        if guild is None:
            return (False, 'guild manquant')
        channel = self._find_organisation_channel(guild, override=draft.channel_override)
        if not channel:
            return (False, f'Salon #{ORGANISATION_CHANNEL_NAME} introuvable (ou override invalide).')
        me = guild.me or guild.get_member(self.bot.user.id) if self.bot.user else None
        if me:
            perms = channel.permissions_for(me)
            if not perms.send_messages:
                return (False, f"Je n'ai pas la permission d'écrire dans #{channel.name}.")
            if not perms.embed_links:
                return (False, f"Je n'ai pas la permission d'envoyer des embeds dans #{channel.name}.")
        mentions = _parse_mentions(draft.mentions_raw, guild)
        allowed_mentions = _build_allowed_mentions(guild, mentions)
        content = ' '.join(mentions) if mentions else None
        event = OrganisationEvent(id=draft.id, guild_id=guild.id, channel_id=channel.id, message_id=0, author_id=draft.author_id, created_at_iso=_now_iso(), activity=draft.activity, date_time=draft.date_time, date_ts=draft.date_ts, location=draft.location, seats=draft.seats, details=draft.details, title=draft.title, body=draft.body, cta=draft.cta, mentions=mentions, going=set(), maybe=set())
        embed = self._build_event_embed(event)
        try:
            msg = await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
        except Exception as exc:
            return (False, f'Erreur Discord envoi message: {exc}')
        event.message_id = msg.id
        self._events[msg.id] = event
        for emoji in OUTING_EMOJIS:
            try:
                await msg.add_reaction(emoji)
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass
        saved = await self._save_event_to_console(event)
        if not saved:
            log.warning("Organisation: impossible de sauvegarder l'événement %s dans #console", event.id)
        return (True, '')

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not self.bot.user or payload.user_id == self.bot.user.id:
            return
        event = self._events.get(payload.message_id)
        if not event or event.status != 'active':
            return
        emoji = str(payload.emoji)
        uid = payload.user_id
        if emoji not in OUTING_EMOJIS:
            return
        changed = self._apply_reaction(event, emoji=emoji, user_id=uid, add=True)
        if changed:
            self._schedule_event_update(event)
        asyncio.create_task(self._try_cleanup_member_reactions(event, payload=payload, keep_emoji=emoji))

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if not self.bot.user or payload.user_id == self.bot.user.id:
            return
        event = self._events.get(payload.message_id)
        if not event or event.status != 'active':
            return
        emoji = str(payload.emoji)
        uid = payload.user_id
        if emoji not in OUTING_EMOJIS:
            return
        changed = self._apply_reaction(event, emoji=emoji, user_id=uid, add=False)
        if changed:
            self._schedule_event_update(event)

    def _apply_reaction(self, event: OrganisationEvent, *, emoji: str, user_id: int, add: bool) -> bool:
        before = (set(event.going), set(event.maybe))
        if emoji == EMOJI_GOING:
            if add:
                event.going.add(user_id)
                event.maybe.discard(user_id)
            else:
                event.going.discard(user_id)
        elif emoji == EMOJI_MAYBE:
            if add:
                event.maybe.add(user_id)
                event.going.discard(user_id)
            else:
                event.maybe.discard(user_id)
        elif emoji == EMOJI_NO:
            if add:
                event.going.discard(user_id)
                event.maybe.discard(user_id)
        after = (set(event.going), set(event.maybe))
        return after != before

    async def _try_cleanup_member_reactions(self, event: OrganisationEvent, *, payload: discord.RawReactionActionEvent, keep_emoji: str) -> None:
        if keep_emoji not in OUTING_EMOJIS:
            return
        guild = self.bot.get_guild(event.guild_id)
        if not guild:
            return
        channel = guild.get_channel(event.channel_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await self.bot.fetch_channel(event.channel_id)
                if isinstance(fetched, discord.TextChannel):
                    channel = fetched
            except Exception:
                return
        if not isinstance(channel, discord.TextChannel):
            return
        me = guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)
        if me:
            perms = channel.permissions_for(me)
            if not perms.manage_messages:
                return
        try:
            message = await channel.fetch_message(event.message_id)
        except Exception:
            return
        user = getattr(payload, 'member', None)
        if user is None:
            try:
                user = await self.bot.fetch_user(payload.user_id)
            except Exception:
                return
        for emoji in OUTING_EMOJIS:
            if emoji == keep_emoji:
                continue
            try:
                await message.remove_reaction(emoji, user)
            except discord.Forbidden:
                return
            except discord.HTTPException:
                continue

    def _schedule_event_update(self, event: OrganisationEvent, delay: float=1.2) -> None:
        mid = event.message_id
        existing = self._pending_update_tasks.get(mid)
        if existing and (not existing.done()):
            return

        async def _runner():
            try:
                await asyncio.sleep(delay)
                await self._update_event_message_and_persist(event)
            finally:
                self._pending_update_tasks.pop(mid, None)
        self._pending_update_tasks[mid] = asyncio.create_task(_runner())

    async def _update_event_message_and_persist(self, event: OrganisationEvent) -> None:
        await self._update_event_message(event)
        try:
            await self._save_event_to_console(event)
        except Exception:
            pass

    async def _update_event_message(self, event: OrganisationEvent) -> None:
        guild = self.bot.get_guild(event.guild_id)
        if not guild:
            return
        channel = guild.get_channel(event.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            msg = await channel.fetch_message(event.message_id)
        except discord.NotFound:
            event.status = 'deleted'
            await self._save_event_to_console(event)
            self._events.pop(event.message_id, None)
            return
        except Exception:
            return
        embed = self._build_event_embed(event)
        try:
            await msg.edit(embed=embed)
        except Exception:
            return

    @commands.command(name='organisation', aliases=['orga', 'sortie'])
    async def organisation_cmd(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.reply('❌ Commande utilisable uniquement sur le serveur.', mention_author=False)
            return
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply('❌ Commande réservée au staff.', mention_author=False)
            return
        view = self._StartView(self, ctx.author.id)
        msg = await ctx.send("Clique pour ouvrir le formulaire d'organisation (sortie guilde).", view=view)
        view.message = msg

    @commands.command(name='organisation-model', aliases=['organisationmodel', 'orga-model'])
    async def organisation_model(self, ctx: commands.Context, *, model: str | None=None) -> None:
        if ctx.guild is None:
            await ctx.reply('❌ Commande utilisable uniquement sur le serveur.', mention_author=False)
            return
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply('❌ Commande réservée au staff.', mention_author=False)
            return
        candidate = (model or '').strip()
        if not candidate:
            await ctx.reply('Précise un modèle, ex: `!organisation-model gpt-5-mini`.', mention_author=False)
            return
        resolved = normalise_staff_model(candidate)
        if not resolved:
            await ctx.reply('Modèle non reconnu.', mention_author=False)
            return
        self._model = resolved
        await ctx.reply(f'✅ Modèle organisation (runtime) : `{self._model}`.\nPour le rendre permanent: définis `OPENAI_STAFF_MODEL` sur Render.', mention_author=False)

    @commands.command(name='organisation-sync', aliases=['orga-sync'])
    async def organisation_sync(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.reply('❌ Serveur requis.', mention_author=False)
            return
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply('❌ Staff uniquement.', mention_author=False)
            return
        before = len(self._events)
        loaded = await self._load_events_from_console(ctx.guild)
        after = len(self._events)
        await ctx.reply(f'✅ Reload terminé. Chargés={loaded}. Tracking avant={before}, après={after}.', mention_author=False)

    @app_commands.command(name='organisation', description='Créer une sortie guilde (formulaire).')
    async def organisation_slash(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message('❌ Serveur requis.', ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not self._is_staff(interaction.user):
            await interaction.response.send_message('❌ Staff uniquement.', ephemeral=True)
            return
        org_channel = self._find_organisation_channel(interaction.guild)
        await interaction.response.send_modal(self._OrganisationModal(self, pref_channel=org_channel))

async def setup(bot: commands.Bot) -> None:
    try:
        bot.remove_command('organisation')
    except Exception:
        pass
    await bot.add_cog(OrganisationCog(bot))
