#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import importlib
import logging
import os
from collections import deque
from typing import Deque, Dict, List, Optional

import discord
from discord.ext import commands

try:
    import yt_dlp
except Exception:  # pragma: no cover - import optional at runtime
    yt_dlp = None  # type: ignore

log = logging.getLogger("music")

STAFF_ROLE_NAME = os.getenv("MUSIC_STAFF_ROLE", "Staff")
MAX_QUEUE_PER_GUILD = int(os.getenv("MUSIC_MAX_QUEUE", "100"))
FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"
YTDL_OPTS = {
    "format": "bestaudio/best",
    "default_search": "auto",
    "noplaylist": False,
    "quiet": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "geo_bypass": True,
    "source_address": "0.0.0.0",
}

def _pynacl_available() -> bool:
    return importlib.util.find_spec("nacl") is not None


class MusicCog(commands.Cog):
    """Simple music helper that streams audio from YouTube links for staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: Dict[int, Deque[dict]] = {}
        self.current_track: Dict[int, dict] = {}
        self._ytdl = self._build_ytdl()
        self._lock = asyncio.Lock()

    def _build_ytdl(self):
        if yt_dlp is None:
            log.warning("yt_dlp absent - commande !musique indisponible.")
            return None
        return yt_dlp.YoutubeDL(YTDL_OPTS)

    def _has_staff_role(self, member: discord.Member) -> bool:
        return any(getattr(role, "name", "") == STAFF_ROLE_NAME for role in getattr(member, "roles", []))

    async def _send_embed(self, ctx: commands.Context, title: str, description: str, color=discord.Color.blurple()):
        embed = discord.Embed(title=title, description=description, color=color)
        await ctx.send(embed=embed)

    async def _ensure_voice_channel(self, ctx: commands.Context) -> Optional[discord.VoiceChannel]:
        channel = getattr(getattr(ctx.author, "voice", None), "channel", None)
        if channel is None:
            await self._send_embed(
                ctx,
                "Salon vocal requis",
                "Rejoins un salon vocal avant d'utiliser `!musique`.",
                discord.Color.orange(),
            )
            return None
        return channel

    async def _ensure_client(self, guild: discord.Guild, channel: discord.VoiceChannel):
        voice = guild.voice_client
        if voice and voice.is_connected():
            if voice.channel != channel:
                await voice.move_to(channel)
        else:
            voice = await channel.connect()
        return voice

    def _ensure_ytdl(self) -> bool:
        if self._ytdl is not None:
            return True
        global yt_dlp  # pylint: disable=global-statement
        if yt_dlp is None:
            try:
                yt_dlp = importlib.import_module("yt_dlp")
            except Exception:
                return False
        self._ytdl = self._build_ytdl()
        return self._ytdl is not None

    async def _extract_tracks(self, url: str) -> List[dict]:
        if not self._ensure_ytdl():
            return []
        loop = asyncio.get_running_loop()

        def _task():
            return self._ytdl.extract_info(url, download=False)

        try:
            info = await loop.run_in_executor(None, _task)
        except Exception as exc:  # pragma: no cover - network errors are runtime only
            log.warning("Extraction yt_dlp impossible: %s", exc)
            return []

        entries = []
        if info is None:
            return entries
        if "entries" in info:
            for item in info.get("entries") or []:
                if item:
                    entries.append(item)
        else:
            entries.append(info)
        prepared = []
        for item in entries:
            stream_url = item.get("url")
            if not stream_url:
                continue
            prepared.append(
                {
                    "title": item.get("title") or item.get("webpage_url") or "Titre inconnu",
                    "stream_url": stream_url,
                    "webpage": item.get("webpage_url") or url,
                    "duration": item.get("duration"),
                }
            )
        return prepared

    def _queue_for(self, guild_id: int) -> Deque[dict]:
        if guild_id not in self.queues:
            self.queues[guild_id] = deque()
        return self.queues[guild_id]

    def _create_source(self, stream_url: str):
        return discord.FFmpegPCMAudio(stream_url, before_options=FFMPEG_BEFORE_OPTS, options=FFMPEG_OPTS)

    async def _play_next(self, guild: discord.Guild):
        queue = self.queues.get(guild.id)
        if not queue:
            self.current_track.pop(guild.id, None)
            voice = guild.voice_client
            if voice and voice.is_connected() and not voice.is_playing():
                await voice.disconnect()
            return
        voice = guild.voice_client
        if voice is None:
            return
        track = queue.popleft()
        self.current_track[guild.id] = track

        try:
            source = self._create_source(track["stream_url"])
        except Exception as exc:  # pragma: no cover - depends on ffmpeg availability
            log.error("FFmpeg invalide pour %s: %s", track.get("title"), exc)
            await self._play_next(guild)
            return

        def _after_playback(error: Optional[Exception]):
            if error:
                log.warning("Erreur lecture audio: %s", error)
            asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)

        voice.play(source, after=_after_playback)

    async def _stop_and_cleanup(self, guild: discord.Guild):
        self.queues.pop(guild.id, None)
        self.current_track.pop(guild.id, None)
        voice = guild.voice_client
        if voice and voice.is_connected():
            voice.stop()
            try:
                await voice.disconnect()
            except Exception:
                pass

    @commands.command(name="musique")
    async def musique_command(self, ctx: commands.Context, *, query: Optional[str] = None):
        """Stream a YouTube track/playlist to the caller's voice channel."""
        if not self._has_staff_role(ctx.author):
            await self._send_embed(
                ctx,
                "Accès refusé",
                "Seul le Staff peut utiliser `!musique`.",
                discord.Color.red(),
            )
            return

        if not query:
            await self._send_embed(
                ctx,
                "Utilisation",
                "`!musique <url_youtube>` pour lancer une musique ou une playlist.\n"
                "`!musique stop` pour arrêter et déconnecter le bot.",
            )
            return

        channel = await self._ensure_voice_channel(ctx)
        if channel is None:
            return

        if query.lower().strip() == "stop":
            await self._stop_and_cleanup(ctx.guild)
            await self._send_embed(ctx, "Lecture arrêtée", "La musique a été coupée et le bot a quitté le salon.")
            return

        if not self._ensure_ytdl():
            await self._send_embed(
                ctx,
                "yt-dlp manquant",
                "Installez `yt-dlp` pour activer la commande musique.",
                discord.Color.red(),
            )
            return

        await self._send_embed(ctx, "Analyse en cours", "Récupération du flux audio...", discord.Color.blurple())

        tracks = await self._extract_tracks(query)
        if not tracks:
            await self._send_embed(
                ctx,
                "Aucun flux disponible",
                "Impossible de récupérer cette URL. Vérifie le lien ou essaie un autre média.",
                discord.Color.orange(),
            )
            return

        async with self._lock:
            queue = self._queue_for(ctx.guild.id)
            can_enqueue = max(0, MAX_QUEUE_PER_GUILD - len(queue))
            to_add = tracks[:can_enqueue] if can_enqueue else []
            for item in to_add:
                queue.append(item)
            voice = await self._ensure_client(ctx.guild, channel)
            if voice and not voice.is_playing():
                await self._play_next(ctx.guild)

        if not to_add:
            await self._send_embed(
                ctx,
                "File d'attente pleine",
                "Trop de musiques sont déjà programmées. Réessaie plus tard.",
                discord.Color.orange(),
            )
            return

        added_msg = to_add[0]["title"]
        if len(to_add) > 1:
            added_msg += f" + {len(to_add) - 1} autres titres"
        await self._send_embed(
            ctx,
            "Musique ajoutée",
            f"Lecture programmée pour **{added_msg}**\nSalon : {channel.mention}",
            discord.Color.green(),
        )

    async def cog_unload(self):
        for guild_id in list(self.queues.keys()):
            guild = self.bot.get_guild(guild_id)
            if guild:
                await self._stop_and_cleanup(guild)


async def setup(bot: commands.Bot):
    if not _pynacl_available():
        log.warning("PyNaCl absent - commandes vocales désactivées.")
        return
    await bot.add_cog(MusicCog(bot))
