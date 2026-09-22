#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import uuid
import asyncio
import logging
import discord
from discord.ext import commands
from dotenv import load_dotenv
from alive import keep_alive, set_ready
from utils.discord_startup import run_with_shutdown
from utils.runtime_memory import log_memory, start_memory_monitor
from collections import deque
from utils.discord_history import fetch_channel_history
from utils.slash_support import EvolutionCommandTree
from utils.command_policy import ai_service_enabled, enabled_flag
from utils.slash_sync import sync_application_commands, cleanup_retired_guild_commands
from utils.bot_branding import sync_bot_branding
from utils.evo_config import EvoError, resolve_console_channel

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("main")

LOCK_TAG = "===BOTLOCK==="
EVO_HANDOVER_SECONDS = 125
STAFF_ROLE_NAME = os.getenv("IASTAFF_ROLE", os.getenv("STAFF_ROLE_NAME", "Staff"))


class EvoLeadershipLost(EvoError):
    """Une autre instance a publié un verrou après le mien."""


def env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def env_csv(name: str) -> list[str]:
    return [item.strip() for item in (os.getenv(name) or "").split(",") if item.strip()]


class EvoBot(commands.Bot):
    def __init__(self):
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_TOKEN manquant")

        intents = discord.Intents.default()
        intents.message_content = env_bool("ENABLE_MESSAGE_CONTENT_INTENT", True)
        intents.members = env_bool("ENABLE_MEMBERS_INTENT", True)
        intents.presences = env_bool("ENABLE_PRESENCE_INTENT", False)

        super().__init__(
            command_prefix=os.getenv("BOT_PREFIX", "!"),
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
            tree_cls=EvolutionCommandTree,
        )
        self.token = token
        self.INSTANCE_ID = os.getenv("RENDER_INSTANCE_ID") or os.getenv("INSTANCE_ID") or uuid.uuid4().hex
        self._singleton_ready = False
        self._startup_setup_started = False
        self._lock_channel_id = None
        self._lock_message_id = None
        self._seen_ids = set()
        self._seen_order = deque()
        self._seen_max = 2048
        self._console_checked = False
        self._branding_attempted = False
        self._evo_connected = False
        self._evo_resume_at = 0.0
        self._evo_leadership_known = False
        self._evo_check_lock = asyncio.Lock()
        self._evo_scan_lock = asyncio.Lock()
        self._lock_scan_message_id = None

        orig = self.process_commands

        async def _once_per_message(message):
            mid = getattr(message, "id", None)
            if mid is not None and mid in self._seen_ids:
                return
            if mid is not None:
                self._seen_ids.add(mid)
                self._seen_order.append(mid)
                if len(self._seen_order) > self._seen_max:
                    old = self._seen_order.popleft()
                    self._seen_ids.discard(old)
            return await orig(message)

        self.process_commands = _once_per_message

    async def _safe_load(self, ext_name: str) -> bool:
        try:
            await self.load_extension(ext_name)
            logging.info("Extension chargée: %s", ext_name)
            return True
        except Exception as e:
            logging.warning("Impossible de charger %s: %s", ext_name, e, exc_info=True)
            return False
        finally:
            log_memory(f"extension:{ext_name}")

    async def _load_iastaff_anywhere(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))

        tried = []

        # 1) Essai direct à la racine
        if await self._safe_load("iastaff"):
            return True
        tried.append("iastaff")

        # 2) Dossier DiscordEVOLUTION/
        de_dir = os.path.join(base_dir, "DiscordEVOLUTION")
        if os.path.isfile(os.path.join(de_dir, "iastaff.py")):
            if de_dir not in sys.path:
                sys.path.insert(0, de_dir)
            if await self._safe_load("iastaff"):
                return True
            tried.append(f"{de_dir} + iastaff")

        # 3) Dossier cogs/
        cogs_dir = os.path.join(base_dir, "cogs")
        if os.path.isfile(os.path.join(cogs_dir, "iastaff.py")):
            if cogs_dir not in sys.path:
                sys.path.insert(0, cogs_dir)
            if await self._safe_load("iastaff"):
                return True
            tried.append(f"{cogs_dir} + iastaff")

        # 4) Noms package si les dossiers sont des packages (avec __init__.py)
        for name in ("DiscordEVOLUTION.iastaff", "cogs.iastaff"):
            if await self._safe_load(name):
                return True
            tried.append(name)

        logging.error(
            "Extension iastaff introuvable. Emplacements testés: %s. "
            "Place iastaff.py à la racine (recommandé) ou ajoute __init__.py au dossier et charge via <dossier>.iastaff.",
            ", ".join(tried),
        )
        return False

    async def setup_hook(self):
        self._startup_setup_started = True
        self.remove_command("help")

        required_exts = [
            "job",
            "activite",
            "ticket",
            "players",
            "sondage",
            "stats",
            "help",
            "welcome",
            "member_guard",
            "enquete",
            "calcul",
            "dofus_wiki",
            "exo",
            "perco",
            "avis",
            "organisation",
            "event_conversation",
        ]

        optional_exts = [
            "music",
            "defender",
            "moderation",
            "up",
            "entree",
            "cogs.profil",
            "cogs.annonce_ai",
        ]

        failed_required: list[str] = []

        for ext in required_exts:
            if not await self._safe_load(ext):
                failed_required.append(ext)

        if ai_service_enabled("gemini"):
            await self._safe_load("ia")
        for ext in optional_exts:
            await self._safe_load(ext)

        if ai_service_enabled("staff"):
            await self._load_iastaff_anywhere()

        # Charger les commandes natives Evo avant l'adaptateur des anciennes commandes.
        if enabled_flag("BUILD_ENABLED", True):
            if not await self._safe_load("build"):
                failed_required.append("build")
        else:
            log.debug("Build : chargement désactivé par BUILD_ENABLED.")

        if enabled_flag("EVO_ENABLED"):
            await self._safe_load("evo")

        if not await self._safe_load("slash_commands"):
            failed_required.append("slash_commands")

        if failed_required:
            logging.error("Extensions critiques non chargées: %s", ", ".join(failed_required))
            raise RuntimeError(f"Extensions critiques non chargées: {', '.join(failed_required)}")

        await self._sync_app_commands()

        cmds = [c.qualified_name for c in self.commands]
        logging.info("Commandes prefix enregistrées: %s", cmds)

    async def _sync_app_commands(self) -> None:
        await sync_application_commands(self)

    async def wait_console_channel(self, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            for g in self.guilds:
                if enabled_flag("EVO_ENABLED") and self._evo_guild_id() != g.id:
                    continue
                ch = resolve_console_channel(g)
                if ch:
                    return ch
            await asyncio.sleep(1)
        return None

    async def ensure_console_channel(self) -> None:
        default_name = os.getenv("CHANNEL_CONSOLE") or os.getenv("CONSOLE_CHANNEL_NAME") or "console"
        console_id = os.getenv("CHANNEL_CONSOLE_ID")
        auto_create = (os.getenv("CONSOLE_AUTO_CREATE", "1") or "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        for guild in list(getattr(self, "guilds", []) or []):
            ch = resolve_console_channel(guild)
            if ch:
                continue
            if console_id:
                logging.warning(
                    "Console channel id %s not found in guild %s.",
                    console_id,
                    getattr(guild, "name", guild.id),
                )
                continue
            if not auto_create:
                logging.warning(
                    "Console channel missing in guild %s. Set CHANNEL_CONSOLE_ID or create #%s.",
                    getattr(guild, "name", guild.id),
                    default_name,
                )
                continue
            me = guild.me or (guild.get_member(self.user.id) if self.user else None)
            if not me or not getattr(me.guild_permissions, "manage_channels", False):
                logging.warning(
                    "Console channel missing in guild %s; bot lacks manage_channels.",
                    getattr(guild, "name", guild.id),
                )
                continue
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    read_messages=False,
                    send_messages=False,
                    read_message_history=False,
                ),
                me: discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    attach_files=True,
                    manage_messages=True,
                    read_message_history=True,
                ),
            }
            staff_role = discord.utils.find(
                lambda role: getattr(role, "name", None) == STAFF_ROLE_NAME,
                getattr(guild, "roles", []) or [],
            )
            if staff_role is not None:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    read_messages=True,
                    send_messages=True,
                    read_message_history=True,
                )
            try:
                created = await guild.create_text_channel(
                    default_name,
                    overwrites=overwrites,
                    reason="Console persistence channel",
                )
                logging.info(
                    "Console channel created in guild %s: #%s",
                    getattr(guild, "name", guild.id),
                    created.name,
                )
            except discord.Forbidden:
                logging.warning(
                    "Console channel missing in guild %s; permission denied to create.",
                    getattr(guild, "name", guild.id),
                )
            except discord.HTTPException as exc:
                logging.warning(
                    "Console channel creation failed in guild %s: %s",
                    getattr(guild, "name", guild.id),
                    exc,
                )

    async def _fetch_history(self, channel, limit=50, oldest_first=False, raise_errors=False):
        return await fetch_channel_history(
            channel,
            limit=limit,
            oldest_first=oldest_first,
            reason="main.lock",
            raise_errors=raise_errors,
        )

    async def parse_latest_lock(self, ch: discord.TextChannel, *, strict=False):
        if strict:
            async for message in ch.history(limit=None):
                if message.author == self.user and message.content.startswith(LOCK_TAG):
                    return self._lock_fields(message)
            return None, None, None
        messages = await self._fetch_history(ch, limit=50, oldest_first=False)
        for msg in messages:
            if msg.author == self.user and msg.content.startswith(LOCK_TAG):
                parts = msg.content.split()
                if len(parts) >= 3:
                    inst = parts[1]
                    try:
                        ts = int(parts[2])
                    except Exception:
                        ts = 0
                    return msg, inst, ts
        return None, None, None

    def _lock_fields(self, message):
        parts = message.content.split()
        if (len(parts) != 3 or parts[0] != LOCK_TAG
                or not parts[2].isascii() or not parts[2].isdigit() or int(parts[2]) <= 0):
            raise EvoError("Le verrou de l'instance est illisible. Evo reste suspendu.")
        return message, parts[1], int(parts[2])

    def _evo_guild_id(self):
        cog = self.get_cog("EvoCog")
        config = getattr(cog, "config", None)
        if config is not None:
            return config.guild_id
        raw = os.getenv("EVO_GUILD_ID", "").strip()
        if not raw:
            guilds = list(getattr(self, "guilds", []) or [])
            return guilds[0].id if len(guilds) == 1 else None
        return int(raw) if raw.isascii() and raw.isdigit() else None

    async def _suspend_evo(self, reason, *, cooldown=True):
        self._evo_leadership_known = False
        if cooldown:
            self._evo_resume_at = max(self._evo_resume_at, time.monotonic() + EVO_HANDOVER_SECONDS)
        logging.debug("Evo leadership suspended reason=%s", reason)
        cog = self.get_cog("EvoCog")
        if cog is not None:
            await cog.suspend()

    async def _verified_lock(self, channel):
        async with self._evo_scan_lock:
            try:
                own = await channel.fetch_message(self._lock_message_id)
            except discord.NotFound:
                latest, _, _ = await self.parse_latest_lock(channel, strict=True)
                if latest is not None and latest.id > self._lock_message_id:
                    raise EvoLeadershipLost("Une autre instance a remplacé le verrou du bot.") from None
                raise EvoError("Le verrou de cette instance a disparu. Evo reste suspendu.") from None
            _, instance, _ = self._lock_fields(own)
            if own.author != self.user or own.id != self._lock_message_id or instance != self.INSTANCE_ID:
                raise EvoError("Cette instance n'a plus le verrou du bot. Evo reste suspendu.")
            watermark = max(own.id, self._lock_scan_message_id or own.id)
            latest_id = watermark
            async for message in channel.history(
                limit=None, after=discord.Object(id=watermark), oldest_first=True,
            ):
                latest_id = max(latest_id, message.id)
                if message.author == self.user and message.content.startswith(LOCK_TAG):
                    self._lock_fields(message)
                    raise EvoLeadershipLost("Une autre instance a repris le verrou du bot.")
            self._lock_scan_message_id = latest_id
            return own

    async def ensure_evo_leadership(self):
        """Je vérifie le verrou Discord avant chaque accès au compteur partagé."""
        async with self._evo_check_lock:
            if not self._singleton_ready or not self._evo_connected or self.is_closed():
                await self._suspend_evo("not_ready", cooldown=False)
                raise EvoError("L'instance du bot n'est pas encore prête. Evo reste suspendu.")
            try:
                guild = self.get_guild(self._evo_guild_id())
                channel = resolve_console_channel(guild) if guild is not None else None
                if (channel is None or channel.id != self._lock_channel_id
                        or self._lock_message_id is None):
                    raise EvoError("Le verrou du salon console est indisponible. Evo reste suspendu.")
                await self._verified_lock(channel)
            except Exception as exc:
                await self._suspend_evo("verification_failed")
                logging.debug("Evo leadership verification failed type=%s", type(exc).__name__)
                raise EvoError("Le verrou de l'instance n'est pas vérifiable. Evo reste suspendu.") from None
            if not self._evo_connected:
                await self._suspend_evo("disconnected_during_verification", cooldown=False)
                raise EvoError("La connexion Discord a été interrompue. Evo reste suspendu.")
            if time.monotonic() < self._evo_resume_at:
                logging.debug("Evo leadership waiting for previous requests")
                raise EvoError("Evo attend la fin des demandes de l'instance précédente. Réessaie dans deux minutes.")
            self._evo_leadership_known = True

    async def ensure_build_leadership(self):
        """Je n'autorise les sauvegardes Build que sous le verrou de la console."""
        from utils.build.models import BuildError

        async with self._evo_check_lock:
            if not self._singleton_ready or not self._evo_connected or self.is_closed():
                raise BuildError("La connexion et le verrou du bot ne sont pas prêts.")
            channel = self.get_channel(self._lock_channel_id) if self._lock_channel_id else None
            if channel is None or self._lock_message_id is None:
                raise BuildError("Le verrou du salon #console est indisponible.")
            try:
                if resolve_console_channel(channel.guild) is not channel:
                    raise BuildError("Le verrou ne correspond pas à la console configurée.")
                await self._verified_lock(channel)
            except Exception as exc:
                await self._suspend_evo("build_verification_failed")
                log.debug("Build leadership rejected type=%s", type(exc).__name__)
                raise BuildError("Verrou non vérifiable : aucune sauvegarde Build autorisée.") from None
            if not self._evo_connected or time.monotonic() < self._evo_resume_at:
                raise BuildError("Reprise du bot en cours : réessaie après la fin des opérations précédentes.")
            return channel

    async def acquire_leadership(self):
        ch = await self.wait_console_channel(timeout=30)
        if not ch:
            logging.warning("Salon #%s introuvable: pas de lock distribué, on continue.", os.getenv("CHANNEL_CONSOLE", "console"))
            return True
        previous, _, _ = await self.parse_latest_lock(ch, strict=True)
        my = await ch.send(f"{LOCK_TAG} {self.INSTANCE_ID} {int(time.time())}")
        self._lock_channel_id = ch.id
        self._lock_message_id = my.id
        self._lock_scan_message_id = my.id
        last, inst, ts = await self.parse_latest_lock(ch, strict=True)
        if last and last.id == my.id:
            if previous is not None:
                self._evo_resume_at = time.monotonic() + EVO_HANDOVER_SECONDS
                logging.debug("Evo leadership handover delay=%s", EVO_HANDOVER_SECONDS)
            logging.info("Lock acquis par %s", self.INSTANCE_ID)
            messages = await self._fetch_history(ch, limit=100, oldest_first=False, raise_errors=True)
            for msg in messages:
                if msg.id != self._lock_message_id and msg.author == self.user and msg.content.startswith(LOCK_TAG):
                    self._evo_resume_at = time.monotonic() + EVO_HANDOVER_SECONDS
                    try:
                        await msg.delete()
                    except Exception:
                        pass
            return True
        logging.warning("Lock non acquis, une autre instance est leader.")
        return False

    async def heartbeat_loop(self):
        while not self.is_closed():
            try:
                if self._lock_channel_id and self._lock_message_id:
                    ch = self.get_channel(self._lock_channel_id)
                    if ch:
                        own_lock = await self._verified_lock(ch)
                        await own_lock.edit(content=f"{LOCK_TAG} {self.INSTANCE_ID} {int(time.time())}")
                    else:
                        await self._suspend_evo("channel_unavailable")
            except EvoLeadershipLost:
                await self._suspend_evo("rival_lock")
                logging.warning("Perte du lock au profit d'une autre instance, fermeture.")
                await self.close()
                os._exit(0)
                return
            except Exception as exc:
                await self._suspend_evo("heartbeat_uncertain")
                logging.debug("Evo heartbeat verification failed type=%s", type(exc).__name__)
            await asyncio.sleep(15)

    async def on_disconnect(self):
        set_ready(False)
        self._evo_connected = False
        await self._suspend_evo("disconnect")

    async def on_resumed(self):
        self._evo_connected = True
        set_ready(self._singleton_ready and not self.is_closed())
        logging.debug("Evo Discord connection resumed; leadership recheck required")

    async def on_ready(self):
        self._evo_connected = True
        if not self._console_checked:
            await self.ensure_console_channel()
            self._console_checked = True
        logging.info("Connecté comme %s (id:%s)", self.user, self.user.id)
        if not self._singleton_ready:
            ok = await self.acquire_leadership()
            if not ok:
                logging.warning("Instance concurrente détectée. Fermeture.")
                await self.close()
                os._exit(0)
            self._singleton_ready = True
            asyncio.create_task(self.heartbeat_loop())
        set_ready(self._singleton_ready and self._evo_connected and not self.is_closed())
        if not self._branding_attempted and env_bool("SYNC_BOT_IDENTITY", True):
            self._branding_attempted = True
            await sync_bot_branding(self)
        await cleanup_retired_guild_commands(self)

    async def close(self):
        set_ready(False)
        await super().close()


