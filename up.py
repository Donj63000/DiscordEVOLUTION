import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks

from utils.channel_resolver import resolve_text_channel
from utils.console_json_store import ConsoleJSONSnapshotStore

CHECK_INTERVAL_HOURS = 168  # 1 semaine
VOTE_DURATION_SECONDS = 300  # 5 minutes
STAFF_ROLE_NAME = "Staff"
VALID_MEMBER_ROLE_NAME = "Membre validé d'Evolution"
INVITE_ROLE_NAME = "Invité"
VETERAN_ROLE_NAME = "Vétéran"
STAFF_CHANNEL_NAME = os.getenv("STAFF_CHANNEL_NAME", "📚 Général-staff 📚")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
BOTUP_TAG = "===BOTUP==="
MESSAGE_THRESHOLD = 20
JOINED_THRESHOLD_DAYS = 6 * 30
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMOTIONS_FILE = os.path.join(BASE_DIR, "promotions_data.json")

log = logging.getLogger(__name__)


class UpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.user_message_count = defaultdict(int)
        self.promotions_data: dict[str, dict] = {}
        self.initialized = False
        self.console_message_id: Optional[int] = None
        self._init_lock = asyncio.Lock()
        self._init_task: asyncio.Task | None = None
        self._vote_tasks: dict[int, asyncio.Task] = {}
        self.store = ConsoleJSONSnapshotStore(
            bot,
            marker=BOTUP_TAG,
            filename="promotions_data.json",
            default_channel_name=CONSOLE_CHANNEL_NAME,
            history_limit_env="UP_CONSOLE_HISTORY_LIMIT",
        )

    async def cog_load(self):
        if self._init_task is None or self._init_task.done():
            self._init_task = asyncio.create_task(self._post_ready_init())

    def cog_unload(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
        for task in self._vote_tasks.values():
            if not task.done():
                task.cancel()
        self._vote_tasks.clear()
        if self.check_up_status.is_running():
            self.check_up_status.cancel()

    async def _post_ready_init(self):
        wait_until_ready = getattr(self.bot, "wait_until_ready", None)
        if callable(wait_until_ready):
            await wait_until_ready()
        async with self._init_lock:
            if self.initialized:
                return
            log.debug("UpCog: init start")
            await self.load_promotions_data()
            self.initialized = True
            await self._resume_pending_votes()
            log.debug("UpCog: init complete (entries=%s)", len(self.promotions_data))
        if not self.check_up_status.is_running():
            self.check_up_status.start()

    async def _ensure_initialized(self):
        if self.initialized:
            return
        task = self._init_task
        if task:
            try:
                await task
            except Exception as exc:
                log.warning("UpCog: init task failed: %s", exc, exc_info=True)
        if not self.initialized:
            await self._post_ready_init()

    async def load_promotions_data(self):
        message, payload = await self.store.load_latest(current_message_id=self.console_message_id)
        if isinstance(payload, dict):
            self.promotions_data = payload
            self.console_message_id = getattr(message, "id", None)
            log.info("UpCog: data loaded from console (%s entries).", len(self.promotions_data))
            return
        if os.path.exists(PROMOTIONS_FILE):
            try:
                with open(PROMOTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.promotions_data = data
                    log.info("UpCog: data loaded from local file.")
                    return
            except Exception as exc:
                log.warning("UpCog: failed to load local promotions file: %s", exc)
        self.promotions_data = {}
        log.info("UpCog: no persisted promotion data found.")

    def save_promotions_data_local(self):
        with open(PROMOTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.promotions_data, f, indent=4, ensure_ascii=False, sort_keys=True)

    async def dump_data_to_console(self):
        message = await self.store.save(self.promotions_data, current_message_id=self.console_message_id)
        if message is not None:
            self.console_message_id = message.id

    async def _persist_state(self):
        self.save_promotions_data_local()
        await self.dump_data_to_console()

    def _entry(self, user_id: int) -> dict:
        return self.promotions_data.setdefault(str(user_id), {})

    def get_promotion_status(self, user_id: int):
        return self.promotions_data.get(str(user_id), {}).get("status")

    def set_promotion_status(self, user_id: int, status: str, *, clear_vote: bool = False):
        entry = self._entry(user_id)
        entry["status"] = status
        if clear_vote:
            entry.pop("vote", None)

    def get_vote_info(self, user_id: int) -> Optional[dict]:
        return self.promotions_data.get(str(user_id), {}).get("vote")

    def set_vote_info(self, user_id: int, vote_info: Optional[dict]):
        entry = self._entry(user_id)
        if vote_info is None:
            entry.pop("vote", None)
        else:
            entry["vote"] = vote_info

    def _find_member(self, guild_id: int, user_id: int) -> Optional[discord.Member]:
        guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
        if guild is None:
            for candidate in getattr(self.bot, "guilds", []) or []:
                if getattr(candidate, "id", None) == guild_id:
                    guild = candidate
                    break
        if guild is None:
            return None
        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            return get_member(user_id)
        for member in getattr(guild, "members", []) or []:
            if getattr(member, "id", None) == user_id:
                return member
        return None

    async def _resume_pending_votes(self):
        now_ts = int(discord.utils.utcnow().timestamp())
        changed = False
        for user_id_str, entry in list(self.promotions_data.items()):
            if entry.get("status") != "voting":
                continue
            vote = entry.get("vote") or {}
            try:
                user_id = int(user_id_str)
            except (TypeError, ValueError):
                continue
            ends_at_ts = int(vote.get("ends_at_ts", 0) or 0)
            if ends_at_ts <= 0:
                self.set_promotion_status(user_id, "postponed", clear_vote=True)
                changed = True
                continue
            if ends_at_ts <= now_ts:
                await self._finalize_vote(user_id)
            else:
                self._schedule_vote_finalization(user_id)
        if changed:
            await self._persist_state()

    def _schedule_vote_finalization(self, user_id: int):
        existing = self._vote_tasks.get(user_id)
        if existing and not existing.done():
            return
        self._vote_tasks[user_id] = asyncio.create_task(self._wait_and_finalize_vote(user_id))

    async def _wait_and_finalize_vote(self, user_id: int):
        try:
            vote = self.get_vote_info(user_id) or {}
            ends_at_ts = int(vote.get("ends_at_ts", 0) or 0)
            if ends_at_ts <= 0:
                self.set_promotion_status(user_id, "postponed", clear_vote=True)
                await self._persist_state()
                return
            delay = max(0.0, ends_at_ts - discord.utils.utcnow().timestamp())
            if delay > 0:
                await asyncio.sleep(delay)
            await self._finalize_vote(user_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("UpCog: vote finalization failed for %s: %s", user_id, exc, exc_info=True)
        finally:
            self._vote_tasks.pop(user_id, None)

    async def _finalize_vote(self, user_id: int):
        entry = self.promotions_data.get(str(user_id), {})
        if entry.get("status") != "voting":
            return
        vote = entry.get("vote") or {}
        guild_id = int(vote.get("guild_id", 0) or 0)
        channel_id = int(vote.get("staff_channel_id", 0) or 0)
        message_id = int(vote.get("message_id", 0) or 0)
        guild = self.bot.get_guild(guild_id) if hasattr(self.bot, "get_guild") else None
        if guild is None:
            for candidate in getattr(self.bot, "guilds", []) or []:
                if getattr(candidate, "id", None) == guild_id:
                    guild = candidate
                    break
        if guild is None:
            self.set_promotion_status(user_id, "postponed", clear_vote=True)
            await self._persist_state()
            return
        staff_channel = guild.get_channel(channel_id) if hasattr(guild, "get_channel") else None
        member = self._find_member(guild_id, user_id)
        if not isinstance(staff_channel, discord.TextChannel) or member is None:
            self.set_promotion_status(user_id, "postponed", clear_vote=True)
            await self._persist_state()
            return
        try:
            vote_message = await staff_channel.fetch_message(message_id)
        except discord.NotFound:
            await staff_channel.send(f"Le message de vote pour {member.mention} a disparu, vote reporté.")
            self.set_promotion_status(user_id, "postponed", clear_vote=True)
            await self._persist_state()
            return
        except discord.HTTPException as exc:
            log.warning("UpCog: unable to fetch vote message %s: %s", message_id, exc)
            self.set_promotion_status(user_id, "postponed", clear_vote=True)
            await self._persist_state()
            return

        yes_count = 0
        no_count = 0
        for reaction in getattr(vote_message, "reactions", []) or []:
            if str(reaction.emoji) == "✅":
                yes_count = max(reaction.count - 1, 0)
            elif str(reaction.emoji) == "❌":
                no_count = max(reaction.count - 1, 0)

        total_votes = yes_count + no_count
        if total_votes == 0:
            await staff_channel.send(
                f"Aucun vote exprimé pour {member.mention}, proposition reportée à la semaine prochaine."
            )
            self.set_promotion_status(user_id, "postponed", clear_vote=True)
            await self._persist_state()
            return
        if no_count >= 1:
            await staff_channel.send(
                f"Promotion refusée pour {member.mention}. (Un ❌ suffit à annuler la promotion)"
            )
            self.set_promotion_status(user_id, "refused", clear_vote=True)
            await self._persist_state()
            return
        await self.promouvoir_veteran(staff_channel, member)

    @tasks.loop(hours=CHECK_INTERVAL_HOURS)
    async def check_up_status(self):
        await self._ensure_initialized()
        if not self.initialized:
            return
        await self.scan_entire_history()
        await self.verifier_membres_eligibles()
        await self._persist_state()

    async def scan_entire_history(self):
        self.user_message_count.clear()
        try:
            scan_days = int(os.getenv("UP_SCAN_DAYS", "180"))
        except ValueError:
            scan_days = 180
        try:
            scan_limit = int(os.getenv("UP_SCAN_LIMIT_PER_CHANNEL", "5000"))
        except ValueError:
            scan_limit = 5000
        if scan_limit < 0:
            scan_limit = 0
        after = None
        if scan_days > 0:
            after = discord.utils.utcnow() - timedelta(days=scan_days)
        channel_delay = max(float(os.getenv("UP_SCAN_DELAY_SECONDS", "0.2")), 0.0)
        history_retries = max(int(os.getenv("UP_SCAN_RETRIES", "2")), 0)
        for guild in getattr(self.bot, "guilds", []) or []:
            for channel in getattr(guild, "text_channels", []) or []:
                attempt = 0
                while True:
                    try:
                        async for msg in channel.history(limit=scan_limit, after=after, oldest_first=False):
                            if not msg.author.bot:
                                self.user_message_count[str(msg.author.id)] += 1
                        break
                    except discord.HTTPException as exc:
                        if getattr(exc, "status", None) == 429 and attempt < history_retries:
                            retry_after = getattr(exc, "retry_after", None)
                            wait = float(retry_after) if retry_after else channel_delay
                            if wait > 0:
                                await asyncio.sleep(wait)
                            attempt += 1
                            continue
                        break
                    except discord.Forbidden:
                        break
                if channel_delay > 0:
                    await asyncio.sleep(channel_delay)

    async def verifier_membres_eligibles(self):
        await self._ensure_initialized()
        for guild in getattr(self.bot, "guilds", []) or []:
            staff_channel = resolve_text_channel(
                guild,
                id_env="STAFF_CHANNEL_ID",
                name_env="STAFF_CHANNEL_NAME",
                default_name=STAFF_CHANNEL_NAME,
            )
            if not staff_channel:
                continue
            for member in getattr(guild, "members", []) or []:
                if getattr(member, "bot", False):
                    continue
                join_days = 0
                if getattr(member, "joined_at", None):
                    join_days = (discord.utils.utcnow() - member.joined_at).days
                has_valid_role = any(getattr(r, "name", None) == VALID_MEMBER_ROLE_NAME for r in member.roles)
                has_invite_role = any(getattr(r, "name", None) == INVITE_ROLE_NAME for r in member.roles)
                has_veteran_role = any(getattr(r, "name", None) == VETERAN_ROLE_NAME for r in member.roles)
                msg_count = self.user_message_count.get(str(member.id), 0)
                status = self.get_promotion_status(member.id)
                if status in ["promoted", "refused", "voting"]:
                    continue
                if status not in ["postponed", None]:
                    continue
                if (
                    join_days >= JOINED_THRESHOLD_DAYS
                    and has_valid_role
                    and not has_invite_role
                    and msg_count >= MESSAGE_THRESHOLD
                    and not has_veteran_role
                ):
                    await self.lancer_vote(staff_channel, member)
        await self._persist_state()

    async def lancer_vote(self, staff_channel: discord.TextChannel, member: discord.Member):
        if self.get_promotion_status(member.id) == "voting":
            return
        mention_staff_role = discord.utils.get(member.guild.roles, name=STAFF_ROLE_NAME)
        mention_text = mention_staff_role.mention if mention_staff_role else "@Staff"
        embed = discord.Embed(
            title="Vote Promotion",
            description=(
                f"{mention_text} — Promotion de {member.mention} en **{VETERAN_ROLE_NAME}** ?\n"
                f"Réagissez ✅ ou ❌ (durée: {VOTE_DURATION_SECONDS // 60} min)."
            ),
            color=discord.Color.blue(),
        )
        vote_message = await staff_channel.send(embed=embed)
        await vote_message.add_reaction("✅")
        await vote_message.add_reaction("❌")

        now_ts = int(discord.utils.utcnow().timestamp())
        vote_info = {
            "guild_id": member.guild.id,
            "staff_channel_id": staff_channel.id,
            "message_id": vote_message.id,
            "started_at_ts": now_ts,
            "ends_at_ts": now_ts + VOTE_DURATION_SECONDS,
        }
        self.set_promotion_status(member.id, "voting")
        self.set_vote_info(member.id, vote_info)
        await self._persist_state()
        self._schedule_vote_finalization(member.id)

    async def promouvoir_veteran(self, staff_channel: discord.TextChannel, member: discord.Member):
        veteran_role = discord.utils.get(member.guild.roles, name=VETERAN_ROLE_NAME)
        if not veteran_role:
            await staff_channel.send("Rôle 'Vétéran' introuvable, impossible de promouvoir.")
            self.set_promotion_status(member.id, "refused", clear_vote=True)
            await self._persist_state()
            return
        try:
            await member.add_roles(veteran_role)
            await staff_channel.send(f"{member.mention} promu(e) **{VETERAN_ROLE_NAME}**.")
            self.set_promotion_status(member.id, "promoted", clear_vote=True)
            await self._persist_state()
        except discord.Forbidden:
            await staff_channel.send(f"Permissions insuffisantes pour promouvoir {member.display_name}.")
            self.set_promotion_status(member.id, "refused", clear_vote=True)
            await self._persist_state()
        except discord.HTTPException as exc:
            await staff_channel.send(f"Erreur promotion {member.display_name} : {exc}")
            self.set_promotion_status(member.id, "refused", clear_vote=True)
            await self._persist_state()


async def setup(bot: commands.Bot):
    await bot.add_cog(UpCog(bot))
