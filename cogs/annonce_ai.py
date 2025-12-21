from __future__ import annotations

import os
import re
import json
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from utils.channel_resolver import resolve_text_channel
from utils.openai_config import resolve_staff_model, build_async_openai_client, resolve_reasoning_effort

try:
    from openai import AsyncOpenAI  # SDK officiel
except Exception:
    AsyncOpenAI = None

try:
    import dateparser  # pour parser "demain 20:30", "27/09 21h", etc.
except Exception:
    dateparser = None

ANNOUNCE_DB_BLOCK = os.getenv("ANNOUNCE_DB_BLOCK", "announce")  # tag du code-fence en #console
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", "Staff")
DEFAULT_MODEL = resolve_staff_model()
ANNONCE_CHANNEL_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "annonces")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", os.getenv("CONSOLE_CHANNEL_NAME", "console"))


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
    return "inference_config" in lowered and ("unexpected" in lowered or "unrecognized" in lowered or "unsupported" in lowered or "not supported" in lowered or "unknown" in lowered)


@dataclass
class Variant:
    style: str
    title: str
    description: str
    footer: Optional[str] = None
    color: str = "#2ECC71"
    mentions: List[str] = None
    image_url: Optional[str] = None
    cta: Optional[str] = None


@dataclass
class AnnounceDraft:
    id: str
    author_id: int
    guild_id: int
    channel_id: Optional[int]
    variants: List[Variant]
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
    status: str = "scheduled"  # scheduled, published, canceled
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
        self.scheduler_loop.start()

    def cog_unload(self) -> None:
        try:
            self.scheduler_loop.cancel()
        except Exception:
            pass

    # --------- helpers

    def _is_staff(self, member: discord.Member) -> bool:
        return any(r.name == STAFF_ROLE_NAME for r in getattr(member, "roles", []))

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
            "Interdits: majuscules abusives, spam d'emojis, promesses vagues, ping excessifs. "
            "Ajoute un titre fort, un corps structuré, optionnellement un CTA clair. "
            "N'invente pas de dates. Toute date doit venir de l'utilisateur. "
            "N'insère pas de formatage Markdown complexe (pas de tableaux)."
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
                                "mentions": {"type": "array", "items": {"type": "string"}},
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

    async def _ask_openai(self, fields: Dict[str, str]) -> List[Variant]:
        if not self._client:
            raise RuntimeError("OPENAI_API_KEY manquant ou librairie openai indisponible.")

        input_text = (
            f"Contexte guilde: Evolution (Dofus Retro).\n"
            f"Objectif annoncé: {fields.get('objectif')}\n"
            f"Cible: {fields.get('cible')}\n"
            f"Ton souhaité: {fields.get('ton')}\n"
            f"Message brut fourni par le staff:\n{fields.get('brut')}\n\n"
            f"Contraintes:\n- 3 variantes: Bref, Standard, RP léger (thème Dofus).\n"
            f"- Retourne STRICTEMENT le JSON demandé."
        )
        temperature_value = float(os.getenv("IASTAFF_TEMPERATURE", "0.4"))
        max_tokens = int(os.getenv("IASTAFF_MAX_OUTPUT_TOKENS", "1200"))
        request_kwargs: Dict[str, Any] = {
            "model": DEFAULT_MODEL,
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
            request_kwargs["response_format"] = {"type": "json_schema", "json_schema": self._json_schema()}
        while True:
            try:
                resp = await self._client.responses.create(**request_kwargs)
                break
            except TypeError as exc:
                handled = False
                if self._supports_temperature and self._temperature_mode == "inference_config" and _is_inference_config_error(exc):
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
                if self._supports_temperature and self._temperature_mode == "inference_config" and _is_inference_config_error(exc):
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
        if not text:
            if getattr(resp, "output", None):
                for msg in resp.output:
                    for c in getattr(msg, "content", []) or []:
                        c_type = getattr(c, "type", "")
                        if c_type in ("output_text", "text") and getattr(c, "text", ""):
                            text += c.text
                        elif c_type in ("json_schema", "json"):
                            payload = getattr(c, "json_schema", None) or getattr(c, "json", None) or getattr(c, "content", None)
                            if isinstance(payload, (dict, list)):
                                text += json.dumps(payload)
                            elif isinstance(payload, str):
                                text += payload
        if not text:
            raise RuntimeError("Réponse OpenAI vide.")

        data = json.loads(text)
        variants = []
        for v in data["variants"]:
            variants.append(Variant(
                style=v.get("style", ""),
                title=v.get("title", ""),
                description=v.get("description", ""),
                footer=v.get("footer"),
                color=v.get("color", "#2ECC71"),
                mentions=v.get("mentions") or [],
                image_url=v.get("image_url"),
                cta=v.get("cta"),
            ))
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
        return embed

    async def _moderate(self, text: str) -> Optional[str]:
        if not self._client or os.getenv("ANNONCE_SAFETY", "1") == "0":
            return None
        try:
            mod = await self._client.moderations.create(model="omni-moderation-latest", input=text)
            flagged = False
            if hasattr(mod, "results") and mod.results:
                flagged = bool(getattr(mod.results[0], "flagged", False))
            elif hasattr(mod, "output") and mod.output:
                flagged = bool(getattr(mod.output[0], "flagged", False))
            if flagged:
                return "Le message semble contrevenir aux règles de modération. Merci d'adoucir la formulation."
        except Exception:
            return None
        return None

    # --------- UI

    class _AnnounceModal(discord.ui.Modal, title="✍️ Rédiger une annonce"):
        def __init__(self, parent: "AnnounceAICog", channel: Optional[discord.TextChannel]):
            super().__init__(timeout=300)
            self.parent = parent
            self.pref_channel = channel

            self.obj = discord.ui.TextInput(label="🎯 Objectif (obligatoire)", style=discord.TextStyle.short, max_length=120)
            self.cible = discord.ui.TextInput(label="👥 Cible (ex: guilde entière / recrues / team donjon)", style=discord.TextStyle.short, required=False, max_length=80)
            self.ton = discord.ui.TextInput(label="🎨 Ton (ex: clair, motivant, RP léger…)", style=discord.TextStyle.short, required=False, max_length=60, default="clair, motivant")
            self.brut = discord.ui.TextInput(label="📝 Message brut (colle ce que tu veux annoncer)", style=discord.TextStyle.paragraph, max_length=2000)

            for comp in (self.obj, self.cible, self.ton, self.brut):
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
            }
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                variants = await self.parent._ask_openai(fields)
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur IA: {e}", ephemeral=True)
                return

            draft = AnnounceDraft(
                id=str(uuid.uuid4())[:8],
                author_id=interaction.user.id,
                guild_id=interaction.guild_id,
                channel_id=self.pref_channel.id if self.pref_channel else None,
                variants=variants,
                chosen=0,
                raw_input=fields,
            )
            self.parent._drafts[interaction.user.id] = draft
            embed = self.parent._variant_to_embed(variants[0])
            view = self.parent._PreviewView(self.parent, interaction.user.id)
            channel_label = self.pref_channel.mention if self.pref_channel else f"#{ANNONCE_CHANNEL_NAME}"
            await interaction.followup.send(
                f"✅ **Brouillon `{draft.id}` prêt.**\n• Variante: **{variants[0].style}**\n• Salon: {channel_label}\n\n"
                "Tu peux **Publier**, **Programmer**, ou **Parcourir les variantes** ci-dessous.",
                embed=embed, view=view, ephemeral=True
            )

    class _PreviewView(discord.ui.View):
        def __init__(self, parent: "AnnounceAICog", user_id: int):
            super().__init__(timeout=600)
            self.parent = parent
            self.user_id = user_id

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            return interaction.user.id == self.user_id

        @discord.ui.button(label="◀️ Variante", style=discord.ButtonStyle.secondary)
        async def prev_var(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            draft.chosen = (draft.chosen - 1) % len(draft.variants)
            embed = self.parent._variant_to_embed(draft.variants[draft.chosen])
            await interaction.response.edit_message(content=f"Variante: **{draft.variants[draft.chosen].style}**", embed=embed, view=self)

        @discord.ui.button(label="Variante ▶️", style=discord.ButtonStyle.secondary)
        async def next_var(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            draft.chosen = (draft.chosen + 1) % len(draft.variants)
            embed = self.parent._variant_to_embed(draft.variants[draft.chosen])
            await interaction.response.edit_message(content=f"Variante: **{draft.variants[draft.chosen].style}**", embed=embed, view=self)

        @discord.ui.button(label="📣 Publier", style=discord.ButtonStyle.success)
        async def publish(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            variant = draft.variants[draft.chosen]
            text = " ".join(variant.mentions) if variant.mentions else ""
            channel = self.parent._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
            if draft.channel_id:
                ch = interaction.guild.get_channel(draft.channel_id)
                if isinstance(ch, discord.TextChannel):
                    channel = ch
            if not channel:
                await interaction.response.send_message(f"❌ Salon d'annonces #{ANNONCE_CHANNEL_NAME} introuvable.", ephemeral=True)
                return
            blocked = await self.parent._moderate(f"{variant.title}\n{variant.description}\n{variant.cta or ''}")
            if blocked:
                await interaction.response.send_message(f"⚠️ {blocked}", ephemeral=True)
                return
            embed = self.parent._variant_to_embed(variant)
            await channel.send(content=text or None, embed=embed)
            await interaction.response.edit_message(content="✅ Annonce publiée.", embed=None, view=None)

        @discord.ui.button(label="⏱️ Programmer", style=discord.ButtonStyle.primary)
        async def schedule(self, interaction: discord.Interaction, _):
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon introuvable.", ephemeral=True)
                return
            await interaction.response.send_modal(self.parent._ScheduleModal(self.parent, self.user_id))

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
                label="Quand ? (ex: demain 20:30, 27/09 21h, 2025-09-27 20:30)",
                style=discord.TextStyle.short,
                placeholder="27/09 20:30",
                required=True,
                max_length=64,
            )
            self.channel = discord.ui.TextInput(
                label=f"Salon (laisser vide pour #{ANNONCE_CHANNEL_NAME})",
                style=discord.TextStyle.short, required=False, max_length=64,
            )
            self.add_item(self.when)
            self.add_item(self.channel)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            draft = self.parent._drafts.get(self.user_id)
            if not draft:
                await interaction.response.send_message("Brouillon expiré.", ephemeral=True)
                return
            if dateparser is None:
                await interaction.response.send_message("Le parsing de date n'est pas disponible. Installe `dateparser`.", ephemeral=True)
                return
            dt = dateparser.parse(str(self.when.value), settings={"RETURN_AS_TIMEZONE_AWARE": True})
            if not dt:
                await interaction.response.send_message("⛔ Date/heure invalide.", ephemeral=True)
                return
            channel_name = str(self.channel.value).strip()
            channel = None
            if channel_name:
                channel = resolve_text_channel(
                    interaction.guild,
                    default_name=channel_name,
                )
            if not channel:
                channel = self.parent._find_channel(interaction.guild, ANNONCE_CHANNEL_NAME)
            if not channel:
                await interaction.response.send_message(f"❌ Salon #{ANNONCE_CHANNEL_NAME} introuvable.", ephemeral=True)
                return

            variant = draft.variants[draft.chosen]
            sched = ScheduledAnnounce(
                id=str(uuid.uuid4())[:8],
                guild_id=interaction.guild_id,
                channel_id=channel.id,
                author_id=interaction.user.id,
                variant=variant,
                run_at_iso=dt.astimezone().isoformat(),
                status="scheduled",
            )
            self.parent._scheduled[sched.id] = sched
            ok = await self.parent._save_to_console(interaction.guild, sched)
            if not ok:
                await interaction.response.send_message("⚠️ Sauvegarde dans #console impossible; la programmation pourrait se perdre.", ephemeral=True)
                return
            await interaction.response.edit_message(
                content=f"✅ Annonce programmée pour **{dt.astimezone().strftime('%d/%m %H:%M')}** dans <#{channel.id}> (id `{sched.id}`).",
                view=None
            )

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
        return True

    async def _load_all_from_console(self, guild: discord.Guild) -> None:
        chan = await self._console_channel(guild)
        if not chan:
            return
        pattern = re.compile(rf"^```{re.escape(ANNOUNCE_DB_BLOCK)}\n(?P<body>.+?)\n```$", re.S)
        async for m in chan.history(limit=200):
            match = pattern.match(m.content or "")
            if not match:
                continue
            try:
                data = json.loads(match.group("body"))
            except Exception:
                continue
            sid = data.get("id")
            if not sid:
                continue
            if sid in self._scheduled and getattr(self._scheduled[sid], "console_message_id", None) == m.id:
                continue
            v = data.get("variant") or {}
            variant = Variant(
                style=v.get("style",""),
                title=v.get("title",""),
                description=v.get("description",""),
                footer=v.get("footer"),
                color=v.get("color", "#2ECC71"),
                mentions=v.get("mentions") or [],
                image_url=v.get("image_url"),
                cta=v.get("cta"),
            )
            obj = ScheduledAnnounce(
                id=sid,
                guild_id=int(data.get("guild_id")),
                channel_id=int(data.get("channel_id")),
                author_id=int(data.get("author_id")),
                variant=variant,
                run_at_iso=data.get("run_at_iso"),
                status=data.get("status","scheduled"),
                console_message_id=m.id,
            )
            self._scheduled[obj.id] = obj

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

        text = " ".join(variant.mentions) if variant.mentions else None
        embed = self._variant_to_embed(variant)
        try:
            await channel.send(content=text, embed=embed)
        except discord.HTTPException:
            return False

        sched.status = "published"
        try:
            console = await self._console_channel(guild)
            if console and sched.console_message_id:
                msg = await console.fetch_message(sched.console_message_id)
                payload = asdict(sched)
                payload["variant"] = asdict(sched.variant)
                await msg.edit(content=f"```{ANNOUNCE_DB_BLOCK}\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```")
        except Exception:
            pass
        return True

    # --------- commandes

    @app_commands.command(name="annonce", description="Rédiger une annonce avec l'IA (brouillon, variantes, publication/scheduling).")
    async def annonce_slash(self, interaction: discord.Interaction, salon: Optional[discord.TextChannel] = None):
        if not isinstance(interaction.user, discord.Member) or not self._is_staff(interaction.user):
            await interaction.response.send_message("❌ Commande réservée au staff.", ephemeral=True)
            return
        await interaction.response.send_modal(self._AnnounceModal(self, salon))

    @commands.hybrid_command(name="annonce", description="Rédiger une annonce avec l'IA (alias de /annonce).")
    async def annonce_prefix(self, ctx: commands.Context):
        if not isinstance(ctx.author, discord.Member) or not self._is_staff(ctx.author):
            await ctx.reply("❌ Commande réservée au staff.", mention_author=False)
            return
        if not hasattr(ctx, "interaction") or ctx.interaction is None:
            await ctx.reply("👉 Utilise **/annonce** (slash) pour l'UI complète.", mention_author=False)
            return
        await ctx.interaction.response.send_modal(self._AnnounceModal(self, self._find_channel(ctx.guild, ANNONCE_CHANNEL_NAME)))

    # --------- scheduler

    @tasks.loop(seconds=30.0)
    async def scheduler_loop(self):
        for guild in self.bot.guilds:
            await self._load_all_from_console(guild)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        to_publish: List[ScheduledAnnounce] = []
        for obj in list(self._scheduled.values()):
            if obj.status != "scheduled":
                continue
            try:
                run_at = datetime.fromisoformat(obj.run_at_iso)
            except Exception:
                continue
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            if run_at <= now:
                to_publish.append(obj)
        for obj in to_publish:
            await self._publish(obj)

    @scheduler_loop.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnounceAICog(bot))

