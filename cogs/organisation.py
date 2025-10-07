from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import discord
from discord.ext import commands
from utils.openai_config import resolve_staff_model
from utils.console_store import ConsoleStore

from utils.datetime_utils import parse_duration, parse_french_datetime
from utils.image_config import load_image_settings

try:  # pragma: no cover - optional runtime dependency
    from openai import AsyncOpenAI
except Exception:  # pragma: no cover - tests may run without openai
    AsyncOpenAI = None

try:  # pragma: no cover - Pillow is optional but recommended
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - allow runtime without Pillow overlay
    Image = ImageDraw = ImageFont = None

log = logging.getLogger(__name__)

_CANCEL_WORDS = {"annuler", "annule", "cancel", "stop", "quit", "exit"}
_EVENT_COLORS = {"donjon": 0x3498DB, "drop": 0x9B59B6, "autre": 0x2ECC71}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default



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
    return "inference_config" in lowered and ("unexpected" in lowered or "unrecognized" in lowered or "unsupported" in lowered or "not supported" in lowered or "unknown" in lowered)

def _coerce_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    maybe = getattr(value, "value", None)
    if isinstance(maybe, str):
        return maybe.strip()
    maybe = getattr(value, "text", None)
    if isinstance(maybe, str):
        return maybe.strip()
    if isinstance(value, dict):
        text_val = value.get("value") or value.get("text") or value.get("content")
        if isinstance(text_val, str):
            return text_val.strip()
    maybe = getattr(value, "content", None)
    if isinstance(maybe, str):
        return maybe.strip()
    return ""

def _to_dict(obj: Any) -> dict[str, Any]:
    for attr in ("model_dump", "to_dict"):
        try:
            fn = getattr(obj, attr, None)
            if callable(fn):
                data = fn()
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    try:
        data = obj.__dict__  # type: ignore[attr-defined]
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _gather_text_nodes(node: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(node, str):
        value = node.strip()
        if value:
            texts.append(value)
        return texts
    if not isinstance(node, (dict, list, tuple)):
        node = _to_dict(node)
        if not isinstance(node, (dict, list, tuple)):
            return texts
    if isinstance(node, dict):
        direct = node.get("output_text") or node.get("text") or node.get("content")
        candidate = _coerce_text_value(direct)
        if candidate:
            texts.append(candidate)
        value = node.get("value")
        candidate = _coerce_text_value(value)
        if candidate:
            texts.append(candidate)
        json_payload = node.get("json")
        if isinstance(json_payload, (dict, list)):
            texts.append(json.dumps(json_payload, ensure_ascii=False))
        else:
            candidate = _coerce_text_value(json_payload)
            if candidate:
                texts.append(candidate)
        schema_payload = node.get("json_schema")
        if isinstance(schema_payload, (dict, list)):
            texts.append(json.dumps(schema_payload, ensure_ascii=False))
        elif schema_payload is not None:
            candidate = _coerce_text_value(schema_payload)
            if candidate:
                texts.append(candidate)
        for key in ("content", "message", "response", "output", "outputs", "choices", "delta", "result", "data"):
            child = node.get(key)
            if child is not None:
                texts.extend(_gather_text_nodes(child))
        return texts
    if isinstance(node, (list, tuple)):
        for item in node:
            texts.extend(_gather_text_nodes(item))
    return texts


def _extract_generated_text(resp: Any) -> str:
    text = _coerce_text_value(getattr(resp, "output_text", None))
    if text:
        return text
    data = _to_dict(resp)
    candidates = _gather_text_nodes(data)
    for candidate in candidates:
        if candidate:
            return candidate
    return ""




STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
DEFAULT_MODEL = resolve_staff_model()
ANNONCE_CHANNEL_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "#organisation")
CONSOLE_CHANNEL_NAME = os.getenv("CONSOLE_CHANNEL_NAME", "console")
CREATE_THREAD = os.getenv("ORGANISATION_CREATE_THREAD", "1") != "0"
QUESTION_TIMEOUT = _env_int("ORGANISATION_DM_TIMEOUT", 900)
TEMPERATURE = _env_float("OPENAI_STAFF_TEMPERATURE", _env_float("IASTAFF_TEMPERATURE", 0.3))
MAX_OUTPUT_TOKENS = _env_int("OPENAI_STAFF_MAX_OUTPUT_TOKENS", _env_int("IASTAFF_MAX_OUTPUT_TOKENS", 800))
MODERATION_ENABLED = os.getenv("ORGANISATION_MODERATION", "1") != "0"