async def ping_cmd(ctx):
    await ctx.send("Pong!")


async def on_command_error(ctx: commands.Context, error: Exception):
    if getattr(ctx, "slash_error_handled", False):
        return
    if ctx.command is not None and ctx.command.has_error_handler():
        return
    if ctx.cog is not None and commands.Cog._get_overridden_method(ctx.cog.cog_command_error):
        return
    original = getattr(error, "original", error)

    if isinstance(original, commands.CommandNotFound):
        return

    if isinstance(original, commands.MissingRequiredArgument):
        await ctx.reply(
            f"Paramètre manquant : `{original.param.name}`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if isinstance(original, commands.BadArgument):
        await ctx.reply(
            "Argument invalide. Vérifie la commande avec `/aide` ou `!aide`.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if isinstance(original, commands.CommandOnCooldown):
        await ctx.reply(
            f"Réessaie dans {original.retry_after:.0f} seconde(s).",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if isinstance(original, commands.MaxConcurrencyReached):
        await ctx.reply(
            "Une commande est déjà en cours. Attends qu’elle se termine avant de réessayer.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if isinstance(original, commands.CheckFailure):
        await ctx.reply(
            "Tu n’as pas la permission d’utiliser cette commande.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    logging.exception(
        "Erreur commande %s par %s: %s",
        getattr(ctx.command, "qualified_name", "inconnue"),
        getattr(ctx.author, "id", "unknown"),
        original,
    )

    try:
        await ctx.reply(
            "Erreur interne pendant l’exécution de la commande. Le staff peut consulter les logs.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass


def create_bot():
    """Je réinstalle les mêmes commandes et événements sur chaque nouveau client."""
    client = EvoBot()
    client.command(name="ping")(ping_cmd)
    client.event(on_command_error)
    return client


bot = create_bot()


if __name__ == "__main__":
    start_memory_monitor()
    keep_alive()
    try:
        asyncio.run(run_with_shutdown(create_bot, bot))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("Arrêt du bot demandé.")
