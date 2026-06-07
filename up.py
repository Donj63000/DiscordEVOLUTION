from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils.channel_resolver import resolve_text_channel
from utils.console_json_store import ConsoleJSONSnapshotStore

log = logging.getLogger(__name__)


def _parse_int_env(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


def _parse_float_env(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

    if minimum is not None and value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value


STAFF_ROLE_NAME = os.getenv(
    "UP_STAFF_ROLE_NAME",
    os.getenv("STAFF_ROLE_NAME", os.getenv("IASTAFF_ROLE", "Staff")),
)
VALID_MEMBER_ROLE_NAME = os.getenv("UP_VALID_MEMBER_ROLE_NAME", "Membre validé d'Evolution")
INVITE_ROLE_NAME = os.getenv("UP_INVITE_ROLE_NAME", "Invité")
VETERAN_ROLE_NAME = os.getenv("UP_VETERAN_ROLE_NAME", "Vétéran")
ANNONCE_CHANNEL_NAME = os.getenv("ANNONCE_CHANNEL_NAME", "annonces")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")

BOTUP_TAG = "===BOTUP==="
MESSAGE_THRESHOLD = _parse_int_env("UP_VETERAN_MESSAGE_THRESHOLD", 20, minimum=1)
JOINED_THRESHOLD_DAYS = _parse_int_env("UP_VETERAN_JOINED_THRESHOLD_DAYS", 6 * 30, minimum=1)
CANDIDATES_PER_PAGE = _parse_int_env("UP_VETERAN_CANDIDATES_PER_PAGE", 10, minimum=1, maximum=25)
VETERAN_VIEW_TIMEOUT_SECONDS = _parse_int_env(
    "UP_VETERAN_VIEW_TIMEOUT_SECONDS",
    15 * 60,
    minimum=60,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMOTIONS_FILE = os.path.join(BASE_DIR, "promotions_data.json")


@dataclass(frozen=True)
class PromotionOutcome:
    ok: bool
    message: str
    disable_button: bool = False
    announcement_sent: bool = False


class VeteranPromotionButton(discord.ui.Button):
    def __init__(
        self,
        cog: UpCog,
        *,
        member_id: int,
        index: int,
        member_display_name: str,
        message_count: int,
        join_days: int,
    ) -> None:
        label = self._build_label(index, member_display_name)
        super().__init__(label=label, style=discord.ButtonStyle.success, emoji="🏅")
        self.cog = cog
        self.member_id = member_id
        self.index = index
        self.message_count = message_count
        self.join_days = join_days

    @staticmethod
    def _build_label(index: int, member_display_name: str) -> str:
        label = f"Promouvoir {index:02} · {member_display_name}"
        if len(label) <= 80:
            return label
        return f"{label[:77]}..."

    async def callback(self, interaction: discord.Interaction) -> None:
        outcome = await self.cog.handle_veteran_promotion_interaction(
            interaction,
            member_id=self.member_id,
            candidate_snapshot={
                "message_count": self.message_count,
                "join_days": self.join_days,
            },
        )

        if outcome.disable_button:
            self.disabled = True
            self.style = discord.ButtonStyle.secondary
            self.emoji = "✅" if outcome.ok else "🚫"
            self.label = f"Promu {self.index:02}" if outcome.ok else f"Indisponible {self.index:02}"

            message = getattr(interaction, "message", None)
            if message is not None and self.view is not None:
                try:
                    await message.edit(view=self.view)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    log.debug("UpCog: impossible de mettre à jour le bouton Vétéran.", exc_info=True)


class VeteranPromotionView(discord.ui.View):
    def __init__(
        self,
        cog: UpCog,
        candidates: list[dict],
        *,
        start_index: int,
    ) -> None:
        super().__init__(timeout=VETERAN_VIEW_TIMEOUT_SECONDS)
        self.cog = cog
        self.message: discord.Message | None = None

        for offset, candidate in enumerate(candidates):
            member = candidate["member"]
            self.add_item(
                VeteranPromotionButton(
                    cog,
                    member_id=member.id,
                    index=start_index + offset,
                    member_display_name=getattr(member, "display_name", str(member.id)),
                    message_count=int(candidate["message_count"]),
                    join_days=int(candidate["join_days"]),
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        user = getattr(interaction, "user", None)

        if interaction.guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                "Cette action doit être utilisée depuis le serveur.",
                ephemeral=True,
            )
            return False

        if not self.cog._is_staff_member(user):
            await interaction.response.send_message(
                "Seuls les membres du Staff peuvent promouvoir un Vétéran.",
                ephemeral=True,
            )
            return False

        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.debug("UpCog: impossible de désactiver la vue Vétéran expirée.", exc_info=True)


class UpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_count = defaultdict(int)
        self.promotions_data: dict[str, dict] = {}
        self.initialized = False
        self.console_message_id: Optional[int] = None
        self._init_lock = asyncio.Lock()
        self._init_task: asyncio.Task | None = None
        self._promotion_locks: dict[int, asyncio.Lock] = {}
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=BOTUP_TAG,
            filename="promotions_data.json",
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="UP_CONSOLE_HISTORY_LIMIT",
        )

    async def cog_load(self) -> None:
        if self._init_task is None or self._init_task.done():
            self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self) -> None:
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()

    async def _post_ready_init(self) -> None:
        wait_until_ready = getattr(self.bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            await wait_until_ready()

        async with self._init_lock:
            if self.initialized:
                return

            log.debug("UpCog: initialisation des promotions Vétéran.")
            await self.load_promotions_data()

            legacy_votes_removed = self._cleanup_legacy_vote_state()
            self.initialized = True

            if legacy_votes_removed:
                await self._persist_state_safely()

            log.debug("UpCog: initialisation terminée (%s entrée(s)).", len(self.promotions_data))

    async def _ensure_initialized(self) -> None:
        if self.initialized:
            return

        task = self._init_task
        if task:
            try:
                await task
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("UpCog: la tâche d'initialisation a échoué: %s", exc, exc_info=True)

        if not self.initialized:
            await self._post_ready_init()

    async def load_promotions_data(self) -> None:
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)

        if isinstance(payload, dict):
            self.promotions_data = payload
            self.console_message_id = getattr(message, "id", None)
            log.info("UpCog: données chargées depuis #console (%s entrée(s)).", len(self.promotions_data))
            return

        if os.path.exists(PROMOTIONS_FILE):
            try:
                with open(PROMOTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    self.promotions_data = data
                    log.info("UpCog: données chargées depuis le fichier local.")
                    return
            except Exception as exc:
                log.warning("UpCog: impossible de charger promotions_data.json: %s", exc)

        self.promotions_data = {}
        log.info("UpCog: aucune donnée de promotion persistée trouvée.")

    def save_promotions_data_local(self) -> None:
        with open(PROMOTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.promotions_data, f, indent=4, ensure_ascii=False, sort_keys=True)

    async def dump_data_to_console(self) -> None:
        message = await self.store.save(
            self.promotions_data,
            current_message_id=self.console_message_id,
        )
        if message is not None:
            self.console_message_id = message.id

    async def _persist_state(self) -> None:
        self.save_promotions_data_local()
        await self.dump_data_to_console()

    async def _persist_state_safely(self) -> None:
        try:
            await self._persist_state()
        except Exception as exc:
            log.warning("UpCog: persistance des promotions impossible: %s", exc, exc_info=True)

    def _cleanup_legacy_vote_state(self) -> bool:
        changed = False

        for entry in self.promotions_data.values():
            if not isinstance(entry, dict):
                continue

            if "vote" in entry:
                entry.pop("vote", None)
                changed = True

            if entry.get("status") == "voting":
                entry["status"] = "postponed"
                changed = True

        if changed:
            log.info("UpCog: ancien état de vote Vétéran nettoyé sans message Staff.")

        return changed

    def _entry(self, user_id: int) -> dict:
        return self.promotions_data.setdefault(str(user_id), {})

    def get_promotion_status(self, user_id: int) -> str | None:
        entry = self.promotions_data.get(str(user_id), {})
        if not isinstance(entry, dict):
            return None
        status = entry.get("status")
        return status if isinstance(status, str) else None

    def set_promotion_status(self, user_id: int, status: str) -> None:
        self._entry(user_id)["status"] = status

    def _promotion_lock_for(self, user_id: int) -> asyncio.Lock:
        lock = self._promotion_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._promotion_locks[user_id] = lock
        return lock

    def _has_role(self, member: discord.Member, role_name: str) -> bool:
        return any(getattr(role, "name", None) == role_name for role in getattr(member, "roles", []) or [])

    def _is_staff_member(self, member: discord.Member) -> bool:
        permissions = getattr(member, "guild_permissions", None)
        if getattr(permissions, "administrator", False):
            return True
        return self._has_role(member, STAFF_ROLE_NAME)

    def _join_days(self, member: discord.Member) -> int:
        joined_at = getattr(member, "joined_at", None)
        if joined_at is None:
            return 0

        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)

        return max((discord.utils.utcnow() - joined_at).days, 0)

    def _eligible_veteran_record(
        self,
        member: discord.Member,
        *,
        message_count: int | None = None,
        join_days: int | None = None,
    ) -> Optional[dict]:
        if getattr(member, "bot", False):
            return None

        current_join_days = self._join_days(member)
        if join_days is not None:
            join_days = max(current_join_days, int(join_days))
        else:
            join_days = current_join_days

        if message_count is not None:
            msg_count = int(message_count)
        else:
            msg_count = self.user_message_count.get(str(member.id), 0)

        if join_days < JOINED_THRESHOLD_DAYS or msg_count < MESSAGE_THRESHOLD:
            return None
        if not self._has_role(member, VALID_MEMBER_ROLE_NAME):
            return None
        if self._has_role(member, INVITE_ROLE_NAME) or self._has_role(member, VETERAN_ROLE_NAME):
            return None

        return {
            "member": member,
            "join_days": join_days,
            "message_count": msg_count,
        }

    def _collect_veteran_candidates(self, guild: discord.Guild) -> list[dict]:
        candidates: list[dict] = []

        for member in getattr(guild, "members", []) or []:
            record = self._eligible_veteran_record(member)
            if record is not None:
                candidates.append(record)

        candidates.sort(
            key=lambda item: (
                -int(item["message_count"]),
                -int(item["join_days"]),
                getattr(item["member"], "display_name", "").casefold(),
            )
        )
        return candidates

    async def verifier_membres_eligibles(self) -> list[dict]:
        await self._ensure_initialized()
        all_candidates: list[dict] = []

        for guild in getattr(self.bot, "guilds", []) or []:
            all_candidates.extend(self._collect_veteran_candidates(guild))

        return all_candidates

    async def scan_entire_history(self, guild: discord.Guild | None = None) -> None:
        self.user_message_count.clear()

        scan_days = _parse_int_env("UP_SCAN_DAYS", 180, minimum=0)
        scan_limit = _parse_int_env("UP_SCAN_LIMIT_PER_CHANNEL", 5000, minimum=0)
        channel_delay = _parse_float_env("UP_SCAN_DELAY_SECONDS", 0.2, minimum=0.0)
        history_retries = _parse_int_env("UP_SCAN_RETRIES", 2, minimum=0)

        after = None
        if scan_days > 0:
            after = discord.utils.utcnow() - timedelta(days=scan_days)

        guilds = [guild] if guild is not None else list(getattr(self.bot, "guilds", []) or [])

        for target_guild in guilds:
            for channel in getattr(target_guild, "text_channels", []) or []:
                attempt = 0

                while True:
                    try:
                        async for msg in channel.history(
                            limit=scan_limit,
                            after=after,
                            oldest_first=False,
                        ):
                            author = getattr(msg, "author", None)
                            if author is not None and not getattr(author, "bot", False):
                                self.user_message_count[str(author.id)] += 1
                        break
                    except discord.Forbidden:
                        log.debug(
                            "UpCog: accès refusé à l'historique du canal %s.",
                            getattr(channel, "id", "unknown"),
                        )
                        break
                    except discord.HTTPException as exc:
                        if getattr(exc, "status", None) == 429 and attempt < history_retries:
                            retry_after = getattr(exc, "retry_after", None)
                            wait = float(retry_after) if retry_after else channel_delay
                            if wait > 0:
                                await asyncio.sleep(wait)
                            attempt += 1
                            continue

                        log.debug(
                            "UpCog: lecture d'historique impossible pour le canal %s: %s",
                            getattr(channel, "id", "unknown"),
                            exc,
                        )
                        break

                if channel_delay > 0:
                    await asyncio.sleep(channel_delay)

    def _candidate_line(self, index: int, candidate: dict) -> str:
        member = candidate["member"]
        return (
            f"`{index:02}` {member.mention} — "
            f"{candidate['message_count']} message(s), "
            f"{candidate['join_days']} jour(s) d'ancienneté"
        )

    def _build_candidates_embed(
        self,
        candidates: list[dict],
        *,
        start_index: int,
        page_index: int,
        total_pages: int,
        total_candidates: int,
    ) -> discord.Embed:
        lines = [
            self._candidate_line(start_index + offset, candidate)
            for offset, candidate in enumerate(candidates)
        ]

        title = "Candidats Vétéran"
        if total_pages > 1:
            title = f"{title} ({page_index}/{total_pages})"

        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(
            text=(
                f"{total_candidates} candidat(s) éligible(s) — "
                f"seuils: {JOINED_THRESHOLD_DAYS} jours d'ancienneté, "
                f"{MESSAGE_THRESHOLD} messages."
            )
        )
        return embed

    def _chunk_candidates(self, candidates: list[dict]) -> list[list[dict]]:
        return [
            candidates[index : index + CANDIDATES_PER_PAGE]
            for index in range(0, len(candidates), CANDIDATES_PER_PAGE)
        ]

    @commands.guild_only()
    @commands.has_role(STAFF_ROLE_NAME)
    @commands.command(name="veteran", aliases=["vétéran"])
    async def veteran_command(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send("Commande disponible uniquement sur le serveur.")
            return

        async with ctx.typing():
            await self._ensure_initialized()
            await self.scan_entire_history(ctx.guild)
            candidates = self._collect_veteran_candidates(ctx.guild)

        if not candidates:
            embed = discord.Embed(
                title="Candidats Vétéran",
                description="Aucun membre ne remplit les critères de promotion pour le moment.",
                color=discord.Color.blue(),
            )
            embed.set_footer(
                text=(
                    f"Seuils: {JOINED_THRESHOLD_DAYS} jours d'ancienneté, "
                    f"{MESSAGE_THRESHOLD} messages."
                )
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            return

        chunks = self._chunk_candidates(candidates)
        total_pages = len(chunks)
        total_candidates = len(candidates)

        for page_index, chunk in enumerate(chunks, start=1):
            start_index = ((page_index - 1) * CANDIDATES_PER_PAGE) + 1
            embed = self._build_candidates_embed(
                chunk,
                start_index=start_index,
                page_index=page_index,
                total_pages=total_pages,
                total_candidates=total_candidates,
            )
            view = VeteranPromotionView(self, chunk, start_index=start_index)
            message = await ctx.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            view.message = message

    async def _get_member(
        self,
        guild: discord.Guild,
        member_id: int,
        *,
        prefer_fetch: bool = False,
    ) -> discord.Member | None:
        fetch_member = getattr(guild, "fetch_member", None)
        if prefer_fetch and callable(fetch_member):
            try:
                return await fetch_member(member_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.debug("UpCog: membre %s introuvable ou inaccessible via fetch.", member_id, exc_info=True)

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            member = get_member(member_id)
            if member is not None:
                return member

        if callable(fetch_member):
            try:
                return await fetch_member(member_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                log.debug("UpCog: membre %s introuvable ou inaccessible.", member_id, exc_info=True)

        return None

    def _resolve_announcement_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        return resolve_text_channel(
            guild,
            id_env="ANNONCE_CHANNEL_ID",
            name_env="ANNONCE_CHANNEL_NAME",
            default_name=ANNONCE_CHANNEL_NAME,
        )

    def _bot_member(self, guild: discord.Guild) -> discord.Member | None:
        me = getattr(guild, "me", None)
        if me is not None:
            return me

        bot_user = getattr(self.bot, "user", None)
        if bot_user is None:
            return None

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            return get_member(bot_user.id)

        return None

    def _can_bot_assign_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        me = self._bot_member(guild)
        if me is None:
            return True

        permissions = getattr(me, "guild_permissions", None)
        if not getattr(permissions, "manage_roles", False):
            return False

        top_role = getattr(me, "top_role", None)
        if top_role is None:
            return True

        try:
            return role < top_role
        except TypeError:
            return True

    async def handle_veteran_promotion_interaction(
        self,
        interaction: discord.Interaction,
        *,
        member_id: int,
        candidate_snapshot: dict | None = None,
    ) -> PromotionOutcome:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        promoted_by = interaction.user

        if guild is None or not isinstance(promoted_by, discord.Member):
            outcome = PromotionOutcome(
                ok=False,
                message="Promotion impossible hors serveur.",
                disable_button=False,
            )
            await interaction.followup.send(outcome.message, ephemeral=True)
            return outcome

        await self._ensure_initialized()
        lock = self._promotion_lock_for(member_id)

        async with lock:
            member = await self._get_member(guild, member_id, prefer_fetch=True)
            if member is None:
                outcome = PromotionOutcome(
                    ok=False,
                    message="Ce membre est introuvable sur le serveur.",
                    disable_button=True,
                )
                await interaction.followup.send(outcome.message, ephemeral=True)
                return outcome

            outcome = await self.promouvoir_veteran(
                member,
                promoted_by,
                candidate_snapshot=candidate_snapshot,
            )
            await interaction.followup.send(outcome.message, ephemeral=True)
            return outcome

    async def promouvoir_veteran(
        self,
        member: discord.Member,
        promoted_by: discord.Member,
        *,
        candidate_snapshot: dict | None = None,
    ) -> PromotionOutcome:
        guild = member.guild
        member_id = member.id

        if self._has_role(member, VETERAN_ROLE_NAME):
            return PromotionOutcome(
                ok=True,
                message=f"{member.display_name} possède déjà le rôle {VETERAN_ROLE_NAME}.",
                disable_button=True,
                announcement_sent=False,
            )

        candidate = self._eligible_veteran_record(
            member,
            message_count=(
                int(candidate_snapshot["message_count"])
                if candidate_snapshot and "message_count" in candidate_snapshot
                else None
            ),
            join_days=(
                int(candidate_snapshot["join_days"])
                if candidate_snapshot and "join_days" in candidate_snapshot
                else None
            ),
        )
        if candidate is None:
            return PromotionOutcome(
                ok=False,
                message=(
                    f"{member.display_name} n'est plus éligible à la promotion Vétéran "
                    "au moment du clic."
                ),
                disable_button=True,
            )

        veteran_role = discord.utils.get(getattr(guild, "roles", []) or [], name=VETERAN_ROLE_NAME)
        if veteran_role is None:
            return PromotionOutcome(
                ok=False,
                message=f"Rôle `{VETERAN_ROLE_NAME}` introuvable. Promotion annulée.",
                disable_button=False,
            )

        announcement_channel = self._resolve_announcement_channel(guild)
        if announcement_channel is None:
            return PromotionOutcome(
                ok=False,
                message=(
                    "Canal d'annonces introuvable. Configure `ANNONCE_CHANNEL_ID` "
                    "ou `ANNONCE_CHANNEL_NAME` avant de promouvoir."
                ),
                disable_button=False,
            )

        if not self._can_bot_assign_role(guild, veteran_role):
            return PromotionOutcome(
                ok=False,
                message=(
                    f"Le bot ne peut pas attribuer le rôle `{VETERAN_ROLE_NAME}`. "
                    "Vérifie la permission `Gérer les rôles` et la position du rôle du bot."
                ),
                disable_button=False,
            )

        try:
            await member.add_roles(
                veteran_role,
                reason=(
                    f"Promotion Vétéran via !veteran par "
                    f"{promoted_by} ({promoted_by.id})"
                ),
            )
        except discord.Forbidden:
            return PromotionOutcome(
                ok=False,
                message=(
                    f"Permissions insuffisantes pour promouvoir {member.display_name}. "
                    "Vérifie la hiérarchie des rôles Discord."
                ),
                disable_button=False,
            )
        except discord.HTTPException as exc:
            return PromotionOutcome(
                ok=False,
                message=f"Erreur Discord pendant la promotion de {member.display_name}: {exc}",
                disable_button=False,
            )

        entry = self._entry(member_id)
        entry.update(
            {
                "status": "promoted",
                "promoted_at": discord.utils.utcnow().isoformat(),
                "promoted_by": promoted_by.id,
                "message_count_at_promotion": candidate["message_count"],
                "join_days_at_promotion": candidate["join_days"],
            }
        )
        await self._persist_state_safely()

        announcement_sent = await self._send_veteran_announcement(
            announcement_channel,
            member,
            promoted_by,
        )

        if announcement_sent:
            return PromotionOutcome(
                ok=True,
                message=(
                    f"{member.display_name} a été promu(e) {VETERAN_ROLE_NAME}. "
                    f"Annonce envoyée dans #{announcement_channel.name}."
                ),
                disable_button=True,
                announcement_sent=True,
            )

        return PromotionOutcome(
            ok=True,
            message=(
                f"{member.display_name} a été promu(e) {VETERAN_ROLE_NAME}, "
                "mais l'annonce n'a pas pu être envoyée. Vérifie les permissions du canal #annonces."
            ),
            disable_button=True,
            announcement_sent=False,
        )

    async def _send_veteran_announcement(
        self,
        channel: discord.TextChannel,
        member: discord.Member,
        promoted_by: discord.Member,
    ) -> bool:
        embed = discord.Embed(
            title="🏅 Nouveau Vétéran",
            description=(
                f"{member.mention} a été promu(e) au rang **{VETERAN_ROLE_NAME}** "
                f"par {promoted_by.mention}."
            ),
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )

        avatar = getattr(member, "display_avatar", None)
        avatar_url = getattr(avatar, "url", None)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        allowed_mentions = discord.AllowedMentions(users=True, roles=False, everyone=False)

        try:
            await channel.send(embed=embed, allowed_mentions=allowed_mentions)
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "UpCog: annonce embed de promotion Vétéran impossible pour %s: %s",
                member.id,
                exc,
                exc_info=True,
            )

        plain_message = (
            f"🏅 {member.mention} a été promu(e) au rang **{VETERAN_ROLE_NAME}** "
            f"par {promoted_by.mention}."
        )
        try:
            await channel.send(plain_message, allowed_mentions=allowed_mentions)
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "UpCog: annonce texte de promotion Vétéran impossible pour %s: %s",
                member.id,
                exc,
                exc_info=True,
            )
            return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UpCog(bot))