SIGNUP_EMOJI_RAW = os.getenv("SIGNUP_EMOJI", "✋")
SIGNUP_WAITLIST = os.getenv("SIGNUP_WAITLIST", "1") != "0"
SIGNUP_UPDATE_EMBED = os.getenv("SIGNUP_UPDATE_EMBED", "1") != "0"
ORGA_STYLE = os.getenv("ORGA_STYLE", "clean")

IMAGE_DEFAULTS = load_image_settings()


@dataclass
class ImageMeta:
    payload: Optional[bytes]
    prompt: str = ""
    filename: str = ""
    mode: str = "hybrid"
    format: str = "png"
    model: str = "gpt-image-1"
    background: str = "auto"
    error: Optional[str] = None

    @property
    def has_image(self) -> bool:
        return self.payload is not None


@dataclass
class CollectedInfo:
    type_category: str
    type_label: str
    title: str
    when_raw: str
    when_dt: Optional[datetime]
    duration_raw: str
    objective: str
    requirements: str
    slots: str
    vocal: str
    mentions_raw: str
    link: str
    lieu: Optional[str] = None
    public: str = "ouvert à tous"
    composition: Optional[str] = None
    use_image: bool = False
    image_prompt: str = ""
    image_text: str = ""

    @property
    def has_mentions(self) -> bool:
        raw = (self.mentions_raw or "").strip()
        if not raw:
            return False
        return raw.lower() not in {"aucun", "none", "sans", "-"}

    @property
    def mentions(self) -> Optional[str]:
        return self.mentions_raw.strip() if self.has_mentions else None

    def normalised(self, value: str, fallback: str = "—") -> str:
        content = (value or "").strip()
        return content if content else fallback


class ConversationCancelled(Exception):
    """Raised when the staff member cancels the workflow."""


