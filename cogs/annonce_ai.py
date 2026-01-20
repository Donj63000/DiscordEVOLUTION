from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.channel_resolver import resolve_text_channel
from utils.openai_config import build_async_openai_client, resolve_reasoning_effort, resolve_staff_model

try:
    from openai import AsyncOpenAI
except Exception:
    AsyncOpenAI = None

try:
    import dateparser
except Exception:
    dateparser = None

ANNOUNCE_DB_BLOCK = os.getenv("ANNOUNCE_DB_BLOCK", "announce")
ANNONCE_CHANNEL_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "annonces")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", os.getenv("CONSOLE_CHANNEL_NAME", "console"))
DEFAULT_MENTIONS = os.getenv("ANNONCE_DEFAULT_MENTIONS", "@everyone")
SCHEDULER_INTERVAL = float(os.getenv("ANNONCE_SCHEDULER_INTERVAL", "30"))
VIEW_TIMEOUT = int(os.getenv("ANNONCE_VIEW_TIMEOUT", "900"))
STAFF_ROLE_ENV = os.getenv("IASTAFF_ROLE", "Staff")

log = logging.getLogger("annonce_ai")
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(?P<body>.+?)```", re.DOTALL)


def _hex_to_int(color: str) -> int:
    color = (color or "").strip().lstrip("#")
    return int(color, 16) if color else 0x2ECC71


def _is_temperature_error(exc: Exception) -> bool:
    msg = getattr(exc, "message", None) or str(exc)
    lowered = msg.lower()
    if "temperature" not in lowered:
        return False
    return (
        "not supported" in lowered
        or "unsupported parameter" in lowered
        or "unsupported value" in lowered
        or "does not support" in lowered
        or "only the default" in lowered
    )


def _is_inference_config_error(exc: Exception) -> bool:
    msg = getattr(exc, "message", None) or str(exc)
    lowered = msg.lower()
    return "inference_config" in lowered and (
        "unexpected" in lowered
        or "unrecognized" in lowered
        or "unsupported" in lowered
        or "not supported" in lowered
        or "unknown" in lowered
    )


@dataclass
class Variant:
    style: str
    title: str
    description: str
    footer: Optional[str] = None
    color: str = "#2ECC71"
    image_url: Optional[str] = None
    cta: Optional[str] = None


@dataclass
class AnnounceDraft:
    id: str
    author_id: int
    guild_id: int
    channel_id: Optional[int]
    variants: List[Variant]
    mentions: List[str]
    chosen: int = 0
    raw_input: Dict[str, Any] = None


@dataclass
class ScheduledAnnounce:
    id: str
    guild_id: int
    channel_id: int
    author_id: int
    variant: Variant
    run_at_iso: str
    mentions: List[str]
    status: str = "scheduled"
    console_message_id: Optional[int] = None


class AnnounceAICog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._drafts: Dict[int, AnnounceDraft] = {}
        self._scheduled: Dict[str, ScheduledAnnounce] = {}
        self._client: Optional[AsyncOpenAI] = build_async_openai_client(AsyncOpenAI)
        self._supports_response_format = True
        self._supports_temperature = True
        self._temperature_mode = "inference_config"
        self._model = resolve_staff_model()
        self._staff_role_ids, self._staff_role_names = self._parse_staff_roles(STAFF_ROLE_ENV)
        self.scheduler_loop.start()

    def cog_unload(self) -> None:
        try:
            self.scheduler_loop.cancel()
        except Exception:
            pass

    def _parse_staff_roles(self, raw: str) -> Tuple[List[int], List[str]]:
        ids: List[int] = []
        names: List[str] = []
        for part in (raw or "").split(","):
            entry = part.strip()
            if not entry:
                continue
            if entry.isdigit():
                ids.append(int(entry))
            else:
                names.append(entry.lower())
        return ids, names

    def _is_staff(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        for role in getattr(member, "roles", []):
            if role.id in self._staff_role_ids:
                return True
            if role.name.lower() in self._staff_role_names:
                return True
        return False

    def _find_channel(self, guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
        return resolve_text_channel(
            guild,
            id_env="ANNONCE_CHANNEL_ID",
            name_env="ANNONCE_CHANNEL_NAME",
            default_name=name or ANNONCE_CHANNEL_NAME,
        )

    async def _console_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        return resolve_text_channel(
            guild,
            id_env="CHANNEL_CONSOLE_ID",
            name_env="CHANNEL_CONSOLE",
            default_name=CONSOLE_CHANNEL_NAME,
        )

    def _system_prompt(self) -> str:
        return (
            "Tu aides le staff de la guilde 'Évolution' (Dofus Retro) à publier des annonces. "
            "Rends le message clair, motivant, conforme au règlement et concis. "
            "Interdits: majuscules abusives, spam d'emojis, promesses vagues. "
            "Ajoute un titre fort, un corps structuré, optionnellement un CTA clair. "
            "N'invente pas de dates. Toute date doit venir de l'utilisateur. "
            "N'insère pas de mentions (@everyone/@here/@role)."
        )

    def _json_schema(self) -> Dict[str, Any]:
        return {
            "name": "AnnouncementVariants",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "variants": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "style": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "footer": {"type": "string"},
                                "color": {"type": "string", "pattern": "^#?[0-9a-fA-F]{6}$"},
                                "image_url": {"type": "string"},
                                "cta": {"type": "string"},
                            },
                            "required": ["style", "title", "description"],
                        },
                    }
                },
                "required": ["variants"],
            },
            "strict": True,
        }

    def _extract_json_payload(self, text: str) -> Optional[str]:
        """Extract a JSON payload from a model response."""
        match = JSON_BLOCK_RE.search(text)
        if match:
            return match.group("body").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start : end + 1]
        return None

    def _parse_variants_payload(self, text: str) -> Dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.debug("Annonce IA JSON brut invalide, tentative d'extraction.", exc_info=True)
        payload = self._extract_json_payload(text)
        if payload:
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                log.debug("Annonce IA JSON extrait invalide.", exc_info=True)
        raise RuntimeError("Réponse IA invalide: JSON illisible.")

    async def _ask_openai(self, fields: Dict[str, str]) -> List[Variant]:
        if not self._client:
            raise RuntimeError("OPENAI_API_KEY manquant ou librairie openai indisponible.")

        input_text = (
            "Contexte guilde: Evolution (Dofus Retro).\n"
            f"Objectif annoncé: {fields.get('objectif')}\n"
            f"Cible: {fields.get('cible')}\n"
            f"Ton souhaité: {fields.get('ton')}\n"
            "Message brut fourni par le staff:\n"
            f"{fields.get('brut')}\n\n"
            "Contraintes:\n"
            "- 3 variantes: Bref, Standard, RP léger (thème Dofus).\n"
            "- Retourne STRICTEMENT le JSON demandé."
        )
        temperature_value = float(os.getenv("IASTAFF_TEMPERATURE", "0.4"))
        max_tokens = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1200"))
        request_kwargs: Dict[str, Any] = {
            "model": self._model,
            "instructions": self._system_prompt(),
            "input": input_text,
            "max_output_tokens": max_tokens,
        }
        reasoning = resolve_reasoning_effort(request_kwargs["model"])
        if reasoning:
            request_kwargs["reasoning"] = reasoning
        if self._supports_temperature:
            if self._temperature_mode == "inference_config":
                request_kwargs["inference_config"] = {"temperature": temperature_value}
            elif self._temperature_mode == "legacy":
                request_kwargs["temperature"] = temperature_value
        if self._supports_response_format:
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": self._json_schema(),
            }

        while True:
            try:
                log.debug("OpenAI annonce request: model=%s", request_kwargs.get("model"))
                resp = await self._client.responses.create(**request_kwargs)
                break
            except TypeError as exc:
                handled = False
                if (
                    self._supports_temperature
                    and self._temperature_mode == "inference_config"
                    and _is_inference_config_error(exc)
                ):
                    self._temperature_mode = "legacy"
                    request_kwargs.pop("inference_config", None)
                    request_kwargs["temperature"] = temperature_value
                    handled = True
                elif self._supports_response_format and "response_format" in str(exc):
                    self._supports_response_format = False
                    request_kwargs.pop("response_format", None)
                    handled = True
                if handled:
                    continue
                raise
            except Exception as exc:
                handled = False
                if (
                    self._supports_temperature
                    and self._temperature_mode == "inference_config"
                    and _is_inference_config_error(exc)
                ):
                    self._temperature_mode = "legacy"
                    request_kwargs.pop("inference_config", None)
                    request_kwargs["temperature"] = temperature_value
                    handled = True
                elif self._supports_temperature and _is_temperature_error(exc):
                    self._supports_temperature = False
                    self._temperature_mode = "disabled"
                    request_kwargs.pop("temperature", None)
                    request_kwargs.pop("inference_config", None)
                    handled = True
                if handled:
                    continue
                raise

        text = getattr(resp, "output_text", "") or ""
        if not text and getattr(resp, "output", None):
            for msg in resp.output:
                for content in getattr(msg, "content", []) or []:
                    c_type = getattr(content, "type", "")
                    if c_type in ("output_text", "text") and getattr(content, "text", ""):
                        text += content.text
                    elif c_type in ("json_schema", "json"):
                        payload = (
                            getattr(content, "json_schema", None)
                            or getattr(content, "json", None)
                            or getattr(content, "content", None)
                        )
                        if isinstance(payload, (dict, list)):
                            text += json.dumps(payload)
                        elif isinstance(payload, str):
                            text += payload
        if not text:
            raise RuntimeError("Réponse OpenAI vide.")

        data = self._parse_variants_payload(text)
        variants = []
        for item in data["variants"]:
            variants.append(
                Variant(
                    style=item.get("style", ""),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    footer=item.get("footer"),
                    color=item.get("color", "#2ECC71"),
                    image_url=item.get("image_url"),
                    cta=item.get("cta"),
                )
            )
        return variants

    def _variant_to_embed(self, variant: Variant) -> discord.Embed:
        embed = discord.Embed(
            title=variant.title[:256],
            description=variant.description[:4096],
            color=_hex_to_int(variant.color),
        )
        if variant.footer:
            embed.set_footer(text=variant.footer[:2048])
        if variant.image_url:
            embed.set_image(url=variant.image_url)
        if variant.cta:
            embed.add_field(name="Appel à l'action", value=variant.cta[:1024], inline=False)
        return embed

    def _parse_mentions(self, raw: str, guild: discord.Guild) -> List[str]:
        tokens: List[str] = []
        seen = set()
        for part in re.split(r"[\s,]+", raw or ""):
            item = part.strip()
            if not item:
                continue
            lowered = item.lower()
            if lowered in ("@everyone", "everyone"):
                if "@everyone" not in seen:
                    tokens.append("@everyone")
                    seen.add("@everyone")
                continue
            if lowered in ("@here", "here"):
                if "@here" not in seen:
                    tokens.append("@here")
                    seen.add("@here")
                continue
            if item.startswith("<@&") and item.endswith(">"):
                if item not in seen:
                    tokens.append(item)
                    seen.add(item)
                continue
            role_name = item.lstrip("@")
            if role_name:
                role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), guild.roles)
                if role:
                    mention = f"<@&{role.id}>"
                    if mention not in seen:
                        tokens.append(mention)
                        seen.add(mention)
        return tokens

    def _build_mentions_payload(
        self, guild: discord.Guild, tokens: List[str]
    ) -> Tuple[Optional[str], discord.AllowedMentions]:
        content = " ".join(tokens) if tokens else None
        allow_everyone = any(token in ("@everyone", "@here") for token in tokens)
        role_ids = []
        for token in tokens:
            if token.startswith("<@&") and token.endswith(">"):
                try:
                    role_id = int(token[3:-1])
                except ValueError:
                    continue
                role = guild.get_role(role_id)
                if role:
                    role_ids.append(role)
        allowed_mentions = discord.AllowedMentions(everyone=allow_everyone, roles=role_ids, users=False)
        return content, allowed_mentions

    async def _moderate(self, text: str) -> Optional[str]:
        if not self._client or os.getenv("ANNONCE_SAFETY", "1") == "0":
            return None
        try:
            log.debug("Moderation request")
            mod = await self._client.moderations.create(model="omni-moderation-latest", input=text)
            flagged = False
            if hasattr(mod, "results") and mod.results:
                flagged = bool(getattr(mod.results[0], "flagged", False))
            elif hasattr(mod, "output") and mod.output:
                flagged = bool(getattr(mod.output[0], "flagged", False))
            if flagged:
                return "Le message semble contrevenir aux règles de modération."
        except Exception:
            return None
        return None

    async def _save_to_console(self, guild: discord.Guild, sched: ScheduledAnnounce) -> bool:
        chan = await self._console_channel(guild)
        if not chan:
            return False
        payload = asdict(sched)
        payload["variant"] = asdict(sched.variant)
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        content = f"```{ANNOUNCE_DB_BLOCK}\n{raw}\n```"
        msg = await chan.send(content)
        sched.console_message_id = msg.id
        log.debug("Annonce programmée sauvegardée: %s", sched.id)
        return True

    async def _load_all_from_console(self, guild: discord.Guild) -> None:
        chan = await self._console_channel(guild)
        if not chan:
            return
        pattern = re.compile(rf"^```{re.escape(ANNOUNCE_DB_BLOCK)}\n(?P<body>.+?)\n```$", re.S)
        async for message in chan.history(limit=200):
            match = pattern.match(message.content or "")
            if not match:
                continue
            try:
                data = json.loads(match.group("body"))
            except Exception:
                continue
            sid = data.get("id")
            if not sid:
                continue
            if sid in self._scheduled and getattr(self._scheduled[sid], "console_message_id", None) == message.id:
                continue
            variant_data = data.get("variant") or {}
            variant = Variant(
                style=variant_data.get("style", ""),
                title=variant_data.get("title", ""),
                description=variant_data.get("description", ""),
                footer=variant_data.get("footer"),
                color=variant_data.get("color", "#2ECC71"),
                image_url=variant_data.get("image_url"),
                cta=variant_data.get("cta"),
            )
            scheduled = ScheduledAnnounce(
                id=sid,
                guild_id=int(data.get("guild_id")),
                channel_id=int(data.get("channel_id")),
                author_id=int(data.get("author_id")),
                variant=variant,
                run_at_iso=data.get("run_at_iso"),
                mentions=data.get("mentions") or [],
                status=data.get("status", "scheduled"),
                console_message_id=message.id,
            )
            self._scheduled[scheduled.id] = scheduled

    async def _update_console_entry(self, guild: discord.Guild, sched: ScheduledAnnounce) -> None:
        console = await self._console_channel(guild)
        if not console or not sched.console_message_id:
            return
        try:
            msg = await console.fetch_message(sched.console_message_id)
        except Exception:
            return
        payload = asdict(sched)
        payload["variant"] = asdict(sched.variant)
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        await msg.edit(content=f"```{ANNOUNCE_DB_BLOCK}\n{raw}\n```")

    async def _publish(self, sched: ScheduledAnnounce) -> bool:
        guild = self.bot.get_guild(sched.guild_id)
        if not guild:
            return False
        channel = guild.get_channel(sched.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        variant = sched.variant
        blocked = await self._moderate(f"{variant.title}\n{variant.description}\n{variant.cta or ''}")
        if blocked:
            console = await self._console_channel(guild)
            if console:
                await console.send(f"❌ Blocage modération pour annonce `{sched.id}`: {blocked}")
            return False

        content, allowed_mentions = self._build_mentions_payload(guild, sched.mentions)
        embed = self._variant_to_embed(variant)
        try:
            await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
            log.debug("Annonce publiée: %s", sched.id)
        except discord.HTTPException:
            return False

        sched.status = "published"
        await self._update_console_entry(guild, sched)
        return True

    async def _send_preview(self, interaction: discord.Interaction, draft: AnnounceDraft) -> None:
        variant = draft.variants[draft.chosen]
        embed = self._variant_to_embed(variant)
        content, _ = self._build_mentions_payload(interaction.guild, draft.mentions)
        mention_text = content or "(aucune mention)"
        view = self._PreviewView(self, interaction.user.id)
        await interaction.followup.send(
            f"✅ **Brouillon `{draft.id}` prêt.**\n"
            f"• Variante: **{variant.style}**\n"
            f"• Mentions: {mention_text}\n"
            "Tu peux **Publier**, **Programmer**, **Régénérer**, ou **Ajuster**.",
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    class _StartView(discord.ui.View):
        def __init__(self, parent: "AnnounceAICog", author_id: int):
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

        @discord.ui.button(label="Ouvrir le formulaire", style=discord.ButtonStyle.primary)
        async def open_form(self, interaction: discord.Interaction, _):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message("❌ Réservé à l'auteur de la commande.", ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member) or not self.parent._is_staff(interaction.user):
                await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
                return
            if interaction.guild is None:
                await interaction.response.send_message(
                    "❌ Action disponible uniquement sur le serveur.",
                    ephemeral=True,
                )
                return
            channel = self.parent._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
            try:
                await interaction.response.send_modal(self.parent._AnnounceModal(self.parent, channel))
            except discord.HTTPException as exc:
                log.exception("Impossible d'ouvrir le modal annonce: %s", exc)
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ Impossible d'ouvrir le formulaire (erreur Discord).",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Impossible d'ouvrir le formulaire (erreur Discord).",
                        ephemeral=True,
                    )

    class _AnnounceModal(discord.ui.Modal, title="✍️ Rédiger une annonce"):
        def __init__(
            self,
            parent: "AnnounceAICog",
            channel: Optional[discord.TextChannel],
            defaults: Optional[Dict[str, str]] = None,
        ):
            super().__init__(timeout=300)
            self.parent = parent
            self.pref_channel = channel
            defaults = defaults or {}

            self.obj = discord.ui.TextInput(
                label="🎯 Objectif (obligatoire)",
                style=discord.TextStyle.short,
                max_length=120,
                default=defaults.get("objectif"),
            )
            self.cible = discord.ui.TextInput(
                label="👥 Cible (guilde / recrues / team)",
                style=discord.TextStyle.short,
                required=False,
                max_length=80,
                default=defaults.get("cible"),
                placeholder="Ex: guilde entière, recrues, team donjon",
            )
            self.ton = discord.ui.TextInput(
                label="🎨 Ton (ex: clair, motivant, RP léger…)",
                style=discord.TextStyle.short,
                required=False,
                max_length=60,
                default=defaults.get("ton") or "clair, motivant",
            )
            self.mentions = discord.ui.TextInput(
                label="📣 Mentions (ex: @everyone, @here, @Role)",
                style=discord.TextStyle.short,
                required=False,
                max_length=120,
                default=defaults.get("mentions") or DEFAULT_MENTIONS,
            )
            self.brut = discord.ui.TextInput(
                label="📝 Message brut (texte de base)",
                style=discord.TextStyle.paragraph,
                max_length=2000,
                default=defaults.get("brut"),
            )

            for comp in (self.obj, self.cible, self.ton, self.mentions, self.brut):
                self.add_item(comp)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member) or not self.parent._is_staff(interaction.user):
                await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
                return
            fields = {
                "objectif": str(self.obj.value).strip(),
                "cible": str(self.cible.value).strip() or "guilde",
                "ton": str(self.ton.value).strip() or "clair, motivant",
                "brut": str(self.brut.value).strip(),
                "mentions": str(self.mentions.value).strip(),
            }
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                variants = await self.parent._ask_openai(fields)
            except Exception as exc:
                await interaction.followup.send(f"❌ Erreur IA: {exc}", ephemeral=True)
                return

            mentions = self.parent._parse_mentions(fields.get("mentions") or "", interaction.guild)
            draft = AnnounceDraft(
                id=str(uuid.uuid4())[:8],
                author_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=self.pref_channel.id if self.pref_channel else None,
                variants=variants,
                mentions=mentions,
                chosen=0,
                raw_input=fields,
            )
            self.parent._drafts[interaction.user.id] = draft
            await self.parent._send_preview(interaction, draft)

    class _PreviewView(discord.ui.View):
        def __init__(self, parent: "AnnounceAICog", user_id: int):
            super().__init__(timeout=VIEW_TIMEOUT)
            self.parent = parent
            self.user_id = user_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return interaction.user.id == self.user_id

        async def _edit_preview(self, interaction: discord.Interaction, draft: AnnounceDraft) -> None:
            variant = draft.variants[draft.chosen]
            embed = self.parent._variant_to_embed(variant)
            content, _ = self.parent._build_mentions_payload(interaction.guild, draft.mentions)
            mention_text = content or "(aucune mention)"
            await interaction.response.edit_message(
                content=f"Variante: **{variant.style}** | Mentions: {mention_text}",
                embed=embed,
                view=self,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @discord.ui.button(label="◀️ Variante", style=discord.ButtonStyle.secondary)
        async def prev_var(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            draft.chosen = (draft.chosen - 1) % len(draft.variants)
            await self._edit_preview(interaction, draft)

        @discord.ui.button(label="Variante ▶️", style=discord.ButtonStyle.secondary)
        async def next_var(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            draft.chosen = (draft.chosen + 1) % len(draft.variants)
            await self._edit_preview(interaction, draft)

        @discord.ui.button(label="🔁 Régénérer", style=discord.ButtonStyle.secondary)
        async def regenerate(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                variants = await self.parent._ask_openai(draft.raw_input)
            except Exception as exc:
                await interaction.followup.send(f"❌ Erreur IA: {exc}", ephemeral=True)
                return
            draft.variants = variants
            draft.chosen = 0
            await self.parent._send_preview(interaction, draft)

        @discord.ui.button(label="✏️ Ajuster", style=discord.ButtonStyle.secondary)
        async def adjust(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            defaults = dict(draft.raw_input)
            defaults["mentions"] = " ".join(draft.mentions)
            await interaction.response.send_modal(
                self.parent._AnnounceModal(self.parent, None, defaults=defaults)
            )

        @discord.ui.button(label="📣 Publier", style=discord.ButtonStyle.success)
        async def publish(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            variant = draft.variants[draft.chosen]
            channel = self.parent._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
            if draft.channel_id:
                ch = interaction.guild.get_channel(draft.channel_id)
                if isinstance(ch, discord.TextChannel):
                    channel = ch
            if not channel:
                await interaction.response.send_message(
                    f"❌ Salon d'annonces #{ANNONCE_CHANNEL_NAME} introuvable.",
                    ephemeral=True,
                )
                return
            blocked = await self.parent._moderate(
                f"{variant.title}\n{variant.description}\n{variant.cta or ''}"
            )
            if blocked:
                await interaction.response.send_message(f"⚠️ {blocked}", ephemeral=True)
                return
            content, allowed_mentions = self.parent._build_mentions_payload(interaction.guild, draft.mentions)
            embed = self.parent._variant_to_embed(variant)
            await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
            await interaction.response.edit_message(content="✅ Annonce publiée.", embed=None, view=None)

        @discord.ui.button(label="⏱️ Programmer", style=discord.ButtonStyle.primary)
        async def schedule(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            if interaction.guild is None:
                await interaction.response.send_message(
                    "❌ Action disponible uniquement sur le serveur.",
                    ephemeral=True,
                )
                return
            try:
                await interaction.response.send_modal(self.parent._ScheduleModal(self.parent, self.user_id))
            except discord.HTTPException as exc:
                log.exception("Impossible d'ouvrir le modal planning: %s", exc)
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ Impossible d'ouvrir le formulaire (erreur Discord).",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Impossible d'ouvrir le formulaire (erreur Discord).",
                        ephemeral=True,
                    )

        @discord.ui.button(label="🗑️ Annuler", style=discord.ButtonStyle.danger)
        async def cancel(self, interaction: discord.Interaction, _):
            self.parent._drafts.pop(self.user_id, None)
            await interaction.response.edit_message(content="Brouillon annulé.", embed=None, view=None)

    class _ScheduleModal(discord.ui.Modal, title="⏱️ Programmer l'annonce"):
        def __init__(self, parent: "AnnounceAICog", user_id: int):
            super().__init__(timeout=300)
            self.parent = parent
            self.user_id = user_id
            self.when = discord.ui.TextInput(
                label="Quand ?",
                style=discord.TextStyle.short,
                placeholder="Ex: demain 20:30 ou 27/09 20:30",
                required=True,
                max_length=64,
            )
            self.channel = discord.ui.TextInput(
                label="Salon (optionnel)",
                placeholder=f"laisser vide = #{ANNONCE_CHANNEL_NAME}",
                style=discord.TextStyle.short,
                required=False,
                max_length=64,
            )
            self.add_item(self.when)
            self.add_item(self.channel)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon expiré.", ephemeral=True)
                return
            if dateparser is None:
                await interaction.response.send_message(
                    "Le parsing de date n'est pas disponible. Installe `dateparser`.",
                    ephemeral=True,
                )
                return
            dt = dateparser.parse(str(self.when.value), settings={"RETURN_AS_TIMEZONE_AWARE": True})
            if not dt:
                await interaction.response.send_message("⛔ Date/heure invalide.", ephemeral=True)
                return
            channel_name = str(self.channel.value).strip()
            channel = None
            if channel_name:
                channel = resolve_text_channel(interaction.guild, default_name=channel_name)
            if not channel:
                channel = self.parent._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
            if not channel:
                await interaction.response.send_message(
                    f"❌ Salon #{ANNONCE_CHANNEL_NAME} introuvable.",
                    ephemeral=True,
                )
                return

            variant = draft.variants[draft.chosen]
            sched = ScheduledAnnounce(
                id=str(uuid.uuid4())[:8],
                guild_id=interaction.guild_id,
                channel_id=channel.id,
                author_id=interaction.user.id,
                variant=variant,
                run_at_iso=dt.astimezone().isoformat(),
                mentions=draft.mentions,
                status="scheduled",
            )
            self.parent._scheduled[sched.id] = sched
            ok = await self.parent._save_to_console(interaction.guild, sched)
            if not ok:
                await interaction.response.send_message(
                    "⚠️ Sauvegarde dans #console impossible; la programmation pourrait se perdre.",
                    ephemeral=True,
                )
                return
            await interaction.response.edit_message(
                content=(
                    f"✅ Annonce programmée pour **{dt.astimezone().strftime('%d/%m %H:%M')}** "
                    f"dans <#{channel.id}> (id `{sched.id}`)."
                ),
                view=None,
            )

    @commands.command(name="annonce", help="Lance le formulaire d'annonce IA.")
    async def annonce_prefix(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply("❌ Commande réservée au staff.", mention_author=False)
            return
        view = self._StartView(self, ctx.author.id)
        message = await ctx.send("Clique pour ouvrir le formulaire d'annonce.", view=view)
        view.message = message

    @app_commands.command(name="annonce", description="Rédiger une annonce avec l'IA.")
    async def annonce_slash(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not self._is_staff(interaction.user):
            await interaction.response.send_message("❌ Commande réservée au staff.", ephemeral=True)
            return
        channel = self._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
        await interaction.response.send_modal(self._AnnounceModal(self, channel))

    @commands.command(name="annonce-model", help="Change le modèle IA pour l'annonce.")
    async def annonce_model(self, ctx: commands.Context, *, model: Optional[str] = None) -> None:
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply("❌ Commande réservée au staff.", mention_author=False)
            return
        if not model:
            await ctx.reply(f"Modèle actuel: `{self._model}`", mention_author=False)
            return
        self._model = model.strip()
        await ctx.reply(f"Modèle annonce mis à jour: `{self._model}`", mention_author=False)

    @commands.command(name="annonce-list", help="Liste les annonces programmées.")
    async def annonce_list(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply("❌ Commande réservée au staff.", mention_author=False)
            return
        rows = []
        for sched in self._scheduled.values():
            if sched.guild_id != ctx.guild.id:
                continue
            if sched.status != "scheduled":
                continue
            try:
                dt = datetime.fromisoformat(sched.run_at_iso)
            except Exception:
                dt = None
            when = dt.astimezone().strftime("%d/%m %H:%M") if dt else "inconnu"
            rows.append(f"`{sched.id}` • {when} • <#{sched.channel_id}>")
        if not rows:
            await ctx.reply("Aucune annonce programmée.", mention_author=False)
            return
        content = "\n".join(rows)
        if len(content) > 1900:
            content = content[:1900] + "…"
        await ctx.reply(content, mention_author=False)

    @commands.command(name="annonce-cancel", help="Annule une annonce programmée.")
    async def annonce_cancel(self, ctx: commands.Context, announce_id: str) -> None:
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply("❌ Commande réservée au staff.", mention_author=False)
            return
        sched = self._scheduled.get(announce_id)
        if not sched or sched.guild_id != ctx.guild.id:
            await ctx.reply("ID d'annonce introuvable.", mention_author=False)
            return
        sched.status = "canceled"
        await self._update_console_entry(ctx.guild, sched)
        await ctx.reply(f"Annonce `{announce_id}` annulée.", mention_author=False)

    @tasks.loop(seconds=SCHEDULER_INTERVAL)
    async def scheduler_loop(self) -> None:
        for guild in self.bot.guilds:
            await self._load_all_from_console(guild)

        now = datetime.now(timezone.utc)
        to_publish: List[ScheduledAnnounce] = []
        for scheduled in list(self._scheduled.values()):
            if scheduled.status != "scheduled":
                continue
            try:
                run_at = datetime.fromisoformat(scheduled.run_at_iso)
            except Exception:
                continue
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at <= now:
                to_publish.append(scheduled)
        for scheduled in to_publish:
            await self._publish(scheduled)

    @scheduler_loop.before_loop
    async def before_scheduler(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    bot.remove_command("annonce")
    await bot.add_cog(AnnounceAICog(bot))