class PublishView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=QUESTION_TIMEOUT)
        self.author_id = author_id
        self.value: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Cette confirmation est réservée à l'auteur de la commande.",
                ephemeral=interaction.guild is not None,
            )
            return False
        return True

    @discord.ui.button(label="Publier", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # noqa: D401 - discord callback
        self.value = True
        if interaction.response.is_done():
            await interaction.followup.send("Je publie l'annonce !")
        else:
            await interaction.response.send_message("Je publie l'annonce !")
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:  # noqa: D401 - discord callback
        self.value = False
        if interaction.response.is_done():
            await interaction.followup.send("Commande annulée.")
        else:
            await interaction.response.send_message("Commande annulée.")
        self.stop()


class OrganisationFlow(commands.Cog):
    """Assistant DM pour générer et publier une annonce d'organisation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._client: Optional[AsyncOpenAI] = None
        self._supports_response_format = True
        self._supports_temperature = True
        self._temperature_mode = "inference_config"
        self._load_error: Optional[str] = None
        self.console = ConsoleStore(self.bot, channel_name=CONSOLE_CHANNEL_NAME)
        self._events: dict[int, dict[str, Any]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self.image_settings = dict(IMAGE_DEFAULTS)

    async def cog_unload(self) -> None:
        # Close the OpenAI client when the cog is unloaded to free resources.
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

    def _type_emoji_and_color(self, type_label: str) -> tuple[str, int]:
        t = (type_label or "").lower()
        if "donjon" in t:
            return "🗝️", 0x5865F2
        if "exp" in t or "xp" in t:
            return "📈", 0x57F287
        if "drop" in t or "farm" in t:
            return "💎", 0xFEE75C
        if "pvp" in t:
            return "⚔️", 0xED4245
        return "🎯", 0x2F3136

    # ------------------------------------------------------------------
    # OpenAI helpers
    # ------------------------------------------------------------------
    def _system_prompt(self) -> str:
        return textwrap.dedent(
            """
            Tu aides le staff de la guilde «Évolution» (Dofus Retro) à rédiger une annonce claire,
            concise et motivante.
            Contraintes de style :
            - Français naturel, ton chaleureux mais pro. Pas de points d'exclamation répétés,
              pas de majuscules agressives.
            - 1 titre percutant (max ~60 caractères) + 1 accroche courte (1–2 phrases).
            - N'invente rien : si une info est absente, n'en parle pas.
            - Si on te fournit un emoji d'inscription et un nombre de places, termine par un CTA:
              «Réagis avec <EMOJI> pour t’inscrire — <N> places.» (ou «places illimitées» si pas de limite).
            Réponds au format JSON avec les clés: title (string), tagline (string), cta (string).
            """
        ).strip()
    def _json_schema(self) -> dict[str, Any]:
        return {
            "name": "OrganisationAnnouncement",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "tagline"],
                "properties": {
                    "title": {"type": "string"},
                    "tagline": {"type": "string"},
                    "cta": {"type": ["string", "null"]},
                },
            },
            "strict": True,
        }

    async def _ensure_client(self) -> bool:
        if self._client is not None:
            return True
        if AsyncOpenAI is None:
            self._load_error = "Le module openai n'est pas disponible sur cette instance."
            return False
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self._load_error = "OPENAI_API_KEY manquant."
            return False
        try:
            self._client = AsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            organization=os.getenv("OPENAI_ORG_ID") or None,
            project=os.getenv("OPENAI_PROJECT") or None,
        )
        except Exception as exc:
            self._load_error = f"Initialisation du client OpenAI impossible: {exc}"
            log.warning("Organisation: initialisation OpenAI impossible", exc_info=True)
            self._client = None
            return False
        self._load_error = None
        return True

    def _collect_payload(
        self,
        info: CollectedInfo,
        author: discord.abc.User,
        guild: Optional[discord.Guild] = None,
    ) -> dict[str, Any]:
        slots_int = self._parse_slots(info.slots)
        guild_name = (
            getattr(guild, "name", None)
            or getattr(getattr(author, "guild", None), "name", "Évolution")
        )
        payload: dict[str, Any] = {
            "type": info.type_label,
            "title": info.title,
            "objective": info.objective,
            "requirements": info.requirements,
            "duration": info.duration_raw,
            "when_iso": info.when_dt.isoformat() if info.when_dt else None,
            "when_raw": info.when_raw,
            "lieu": (info.lieu or "").strip() or None,
            "public": info.public or "ouvert à tous",
            "composition": (info.composition or "").strip() or None,
            "slots": info.slots,
            "slots_int": slots_int,
            "vocal": info.vocal,
            "link": info.link,
            "mentions": info.mentions,
            "signup_emoji": SIGNUP_EMOJI_RAW,
            "guild_name": guild_name,
            "author": getattr(author, "display_name", None) or getattr(author, "name", None),
            "style": ORGA_STYLE,
        }
        return payload

    def _parse_slots(self, raw: str) -> Optional[int]:
        value = (raw or "").strip().lower()
        if not value:
            return None
        normalized = (
            value.replace("é", "e")
            .replace("è", "e")
            .replace("ê", "e")
            .replace("∞", "infini")
        )
        if normalized in {"--", "-", "illimite", "illimite", "infini", "infinite"}:
            return None
        digits = re.sub(r"[^0-9]", "", value)
        if not digits:
            return None
        try:
            count = int(digits)
        except ValueError:
            return None
        return count if count > 0 else None

    def _preclean_when(self, text: str) -> str:
        cleaned = text or ""
        cleaned = re.sub(r"\bsame?i\b", "samedi", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(a)\b", "à", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
        return cleaned


    # ------------------------------------------------------------------
    # Embed helpers
    # ------------------------------------------------------------------
    def _bullets_block(self, info: CollectedInfo) -> str:
        lines: list[str] = []
        if info.when_dt:
            ts = int(info.when_dt.timestamp())
            lines.append(f"• **Date & heure :** <t:{ts}:F> (<t:{ts}:R>)")

        lieu = (getattr(info, "lieu", None) or "").strip()
        if lieu:
            lines.append(f"• **Lieu :** {lieu}")

        public_raw = getattr(info, "public", None)
        public_value = (public_raw or "ouvert à tous").strip() or "ouvert à tous"
        lines.append(f"• **Public :** {public_value}")

        composition = (getattr(info, "composition", None) or "").strip()
        if composition:
            lines.append(f"• **Composition :** {composition}")

        if info.objective:
            lines.append(f"• **Objectifs :** {info.objective}")

        if info.duration_raw:
            lines.append(f"• **Durée :** {info.duration_raw}")

        vocal_value = (info.vocal or "").strip()
        if vocal_value and vocal_value.lower() not in {"non", "aucun", "--"}:
            lines.append(f"• **Vocal :** {info.vocal}")

        return "\n".join(lines)

    def _build_embed(
        self,
        ctx: commands.Context,
        info: CollectedInfo,
        ai: dict[str, Any],
    ) -> discord.Embed:
        emoji, color = self._type_emoji_and_color(info.type_label)
        title_ai = (ai.get("title") or info.title or info.type_label or "Événement").strip()
        title = title_ai
        if info.title and info.type_label and info.type_label.lower() not in info.title.lower():
            title = f"{info.type_label} — {title_ai}"
        full_title = f"{emoji} {title}".strip()[:256]

        tagline = (ai.get("tagline") or info.objective or "").strip()
        bullets = self._bullets_block(info)
        cta = (ai.get("cta") or "").strip()

        desc_parts = []
        if tagline:
            desc_parts.append(tagline)
        if bullets:
            desc_parts.append(bullets)
        if cta:
            desc_parts.append(cta)

        description = "\n\n".join(desc_parts).strip()[:4096]

        embed = discord.Embed(title=full_title, description=description, color=color)
        if info.when_dt:
            embed.timestamp = info.when_dt

        footer = f"Préparé par : {getattr(ctx.author, 'display_name', 'Équipe staff')}"
        try:
            avatar = getattr(ctx.author, 'display_avatar', None)
            if avatar:
                embed.set_footer(text=footer, icon_url=getattr(avatar, 'url', None))
            else:
                embed.set_footer(text=footer)
        except Exception:
            embed.set_footer(text=footer)

        slots_int = self._parse_slots(info.slots)
        label = f"Participants (0/{slots_int})" if slots_int else "Participants"
        embed.add_field(name=label, value="—", inline=False)

        return embed

    def _short_time_label(self, info: CollectedInfo) -> str:
        if not info.when_dt:
            return ""
        weekdays = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
        dt = info.when_dt
        weekday = weekdays[dt.weekday() % 7]
        return f"{weekday} {dt.day:02d} • {dt.strftime('%Hh%M')}"

    def _build_image_prompt(self, info: CollectedInfo) -> str:
        base = (
            "Illustration fan-art inspirée de l'univers Dofus Retro, rendu jeu vidéo, "
            "isométrique 2.5D, couleurs vibrantes, sans watermark, sans logo officiel, "
            "composition lisible pour une annonce Discord."
        )
        details = (info.image_prompt or "").strip()
        constraint = (
            "sans aucun texte dans l'image"
            if self.image_settings.get("mode", "hybrid") == "hybrid"
            else ""
        )
        return "\n".join(part for part in (base, details, constraint) if part)

    async def _generate_image_bytes(
        self,
        prompt: str,
        *,
        want_transparent: bool,
    ) -> tuple[Optional[bytes], Optional[str]]:
        if self._client is None or getattr(self._client, "images", None) is None:
            return None, "client-missing"
        settings = self.image_settings
        background = "transparent" if want_transparent else settings.get("background", "auto")
        fmt = settings.get("output_format", "png").lower()
        if background == "transparent" and fmt not in {"png", "webp"}:
            background = "auto"
        kwargs = {
            "model": settings.get("model", "gpt-image-1"),
            "prompt": prompt,
            "size": settings.get("size", "1024x1024"),
            "quality": settings.get("quality", "high"),
            "background": background,
            "output_format": settings.get("output_format", "png"),
            "output_compression": settings.get("output_compression", 85),
        }
        try:
            result = await self._client.images.generate(**kwargs)
        except Exception as exc:  # pragma: no cover - network failures
            log.warning("Organisation: génération image impossible", exc_info=True)
            return None, str(exc)
        data = getattr(result, "data", None)
        if not data:
            return None, "missing-data"
        b64_payload = getattr(data[0], "b64_json", None)
        if not b64_payload:
            return None, "missing-b64"
        try:
            return base64.b64decode(b64_payload), None
        except Exception as exc:  # pragma: no cover - decode issues
            log.warning("Organisation: décodage image impossible", exc_info=True)
            return None, str(exc)

    def _overlay_text_labels(self, info: CollectedInfo, ai: dict[str, Any]) -> tuple[str, str]:
        raw = (info.image_text or "").strip()
        if raw:
            parts = [chunk.strip() for chunk in re.split(r"[\n|/]", raw) if chunk.strip()]
            title = parts[0][:60]
            subtitle = parts[1][:60] if len(parts) > 1 else ""
            return title, subtitle
        title = (ai.get("title") or info.title or info.type_label or "Événement").strip()[:60]
        return title, self._short_time_label(info)[:60]

    def _load_font(self, size: int) -> Optional["ImageFont.FreeTypeFont"]:
        if ImageFont is None:
            return None
        font_path = self.image_settings.get("font_path")
        if font_path:
            try:
                return ImageFont.truetype(font_path, size=size)
            except Exception:
                pass
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except Exception:
            try:
                return ImageFont.load_default()
            except Exception:
                return None

    def _overlay_text(self, blob: bytes, title: str, subtitle: str) -> bytes:
        if Image is None or ImageDraw is None or ImageFont is None:
            return blob
        try:
            image = Image.open(io.BytesIO(blob)).convert("RGBA")
        except Exception:
            return blob
        width, height = image.size
        draw = ImageDraw.Draw(image)
        font_title = self._load_font(max(38, width // 14))
        font_sub = self._load_font(max(24, width // 22))
        if font_title is None:
            return blob

        def draw_centered(y_pos: int, text: str, font: "ImageFont.ImageFont") -> int:
            if not text:
                return y_pos
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=3)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x_pos = (width - text_w) // 2
            draw.text(
                (x_pos, y_pos),
                text,
                font=font,
                fill="white",
                stroke_width=3,
                stroke_fill="black",
            )
            return y_pos + text_h + 8

        current_y = int(height * 0.08)
        current_y = draw_centered(current_y, title, font_title)
        if subtitle and font_sub is not None:
            draw_centered(current_y, subtitle, font_sub)

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    async def _prepare_image(self, ctx: commands.Context, info: CollectedInfo, ai: dict[str, Any]) -> ImageMeta:
        settings = self.image_settings
        enabled = settings.get("enable", True)
        if not enabled or not info.use_image:
            return ImageMeta(None, mode=settings.get("mode", "hybrid"))
        if self._client is None:
            return ImageMeta(None, error=self._load_error or "client-missing")

        prompt = self._build_image_prompt(info)
        mode = settings.get("mode", "hybrid")
        want_transparent = mode == "hybrid"
        payload, error = await self._generate_image_bytes(prompt, want_transparent=want_transparent)
        if not payload:
            return ImageMeta(
                None,
                prompt=prompt,
                mode=mode,
                format=settings.get("output_format", "png"),
                model=settings.get("model", "gpt-image-1"),
                background="transparent" if want_transparent else settings.get("background", "auto"),
                error=error or "generation-failed",
            )

        image_format = settings.get("output_format", "png").lower()
        background = "transparent" if want_transparent else settings.get("background", "auto")

        if mode == "hybrid":
            title, subtitle = self._overlay_text_labels(info, ai)
            payload = self._overlay_text(payload, title, subtitle)
            image_format = "png"
            background = "transparent"

        filename = f"organisation-{ctx.author.id}.{image_format}"
        return ImageMeta(
            payload=payload,
            prompt=prompt,
            filename=filename,
            mode=mode,
            format=image_format,
            model=settings.get("model", "gpt-image-1"),
            background=background,
        )

    async def _moderate(self, content: str) -> bool:
        if not MODERATION_ENABLED or not content.strip():
            return False
        if self._client is None or getattr(self._client, "moderations", None) is None:
            return False
        try:
            result = await self._client.moderations.create(
                model="omni-moderation-latest",
                input=content,
            )
        except Exception:  # pragma: no cover - moderation failures are non-fatal
            log.warning("Organisation: modération indisponible", exc_info=True)
            return False
        try:
            return any(getattr(item, "flagged", False) for item in result.results)
        except Exception:
            return False

    async def _call_openai(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            return {}
        body = json.dumps(payload, ensure_ascii=False)
        messages = [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": self._system_prompt()}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": body}],
            },
        ]
        request_kwargs: dict[str, Any] = {
            "model": DEFAULT_MODEL,
            "input": messages,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        if self._supports_response_format:
            request_kwargs["response_format"] = {"type": "json_schema", "json_schema": self._json_schema()}
        if self._supports_temperature:
            if self._temperature_mode == "inference_config":
                request_kwargs["inference_config"] = {"temperature": TEMPERATURE}
            elif self._temperature_mode == "legacy":
                request_kwargs["temperature"] = TEMPERATURE

        while True:
            try:
                response = await self._client.responses.create(**request_kwargs)
                break
            except TypeError as exc:
                handled = False
                message = getattr(exc, "message", None) or str(exc)
                lowered = message.lower()
                if (
                    self._supports_temperature
                    and self._temperature_mode == "inference_config"
                    and _is_inference_config_error(exc)
                ):
                    self._temperature_mode = "legacy"
                    request_kwargs.pop("inference_config", None)
                    request_kwargs["temperature"] = TEMPERATURE
                    handled = True
                elif self._supports_response_format and "response_format" in lowered:
                    self._supports_response_format = False
                    request_kwargs.pop("response_format", None)
                    handled = True
                elif "instructions" in lowered and "instructions" in request_kwargs:
                    request_kwargs["system"] = request_kwargs.pop("instructions")
                    handled = True
                elif "input" in lowered and isinstance(request_kwargs.get("input"), list):
                    request_kwargs["input"] = body
                    handled = True
                if handled:
                    continue
                raise
            except Exception as exc:
                handled = False
                message = getattr(exc, "message", None) or str(exc)
                lowered = message.lower()
                if (
                    self._supports_temperature
                    and self._temperature_mode == "inference_config"
                    and _is_inference_config_error(exc)
                ):
                    self._temperature_mode = "legacy"
                    request_kwargs.pop("inference_config", None)
                    request_kwargs["temperature"] = TEMPERATURE
                    handled = True
                elif self._supports_temperature and _is_temperature_error(exc):
                    self._supports_temperature = False
                    self._temperature_mode = "disabled"
                    request_kwargs.pop("temperature", None)
                    request_kwargs.pop("inference_config", None)
                    handled = True
                elif self._supports_response_format and "response_format" in lowered:
                    self._supports_response_format = False
                    request_kwargs.pop("response_format", None)
                    handled = True
                elif "instructions" in lowered and "instructions" in request_kwargs:
                    request_kwargs["system"] = request_kwargs.pop("instructions")
                    handled = True
                elif "input" in lowered and isinstance(request_kwargs.get("input"), list):
                    request_kwargs["input"] = body
                    handled = True
                if handled:
                    continue
                raise

        text = _extract_generated_text(response)
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("Organisation: reponse IA non JSON: %s", text)
            return {}

    def _resolve_channel(self, ctx: commands.Context) -> Optional[discord.TextChannel]:
        if not ctx.guild:
            return None
        name = ANNONCE_CHANNEL_NAME.lstrip("#")
        channel = discord.utils.get(ctx.guild.text_channels, name=name)
        if channel:
            return channel
        if isinstance(ctx.channel, discord.TextChannel):
            return ctx.channel
        return None

    async def _prompt(
        self,
        ctx: commands.Context,
        dm: discord.DMChannel,
        question: str,
        *,
        allow_blank: bool = False,
    ) -> str:
        await dm.send(question)
        while True:
            try:
                message = await self.bot.wait_for(
                    "message",
                    timeout=QUESTION_TIMEOUT,
                    check=lambda m: m.author == ctx.author and isinstance(m.channel, discord.DMChannel),
                )
            except asyncio.TimeoutError as exc:
                raise exc
            content = (message.content or "").strip()
            if content.lower() in _CANCEL_WORDS:
                raise ConversationCancelled()
            if content or allow_blank:
                return content
            await dm.send("Merci de répondre par un message (tu peux écrire 'aucun').")

    async def _collect_info(self, ctx: commands.Context) -> CollectedInfo:
        dm = await ctx.author.create_dm()
        await dm.send("Réponds à ces questions pour préparer l'annonce (écris 'annuler' pour stopper).")

        type_label = await self._prompt(ctx, dm, "Type d'événement ? (ex. Sortie EXP)")
        type_category = (type_label or "autre").split()[0].lower()
        title = await self._prompt(ctx, dm, "Titre de l'annonce ?")

        when_input = await self._prompt(ctx, dm, "Quand ? (date + heure, ex. samedi 21h)")
        when_clean = self._preclean_when(when_input)
        when_dt = parse_french_datetime(when_clean)

        duration_raw = await self._prompt(
            ctx,
            dm,
            "Durée prévue ? (ex. 2h, réponds 'aucune' si non applicable)",
            allow_blank=True,
        )
        if (duration_raw or "").strip().lower() in {"aucun", "aucune", "none", "--"}:
            duration_raw = ""
        objective = await self._prompt(ctx, dm, "Objectifs principaux ?")
        requirements = await self._prompt(
            ctx,
            dm,
            "Prérequis / niveau conseillé ? (écris 'aucun' si libre)",
            allow_blank=True,
        )
        slots = await self._prompt(ctx, dm, "Nombre de places ? (écris 'illimité' si libre)")
        vocal = await self._prompt(
            ctx,
            dm,
            "Vocal ? (obligatoire / optionnel / aucun)",
            allow_blank=True,
        )
        mentions_raw = await self._prompt(
            ctx,
            dm,
            "Mentions à ajouter ? (@here, @everyone, rôles, 'aucun' sinon)",
            allow_blank=True,
        )
        link = await self._prompt(
            ctx,
            dm,
            "Lien utile ? (inscription, doc, etc. — laisse vide si aucun)",
            allow_blank=True,
        )
        lieu = await self._prompt(
            ctx,
            dm,
            "Lieu / zone ? (optionnel)",
            allow_blank=True,
        )
        public = await self._prompt(
            ctx,
            dm,
            "Public visé ? (ex. guilde, alliance, ouvert à tous)",
            allow_blank=True,
        )
        composition = await self._prompt(
            ctx,
            dm,
            "Composition souhaitée ? (classes, roles — optionnel)",
            allow_blank=True,
        )

        image_choice = await self._prompt(
            ctx,
            dm,
            "Souhaites-tu une image IA pour illustrer l'annonce ? (oui/non)",
        )
        use_image = image_choice.strip().lower() in {"oui", "yes", "y", "o"}
        image_prompt = ""
        image_text = ""
        if use_image:
            image_prompt = await self._prompt(
                ctx,
                dm,
                (
                    "D?cris l'image : sc?ne Dofus (lieu, monstre, ambiance).\n"
                    "Tu peux pr?ciser le style (isom?trique, pixel-art, affiche...)."
                ),
            )
            if Image and ImageDraw and ImageFont:
                image_text = await self._prompt(
                    ctx,
                    dm,
                    (
                        "Quel texte exact sur l'image ? (Titre + sous-titre).\n"
                        "R?ponds 'aucun' pour laisser l'image sans texte."
                    ),
                    allow_blank=True,
                )
                if image_text.strip().lower() in {"aucun", "none", "--"}:
                    image_text = ""
            else:
                image_text = ""
        info = CollectedInfo(
            type_category=type_category,
            type_label=type_label,
            title=title,
            when_raw=when_clean,
            when_dt=when_dt,
            duration_raw=duration_raw,
            objective=objective,
            requirements=requirements,
            slots=slots,
            vocal=vocal,
            mentions_raw=mentions_raw,
            link=link,
            lieu=lieu,
            public=public or "ouvert à tous",
            composition=composition,
            use_image=use_image,
            image_prompt=image_prompt,
            image_text=image_text,
        )
        return info

    def _moderation_source(self, ai: dict[str, Any], info: CollectedInfo) -> str:
        parts = [ai.get("title"), ai.get("tagline"), ai.get("body"), ai.get("cta"), info.objective]
        return "\n".join(part for part in parts if part)

    @commands.command(name="organisation")
    @commands.has_role(STAFF_ROLE_NAME)
    async def organisation(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.reply("Commande disponible uniquement sur le serveur.", mention_author=False)
            return

        lock = self._locks.setdefault(ctx.author.id, asyncio.Lock())
        if lock.locked():
            await ctx.reply("Tu as déjà une organisation en cours.", mention_author=False)
            return

        async with lock:
            try:
                info = await self._collect_info(ctx)
            except ConversationCancelled:
                await ctx.author.send("Organisation annul?e ? ta demande.")
                return
            except discord.Forbidden:
                await ctx.reply(
                    "Je n'arrive pas ? t'?crire en MP. Active les messages priv?s du serveur "
                    "({Parametres > Confidentialite}) puis relance la commande.",
                    mention_author=False,
                )
                return
            except asyncio.TimeoutError:
                await ctx.author.send("Temps ?coul?, organisation annul?e.")
                return
            except Exception as exc:
                log.exception("Organisation: erreur collecte info", exc_info=True)
                # Ne pas r?essayer en DM si on vient d'?chouer
                await ctx.reply("Erreur pendant la collecte des informations.", mention_author=False)
                return

            dm = await ctx.author.create_dm()

            payload = self._collect_payload(info, ctx.author, ctx.guild)
            ai_data: dict[str, Any] = {}
            ai_used = False

            ensure_ok = await self._ensure_client()
            if ensure_ok:
                try:
                    ai_data = await self._call_openai(payload)
                    ai_used = bool(ai_data)
                except Exception:
                    log.warning("Organisation: OpenAI indisponible — fallback sans IA", exc_info=True)
                    await dm.send("IA indisponible — génération manuelle en cours.")
                    ai_data = {}
            else:
                await dm.send("IA indisponible — génération manuelle en cours.")

            if not ai_data:
                ai_data = {
                    "title": info.title or info.type_label,
                    "tagline": info.objective or info.title,
                    "cta": None,
                }

            embed = self._build_embed(ctx, info, ai_data)

            moderation_input = self._moderation_source(ai_data, info)
            try:
                flagged = await self._moderate(moderation_input)
            except Exception:
                flagged = False
            if flagged:
                await dm.send("Contenu bloqué par la modération — publication annulée.")
                return

            image_meta = await self._prepare_image(ctx, info, ai_data)

            preview_embed = embed.copy()
            preview_files: list[discord.File] = []
            if image_meta.has_image:
                preview_filename = f"preview.{image_meta.format}"
                preview_embed.set_image(url=f"attachment://{preview_filename}")
                preview_files.append(
                    discord.File(io.BytesIO(image_meta.payload), filename=preview_filename)
                )
            elif image_meta.error:
                try:
                    lowered_error = str(image_meta.error).lower()
                    if "must be verified" in lowered_error:
                        await dm.send(
                            "⚠️ L'illustration IA n'a pas pu être générée : "
                            "l'organisation OpenAI liée à la clé API n'est pas vérifiée pour les images. "
                            "L'annonce sera publiée sans visuel IA."
                        )
                    else:
                        await dm.send(
                            "⚠️ L'illustration IA n'a pas pu être générée (erreur API). "
                            "L'annonce sera publiée sans visuel IA."
                        )
                except Exception:
                    pass

            await dm.send(
                "Voici l'aperçu de l'annonce :",
                embed=preview_embed,
                files=preview_files or None,
            )

            channel = self._resolve_channel(ctx)
            if channel is None:
                await dm.send("Impossible de trouver le salon de publication — opération annulée.")
                return

            final_embed = embed.copy()
            files_kwargs: dict[str, Any] = {}
            if image_meta.has_image:
                final_embed.set_image(url=f"attachment://{image_meta.filename}")
                files_kwargs["file"] = discord.File(
                    io.BytesIO(image_meta.payload),
                    filename=image_meta.filename,
                )

            send_kwargs: dict[str, Any] = {
                "embed": final_embed,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if info.has_mentions:
                send_kwargs["content"] = info.mentions
                send_kwargs["allowed_mentions"] = discord.AllowedMentions(
                    users=True,
                    roles=True,
                    everyone=True,
                )
            send_kwargs.update(files_kwargs)

            message = await channel.send(**send_kwargs)
            # Ajoute la r?action d'inscription pour aligner le CTA
            try:
                await message.add_reaction(SIGNUP_EMOJI_RAW)
            except Exception:
                pass

            if SIGNUP_WAITLIST and CREATE_THREAD and hasattr(message, "create_thread"):
                try:
                    thread_name = (final_embed.title or "Inscriptions").strip()[:90]
                    thread = await message.create_thread(name=thread_name or "Inscriptions")
                    await thread.send("Repondez ici pour vous inscrire !")
                except Exception:  # pragma: no cover - thread optional
                    log.warning("Organisation: impossible de créer le thread", exc_info=True)

            await dm.send(f"Annonce publiee sur {channel.mention}.")

            record = {
                "event_id": message.id,
                "guild_id": ctx.guild.id,
                "channel_id": message.channel.id,
                "ai_used": ai_used,
                "ai_model": DEFAULT_MODEL if ai_used else None,
                "title": ai_data.get("title") or info.title,
                "objective": info.objective,
                "type": info.type_label,
                "when_iso": info.when_dt.isoformat() if info.when_dt else None,
                "link": info.link,
                "slots": info.slots,
                "image_enabled": image_meta.has_image,
                "image_prompt": image_meta.prompt,
                "image_mode": image_meta.mode,
                "image_model": image_meta.model,
                "image_format": image_meta.format,
                "image_error": image_meta.error,
            }
            await self.console.upsert(record)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OrganisationFlow(bot))



