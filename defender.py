#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
import async_timeout
import discord
import idna
from cryptography.fernet import Fernet, InvalidToken
from discord.ext import commands

log = logging.getLogger("Defender")


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "non"}


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default

    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)

    return value


def _env_csv(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in (os.getenv(name) or default).split(",") if item.strip()]


URL_HOST_PATTERN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,24}|xn--[a-z0-9-]{2,59})"
UNICODE_LABEL_PATTERN = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
UNICODE_TLD_PATTERN = r"(?:[a-z]{2,24}|xn--[a-z0-9-]{2,59}|[^\W_\d]{2,24})"
UNICODE_HOST_PATTERN = rf"(?:{UNICODE_LABEL_PATTERN}\.)+{UNICODE_TLD_PATTERN}"

URL_CANDIDATE_RE = re.compile(
    rf"""(?ix)
    (?:
        \b(?:hxxps?|https?)://[^\s<>"'`]+
        |
        (?<![@\w/.-]){URL_HOST_PATTERN}(?::[^\s@<>"'`]{{1,80}})?@{URL_HOST_PATTERN}(?::\d{{2,5}})?(?:/[^\s<>"'`]*)?
        |
        (?<![@\w/])
        www\.[a-z0-9][a-z0-9.-]{{0,250}}\.
        (?:[a-z]{{2,24}}|xn--[a-z0-9-]{{2,59}})(?::\d{{2,5}})?(?:/[^\s<>"'`]*)?
        |
        (?<![@\w/.-]){UNICODE_HOST_PATTERN}(?::\d{{2,5}})?(?:/[^\s<>"'`]*)?
        |
        (?<![@\w/.-]){URL_HOST_PATTERN}(?::\d{{2,5}})?(?:/[^\s<>"'`]*)?
    )
    """
)

OBFUSCATED_DOT_RE = re.compile(
    r"(?i)(?:\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|<\s*\.\s*>|\\\.)"
)
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

TRAILING_URL_CHARS = ".,;:!?)]}»”’'\"`"
LEADING_URL_CHARS = "<([{«“‘'\"`"

SHORTLINK_DOMAINS_DEFAULT = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "is.gd",
    "buff.ly",
    "buffly.com",
    "cutt.ly",
    "rebrand.ly",
    "s.id",
    "lnkd.in",
    "shorturl.at",
    "rb.gy",
    "bl.ink",
    "trib.al",
    "soo.gd",
    "tiny.cc",
    "urlz.fr",
    "lc.cx",
    "bitly.com",
}

SUSPICIOUS_TLDS_DEFAULT = {
    "zip",
    "mov",
    "click",
    "top",
    "xyz",
    "tk",
    "gq",
    "cf",
    "ml",
    "ga",
    "work",
    "rest",
    "quest",
    "support",
    "cam",
    "country",
    "stream",
    "download",
    "loan",
}

SUSPICIOUS_WORDS = {
    "login",
    "log-in",
    "verify",
    "verification",
    "secure",
    "security",
    "support",
    "account",
    "auth",
    "gift",
    "giveaway",
    "free",
    "nitro",
    "airdrop",
    "wallet",
    "bonus",
    "claim",
    "reward",
    "steam",
    "trade",
    "skin",
    "password",
    "connexion",
    "cadeau",
    "gratuit",
    "kamas",
    "dofus",
    "ankama",
    "discord",
}

BRAND_OFFICIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "discord": ("discord.com", "discord.gg", "discordapp.com", "discordapp.net"),
    "ankama": ("ankama.com", "ankama-games.com"),
    "dofus": ("dofus.com", "dofusretro.com", "dofusbook.net", "dofusdb.fr"),
    "steam": ("steampowered.com", "steamcommunity.com"),
    "google": ("google.com", "accounts.google.com", "youtube.com", "youtu.be"),
    "microsoft": ("microsoft.com", "live.com", "office.com", "outlook.com"),
    "paypal": ("paypal.com", "paypal.me"),
    "facebook": ("facebook.com", "fb.com", "messenger.com"),
    "instagram": ("instagram.com",),
    "twitch": ("twitch.tv",),
    "twitter": ("twitter.com", "x.com"),
    "telegram": ("telegram.org", "t.me"),
}


class ThreatLevel(str, Enum):
    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    DANGEROUS = "dangerous"
    INVALID = "invalid"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UrlCandidate:
    original: str
    normalized: str
    host: str
    scheme: str
    obfuscated: bool = False
    userinfo_present: bool = False
    expanded_from: str | None = None


@dataclass(frozen=True)
class HttpProbeResult:
    status: int
    headers: dict[str, str]


@dataclass
class ProviderResult:
    name: str
    verdict: ThreatLevel
    detail: str
    score_delta: int = 0


@dataclass
class AnalysisResult:
    original: str
    url: str
    host: str
    level: ThreatLevel
    score: int
    reasons: list[str] = field(default_factory=list)
    providers: list[ProviderResult] = field(default_factory=list)
    expanded_from: str | None = None
    color: int = 0x95A5A6

    @property
    def label(self) -> str:
        if self.level == ThreatLevel.DANGEROUS:
            return "DANGEREUX ⚠️"
        if self.level == ThreatLevel.SUSPICIOUS:
            return "SUSPECT 🟠"
        if self.level == ThreatLevel.CLEAN:
            return "Aucun signal fort détecté ✅"
        return "INDÉTERMINÉ ❓"


class DefenderCog(commands.Cog):
    """
    Defender anti-phishing Discord.

    Mode par défaut :
    - scan automatique silencieux ;
    - alerte publique seulement en cas de danger ;
    - rapport détaillé via !scan <url> ;
    - logs staff/console pour les liens suspects et dangereux.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = log

        self.google_safe_browsing_key = os.getenv("GSB_API_KEY", "").strip()
        self.virustotal_api_key = os.getenv("VT_API_KEY", "").strip()
        self.phishtank_app_key = os.getenv("PHISHTANK_APP_KEY", "").strip()

        self.max_concurrent_scans = _env_int(
            "DEFENDER_MAX_CONCURRENT_SCANS",
            5,
            minimum=1,
            maximum=20,
        )
        self.max_urls_per_message = _env_int(
            "DEFENDER_MAX_URLS_PER_MESSAGE",
            5,
            minimum=1,
            maximum=20,
        )
        self.http_timeout = _env_int("DEFENDER_HTTP_TIMEOUT", 8, minimum=2, maximum=30)
        self.max_retries = _env_int("DEFENDER_MAX_RETRIES", 2, minimum=0, maximum=5)
        self.backoff_base = _env_int("DEFENDER_BACKOFF_BASE", 2, minimum=1, maximum=10)

        self.danger_threshold = _env_int("DEFENDER_DANGER_SCORE", 75, minimum=1, maximum=100)
        self.suspicious_threshold = _env_int("DEFENDER_SUSPICIOUS_SCORE", 45, minimum=1, maximum=100)
        self.vt_suspicious_threshold = _env_int(
            "DEFENDER_VT_SUSPICIOUS_THRESHOLD",
            2,
            minimum=1,
            maximum=20,
        )

        self.delete_dangerous = _env_bool("DEFENDER_DELETE_DANGEROUS", True)
        self.public_alert = _env_bool("DEFENDER_PUBLIC_ALERT", True)
        self.public_alert_suspicious = _env_bool("DEFENDER_PUBLIC_ALERT_SUSPICIOUS", False)
        self.public_alert_delete_after = _env_int(
            "DEFENDER_PUBLIC_ALERT_DELETE_AFTER",
            60,
            minimum=0,
            maximum=3600,
        )
        self.mention_author = _env_bool("DEFENDER_MENTION_AUTHOR", False)

        self.log_suspicious = _env_bool("DEFENDER_LOG_SUSPICIOUS", True)
        self.log_clean_manual_only = _env_bool("DEFENDER_LOG_CLEAN_MANUAL_ONLY", True)
        self.alert_cooldown_seconds = _env_int(
            "DEFENDER_ALERT_COOLDOWN",
            120,
            minimum=0,
            maximum=3600,
        )
        self.cache_ttl_seconds = _env_int(
            "DEFENDER_CACHE_TTL",
            1800,
            minimum=60,
            maximum=86400,
        )
        self.cache_max_entries = _env_int(
            "DEFENDER_CACHE_MAX_ENTRIES",
            2048,
            minimum=128,
            maximum=20000,
        )

        self.expand_shortlinks = _env_bool("DEFENDER_EXPAND_SHORTLINKS", True)
        self.vt_submit_unknown = _env_bool("DEFENDER_VT_SUBMIT_UNKNOWN", False)
        self.block_private_ips = _env_bool("DEFENDER_BLOCK_PRIVATE_IPS", True)

        configured_allowlist = set(_env_csv("DEFENDER_DOMAIN_ALLOWLIST"))
        configured_allowlist.update(_env_csv("DEFENDER_DOMAIN_WHITELIST"))

        self.domain_allowlist = {
            self._normalize_domain(d)
            for d in configured_allowlist
            if d.strip()
        }

        self.domain_allowlist.update(
            self._normalize_domain(d)
            for domains in BRAND_OFFICIAL_DOMAINS.values()
            for d in domains
        )

        configured_shortlinks = set(_env_csv("DEFENDER_SHORTLINK_DOMAINS"))
        self.shortlink_domains = {
            self._normalize_domain(d)
            for d in SHORTLINK_DOMAINS_DEFAULT | configured_shortlinks
            if d
        }

        configured_tlds = set(_env_csv("DEFENDER_SUSPICIOUS_TLDS"))
        self.suspicious_tlds = {
            d.lower().lstrip(".")
            for d in (SUSPICIOUS_TLDS_DEFAULT | configured_tlds)
            if d
        }

        self.http_session: aiohttp.ClientSession | None = None
        self.scan_semaphore = asyncio.Semaphore(self.max_concurrent_scans)

        self._analysis_cache: dict[str, tuple[float, AnalysisResult]] = {}
        self._expanded_cache: dict[str, tuple[float, str]] = {}
        self._dns_cache: dict[str, tuple[float, tuple[bool, bool]]] = {}
        self._alert_cooldowns: dict[str, float] = {}

        self.db_filename = os.getenv("DEFENDER_DB_PATH", "historique_defender.db")
        self.history_enabled = _env_bool("DEFENDER_HISTORY_ENABLED", True)
        self.fernet: Fernet | None = None
        self._init_history()

        self.logger.info(
            "Defender initialisé: delete_dangerous=%s, GSB=%s, VT=%s, PhishTank=%s",
            self.delete_dangerous,
            bool(self.google_safe_browsing_key),
            bool(self.virustotal_api_key),
            bool(self.phishtank_app_key),
        )

    async def cog_load(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.http_timeout)
        connector = aiohttp.TCPConnector(limit_per_host=8, ttl_dns_cache=300)

        self.http_session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={"User-Agent": "EvoDefender/2.0 Discord anti-phishing"},
        )

        self.logger.info("Session HTTP Defender créée.")

    async def cog_unload(self) -> None:
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

        self.logger.info("Session HTTP Defender fermée.")

    @commands.command(name="scan", help="Analyse un lien sans déclencher le mode automatique.")
    @commands.cooldown(3, 30, commands.BucketType.user)
    async def scan_command(self, ctx: commands.Context, *, url: str | None = None) -> None:
        if _env_bool("DEFENDER_SCAN_DELETE_COMMAND", True):
            try:
                await ctx.message.delete()
            except (discord.Forbidden, discord.HTTPException):
                pass

        if not url:
            await ctx.send(
                "Usage : `!scan <URL>`",
                delete_after=10,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        async with self.scan_semaphore:
            result = await self.analyser_url(url, manual=True)

        if result is None or result.level == ThreatLevel.INVALID:
            await ctx.send(
                "URL invalide ou non supportée.",
                delete_after=12,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        embed = self.creer_embed(result, detailed=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(
        name="defenderstatus",
        aliases=["defender_status"],
        help="Affiche l'état des fournisseurs Defender.",
    )
    @commands.has_permissions(manage_guild=True)
    async def defender_status_command(self, ctx: commands.Context) -> None:
        embed = discord.Embed(title="État Defender", color=0x3498DB)
        embed.add_field(
            name="Google Safe Browsing",
            value="✅ configuré" if self.google_safe_browsing_key else "⚠️ clé absente",
            inline=True,
        )
        embed.add_field(
            name="VirusTotal",
            value="✅ configuré" if self.virustotal_api_key else "⚠️ clé absente",
            inline=True,
        )
        embed.add_field(
            name="PhishTank",
            value="✅ configuré" if self.phishtank_app_key else "⚠️ clé absente",
            inline=True,
        )
        embed.add_field(name="Mode automatique", value="Silencieux sauf danger", inline=False)
        embed.add_field(
            name="Seuils",
            value=f"Suspect ≥ {self.suspicious_threshold} / Danger ≥ {self.danger_threshold}",
            inline=False,
        )
        embed.add_field(
            name="Cache",
            value=f"{len(self._analysis_cache)} analyses / TTL {self.cache_ttl_seconds}s",
            inline=False,
        )

        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        content = message.content or ""
        if not content:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid and ctx.command and ctx.command.qualified_name in {
            "scan",
            "defenderstatus",
            "defender_status",
        }:
            return

        candidates = self.extraire_urls(content)
        if not candidates:
            return

        candidates = candidates[: self.max_urls_per_message]
        results: list[AnalysisResult] = []

        async with self.scan_semaphore:
            for candidate in candidates:
                result = await self.analyser_url(candidate.original)
                if result and result.level != ThreatLevel.INVALID:
                    results.append(result)

        if not results:
            return

        dangerous = [r for r in results if r.level == ThreatLevel.DANGEROUS]
        suspicious = [r for r in results if r.level == ThreatLevel.SUSPICIOUS]

        if dangerous:
            await self._handle_dangerous_message(message, dangerous)
            return

        if suspicious:
            await self._log_results(message, suspicious, reason="Lien suspect détecté")

            if self.public_alert_suspicious:
                await self._send_public_suspicious_notice(message, suspicious)

    async def analyser_url(self, raw_url: str, *, manual: bool = False) -> AnalysisResult | None:
        candidate = self._normalize_candidate(raw_url)
        if candidate is None:
            return None

        if self._is_domain_allowed(candidate.host):
            result = AnalysisResult(
                original=raw_url,
                url=candidate.normalized,
                host=candidate.host,
                level=ThreatLevel.CLEAN,
                score=0,
                reasons=["Domaine en liste blanche."],
                providers=[],
                expanded_from=candidate.expanded_from,
                color=0x2ECC71,
            )

            if manual:
                self._record_history(result)

            return result

        if self.expand_shortlinks and self._is_shortlink(candidate.host):
            expanded = await self.expand_url(candidate.normalized)

            if expanded and expanded != candidate.normalized:
                expanded_candidate = self._normalize_candidate(
                    expanded,
                    expanded_from=candidate.normalized,
                )

                if expanded_candidate:
                    candidate = expanded_candidate

        cache_key = candidate.normalized
        cached = self._cache_get(self._analysis_cache, cache_key)

        if cached:
            if manual:
                self._record_history(cached)

            return cached

        heuristic_score, reasons = await self._score_heuristics(candidate)
        provider_results = await self._run_providers(candidate.normalized)

        score = min(
            100,
            heuristic_score + sum(max(0, p.score_delta) for p in provider_results),
        )

        provider_danger = any(p.verdict == ThreatLevel.DANGEROUS for p in provider_results)
        provider_suspicious = any(p.verdict == ThreatLevel.SUSPICIOUS for p in provider_results)

        if provider_danger or score >= self.danger_threshold:
            level = ThreatLevel.DANGEROUS
            color = 0xE74C3C
        elif provider_suspicious or score >= self.suspicious_threshold:
            level = ThreatLevel.SUSPICIOUS
            color = 0xE67E22
        else:
            level = ThreatLevel.CLEAN
            color = 0x2ECC71

        result = AnalysisResult(
            original=raw_url,
            url=candidate.normalized,
            host=candidate.host,
            level=level,
            score=score,
            reasons=reasons,
            providers=provider_results,
            expanded_from=candidate.expanded_from,
            color=color,
        )

        self._cache_set(self._analysis_cache, cache_key, result)

        if level in {ThreatLevel.DANGEROUS, ThreatLevel.SUSPICIOUS} or manual or not self.log_clean_manual_only:
            self._record_history(result)

        return result

    async def _run_providers(self, url: str) -> list[ProviderResult]:
        tasks = [
            self.verifier_url_safe_browsing(url),
            self.verifier_url_virustotal(url),
            self.verifier_url_phishtank(url),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        provider_results: list[ProviderResult] = []

        for item in results:
            if isinstance(item, ProviderResult):
                provider_results.append(item)
            elif isinstance(item, Exception):
                self.logger.warning("Provider Defender en erreur: %s", item)

        return provider_results

    async def verifier_url_safe_browsing(self, url: str) -> ProviderResult:
        if not self.google_safe_browsing_key:
            return ProviderResult("Google Safe Browsing", ThreatLevel.UNKNOWN, "clé absente")

        assert self.http_session is not None

        endpoint = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
        payload = {
            "client": {
                "clientId": "evo-defender-discord",
                "clientVersion": "2.0",
            },
            "threatInfo": {
                "threatTypes": [
                    "MALWARE",
                    "SOCIAL_ENGINEERING",
                    "UNWANTED_SOFTWARE",
                    "POTENTIALLY_HARMFUL_APPLICATION",
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}],
            },
        }

        for attempt in range(self.max_retries + 1):
            try:
                async with async_timeout.timeout(self.http_timeout):
                    async with self.http_session.post(
                        endpoint,
                        params={"key": self.google_safe_browsing_key},
                        json=payload,
                    ) as resp:
                        if resp.status in {429, 503} and attempt < self.max_retries:
                            await asyncio.sleep(self.backoff_base * (2**attempt))
                            continue

                        if resp.status >= 400:
                            return ProviderResult(
                                "Google Safe Browsing",
                                ThreatLevel.UNKNOWN,
                                f"HTTP {resp.status}",
                            )

                        data = await resp.json(content_type=None)
                        matches = data.get("matches") or []

                        if matches:
                            types = sorted({m.get("threatType", "UNKNOWN") for m in matches})
                            return ProviderResult(
                                "Google Safe Browsing",
                                ThreatLevel.DANGEROUS,
                                "menace connue: " + ", ".join(types),
                                score_delta=100,
                            )

                        return ProviderResult(
                            "Google Safe Browsing",
                            ThreatLevel.CLEAN,
                            "aucune correspondance",
                        )
            except Exception as exc:
                if attempt >= self.max_retries:
                    self.logger.warning(
                        "Safe Browsing indisponible pour %s: %s",
                        self._mask_url(url),
                        exc,
                    )
                    return ProviderResult(
                        "Google Safe Browsing",
                        ThreatLevel.UNKNOWN,
                        "indisponible",
                    )

                await asyncio.sleep(self.backoff_base * (2**attempt))

        return ProviderResult("Google Safe Browsing", ThreatLevel.UNKNOWN, "indisponible")

    async def verifier_url_virustotal(self, url: str) -> ProviderResult:
        if not self.virustotal_api_key:
            return ProviderResult("VirusTotal", ThreatLevel.UNKNOWN, "clé absente")

        assert self.http_session is not None

        url_id = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        endpoint = f"https://www.virustotal.com/api/v3/urls/{url_id}"
        headers = {"x-apikey": self.virustotal_api_key}

        for attempt in range(self.max_retries + 1):
            try:
                async with async_timeout.timeout(self.http_timeout):
                    async with self.http_session.get(endpoint, headers=headers) as resp:
                        if resp.status in {429, 503} and attempt < self.max_retries:
                            await asyncio.sleep(self.backoff_base * (2**attempt))
                            continue

                        if resp.status == 404:
                            if self.vt_submit_unknown:
                                await self._submit_virustotal(url)

                            return ProviderResult("VirusTotal", ThreatLevel.UNKNOWN, "URL inconnue")

                        if resp.status >= 400:
                            return ProviderResult(
                                "VirusTotal",
                                ThreatLevel.UNKNOWN,
                                f"HTTP {resp.status}",
                            )

                        data = await resp.json(content_type=None)

                stats = (
                    data.get("data", {})
                    .get("attributes", {})
                    .get("last_analysis_stats", {})
                )

                malicious = int(stats.get("malicious", 0) or 0)
                suspicious = int(stats.get("suspicious", 0) or 0)
                harmless = int(stats.get("harmless", 0) or 0)

                if malicious >= 1:
                    return ProviderResult(
                        "VirusTotal",
                        ThreatLevel.DANGEROUS,
                        f"{malicious} moteur(s) malveillant(s), {suspicious} suspect(s)",
                        score_delta=100,
                    )

                if suspicious >= self.vt_suspicious_threshold:
                    return ProviderResult(
                        "VirusTotal",
                        ThreatLevel.DANGEROUS,
                        f"{suspicious} moteur(s) suspect(s)",
                        score_delta=80,
                    )

                if suspicious > 0:
                    return ProviderResult(
                        "VirusTotal",
                        ThreatLevel.SUSPICIOUS,
                        f"{suspicious} moteur suspect",
                        score_delta=25,
                    )

                return ProviderResult(
                    "VirusTotal",
                    ThreatLevel.CLEAN,
                    f"{harmless} moteur(s) sans alerte",
                )
            except Exception as exc:
                if attempt >= self.max_retries:
                    self.logger.warning(
                        "VirusTotal indisponible pour %s: %s",
                        self._mask_url(url),
                        exc,
                    )
                    return ProviderResult("VirusTotal", ThreatLevel.UNKNOWN, "indisponible")

                await asyncio.sleep(self.backoff_base * (2**attempt))

        return ProviderResult("VirusTotal", ThreatLevel.UNKNOWN, "indisponible")

    async def _submit_virustotal(self, url: str) -> None:
        if not self.virustotal_api_key or not self.http_session:
            return

        try:
            async with async_timeout.timeout(self.http_timeout):
                await self.http_session.post(
                    "https://www.virustotal.com/api/v3/urls",
                    headers={"x-apikey": self.virustotal_api_key},
                    data={"url": url},
                )
        except Exception as exc:
            self.logger.debug("Soumission VirusTotal impossible: %s", exc)

    async def verifier_url_phishtank(self, url: str) -> ProviderResult:
        if not self.phishtank_app_key:
            return ProviderResult("PhishTank", ThreatLevel.UNKNOWN, "clé absente")

        assert self.http_session is not None

        data = {
            "url": url,
            "format": "json",
            "app_key": self.phishtank_app_key,
        }

        for attempt in range(self.max_retries + 1):
            try:
                async with async_timeout.timeout(self.http_timeout):
                    async with self.http_session.post(
                        "https://checkurl.phishtank.com/checkurl/",
                        data=data,
                    ) as resp:
                        if resp.status in {429, 503} and attempt < self.max_retries:
                            await asyncio.sleep(self.backoff_base * (2**attempt))
                            continue

                        if resp.status >= 400:
                            return ProviderResult(
                                "PhishTank",
                                ThreatLevel.UNKNOWN,
                                f"HTTP {resp.status}",
                            )

                        payload = await resp.json(content_type=None)

                results = payload.get("results") or {}

                in_database = bool(results.get("in_database"))
                verified = bool(results.get("verified"))
                valid = results.get("valid")
                is_valid = True if valid is None else bool(valid)

                if in_database and verified and is_valid:
                    return ProviderResult(
                        "PhishTank",
                        ThreatLevel.DANGEROUS,
                        "phishing vérifié",
                        score_delta=100,
                    )

                if in_database:
                    return ProviderResult(
                        "PhishTank",
                        ThreatLevel.SUSPICIOUS,
                        "présent en base mais non vérifié",
                        score_delta=35,
                    )

                return ProviderResult("PhishTank", ThreatLevel.UNKNOWN, "non présent en base")
            except Exception as exc:
                if attempt >= self.max_retries:
                    self.logger.warning(
                        "PhishTank indisponible pour %s: %s",
                        self._mask_url(url),
                        exc,
                    )
                    return ProviderResult("PhishTank", ThreatLevel.UNKNOWN, "indisponible")

                await asyncio.sleep(self.backoff_base * (2**attempt))

        return ProviderResult("PhishTank", ThreatLevel.UNKNOWN, "indisponible")

    def extraire_urls(self, content: str) -> list[UrlCandidate]:
        normalized_text, text_was_obfuscated = self._deobfuscate_text(content)

        candidates: list[UrlCandidate] = []
        seen: set[str] = set()

        for match in URL_CANDIDATE_RE.finditer(normalized_text):
            token = match.group(0)
            candidate = self._normalize_candidate(
                token,
                forced_obfuscated=text_was_obfuscated,
            )

            if not candidate:
                continue

            if candidate.normalized in seen:
                continue

            seen.add(candidate.normalized)
            candidates.append(candidate)

        return candidates

    def _deobfuscate_text(self, value: str) -> tuple[str, bool]:
        original = value

        text = unicodedata.normalize("NFKC", value or "")
        text = ZERO_WIDTH_RE.sub("", text)
        text = CONTROL_RE.sub(" ", text)
        text = OBFUSCATED_DOT_RE.sub(".", text)
        text = re.sub(r"(?i)\bhxxps://", "https://", text)
        text = re.sub(r"(?i)\bhxxp://", "http://", text)

        return text, text != original

    def _normalize_candidate(
        self,
        raw_url: str,
        *,
        forced_obfuscated: bool = False,
        expanded_from: str | None = None,
    ) -> UrlCandidate | None:
        if not raw_url:
            return None

        token_raw = raw_url.strip().strip(LEADING_URL_CHARS).strip(TRAILING_URL_CHARS)
        token, token_obfuscated = self._deobfuscate_text(token_raw)
        token = token.strip().strip(LEADING_URL_CHARS).strip(TRAILING_URL_CHARS)

        if not token:
            return None

        if re.match(r"(?i)^hxxps?://", token):
            token_obfuscated = True
            token = re.sub(r"(?i)^hxxps://", "https://", token)
            token = re.sub(r"(?i)^hxxp://", "http://", token)

        if token.lower().startswith("www."):
            token = "https://" + token

        if not re.match(r"(?i)^https?://", token):
            token = "https://" + token

        if len(token) > 2048:
            return None

        parsed = urlparse(token)
        scheme = parsed.scheme.lower()

        if scheme not in {"http", "https"}:
            return None

        host = parsed.hostname or ""
        if not host:
            return None

        try:
            ascii_host = self._normalize_domain(host)
        except ValueError:
            return None

        try:
            parsed_port = parsed.port
        except ValueError:
            return None

        port = ""
        if parsed_port is not None:
            if not (1 <= parsed_port <= 65535):
                return None

            port = f":{parsed_port}"

        if ":" in ascii_host and not ascii_host.startswith("["):
            netloc = f"[{ascii_host}]{port}"
        else:
            netloc = f"{ascii_host}{port}"

        normalized = urlunparse(
            (
                scheme,
                netloc,
                parsed.path or "/",
                "",
                parsed.query,
                "",
            )
        )

        return UrlCandidate(
            original=raw_url,
            normalized=normalized,
            host=ascii_host.lower().strip("[]"),
            scheme=scheme,
            obfuscated=forced_obfuscated or token_obfuscated,
            userinfo_present=bool(parsed.username or parsed.password or "@" in parsed.netloc),
            expanded_from=expanded_from,
        )

    def _normalize_domain(self, domain: str) -> str:
        host = (domain or "").strip().strip(".").strip("[]").lower()

        if not host:
            raise ValueError("empty host")

        try:
            ip = ipaddress.ip_address(host)
            return str(ip)
        except ValueError:
            pass

        try:
            return idna.encode(host, uts46=True).decode("ascii").lower()
        except (idna.IDNAError, UnicodeError) as exc:
            raise ValueError(f"invalid IDN: {domain}") from exc

    def _is_domain_allowed(self, host: str) -> bool:
        domain = self._normalize_domain(host)

        return any(
            domain == allowed or domain.endswith("." + allowed)
            for allowed in self.domain_allowlist
        )

    def _is_shortlink(self, host: str) -> bool:
        domain = self._normalize_domain(host)

        return any(
            domain == shortener or domain.endswith("." + shortener)
            for shortener in self.shortlink_domains
        )

    async def _score_heuristics(self, candidate: UrlCandidate) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        host = candidate.host
        parsed = urlparse(candidate.normalized)

        if candidate.userinfo_present:
            score += 45
            reasons.append("URL avec identifiants avant le domaine réel, technique fréquente d’hameçonnage.")

        if candidate.obfuscated:
            score += 20
            reasons.append("Lien volontairement obfusqué : `hxxp`, `[.]`, caractères invisibles…")

        if candidate.expanded_from:
            score += 15
            reasons.append(f"Raccourcisseur redirigé depuis {self._mask_url(candidate.expanded_from)}.")

        if self._is_shortlink(host):
            score += 18
            reasons.append("Domaine de raccourcisseur d’URL.")

        forbidden, resolved = await self._host_resolves_to_forbidden(host)

        if forbidden:
            score += 90
            reasons.append("Destination locale/privée/réservée bloquée par sécurité.")
        elif not resolved:
            score += 12
            reasons.append("Domaine non résolu pendant l’analyse DNS.")

        try:
            ipaddress.ip_address(host)
            score += 15
            reasons.append("URL basée sur une adresse IP au lieu d’un nom de domaine.")
        except ValueError:
            pass

        if candidate.scheme == "http":
            score += 8
            reasons.append("Lien non chiffré en HTTP.")

        if "xn--" in host:
            score += 35
            reasons.append("Domaine IDN/punycode, risque d’homographe visuel.")

        labels = [part for part in host.split(".") if part]
        tld = labels[-1] if labels else ""

        if tld in self.suspicious_tlds:
            score += 10
            reasons.append(f"TLD souvent utilisé dans des campagnes opportunistes : .{tld}")

        if len(labels) >= 5:
            score += 8
            reasons.append("Nombre inhabituel de sous-domaines.")

        domain_body = ".".join(labels[:-1])
        domain_body_for_words = self._strip_accents(self._decode_idn(host).rsplit(".", 1)[0])
        hyphen_count = domain_body.count("-")
        digit_count = sum(ch.isdigit() for ch in domain_body)

        if hyphen_count >= 3:
            score += 7
            reasons.append("Domaine avec beaucoup de tirets.")

        if digit_count >= 4:
            score += 7
            reasons.append("Domaine avec beaucoup de chiffres.")

        suspicious_words_found = {
            word
            for word in SUSPICIOUS_WORDS
            if re.search(rf"(?i)(^|[-_.]){re.escape(word)}($|[-_.])", domain_body_for_words)
            or word in (parsed.path or "").lower()
        }

        if suspicious_words_found:
            points = min(20, 4 * len(suspicious_words_found))
            score += points
            reasons.append(
                "Mots sensibles détectés : "
                + ", ".join(sorted(suspicious_words_found)[:8])
                + "."
            )

        brand_hits = self._detect_brand_impersonation(host)

        for brand, detail, points in brand_hits:
            score += points
            reasons.append(f"Suspicion d’usurpation {brand}: {detail}")

        if len(reasons) > 10:
            reasons = reasons[:10] + ["Autres signaux mineurs tronqués."]

        return min(score, 100), reasons

    def _detect_brand_impersonation(self, host: str) -> list[tuple[str, str, int]]:
        if self._is_domain_allowed(host):
            return []

        labels = [label for label in host.split(".") if label]

        if len(labels) < 2:
            return []

        decoded_host = self._strip_accents(self._decode_idn(host).lower())
        decoded_labels = [label for label in decoded_host.split(".") if label]
        root_label = labels[-2]
        decoded_root_label = decoded_labels[-2] if len(decoded_labels) >= 2 else root_label
        full_domain = ".".join(labels[-2:])
        compact = re.sub(r"[^a-z0-9]", "", ".".join(labels[:-1]).lower())
        decoded_compact = re.sub(r"[^a-z0-9]", "", ".".join(decoded_labels[:-1]).lower())

        hits: list[tuple[str, str, int]] = []

        for brand, official_domains in BRAND_OFFICIAL_DOMAINS.items():
            if any(host == d or host.endswith("." + d) for d in official_domains):
                continue

            brand_compact = re.sub(r"[^a-z0-9]", "", brand.lower())

            if brand_compact in compact or brand_compact in decoded_compact:
                points = 35
                lure_words = {
                    "gift",
                    "free",
                    "nitro",
                    "login",
                    "verify",
                    "support",
                    "secure",
                    "claim",
                    "kamas",
                }

                if any(word in compact or word in decoded_compact for word in lure_words):
                    points = 50

                hits.append(
                    (
                        brand,
                        f"le domaine `{full_domain}` contient la marque hors domaine officiel",
                        points,
                    )
                )
                continue

            ascii_distance = self._levenshtein_limited(root_label.lower(), brand_compact, limit=2)
            decoded_distance = self._levenshtein_limited(
                decoded_root_label.lower(),
                brand_compact,
                limit=2,
            )
            distance = min(ascii_distance, decoded_distance)

            if distance <= 2 and abs(len(decoded_root_label) - len(brand_compact)) <= 2:
                hits.append(
                    (
                        brand,
                        f"`{decoded_root_label}` ressemble fortement à `{brand}`",
                        40,
                    )
                )

        return hits

    def _decode_idn(self, host: str) -> str:
        try:
            return idna.decode(host, uts46=True)
        except (idna.IDNAError, UnicodeError):
            return host

    def _strip_accents(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    def _levenshtein_limited(self, a: str, b: str, *, limit: int = 2) -> int:
        if abs(len(a) - len(b)) > limit:
            return limit + 1

        previous = list(range(len(b) + 1))

        for i, ca in enumerate(a, 1):
            current = [i]
            row_min = current[0]

            for j, cb in enumerate(b, 1):
                insert = current[j - 1] + 1
                delete = previous[j] + 1
                replace = previous[j - 1] + (0 if ca == cb else 1)

                value = min(insert, delete, replace)
                current.append(value)
                row_min = min(row_min, value)

            if row_min > limit:
                return limit + 1

            previous = current

        return previous[-1]

    async def expand_url(self, url: str, max_redirects: int = 4) -> str:
        cached = self._cache_get(self._expanded_cache, url)

        if cached:
            return cached

        current_url = url

        for _ in range(max_redirects):
            parsed = urlparse(current_url)

            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                break

            forbidden, resolved = await self._host_resolves_to_forbidden(parsed.hostname)

            if self.block_private_ips and (forbidden or not resolved):
                break

            response = await self.head_or_get(current_url, method="HEAD")

            if response is None or response.status in {405, 403}:
                response = await self.head_or_get(current_url, method="GET")

            if response is None:
                break

            if 300 <= response.status < 400 and response.headers.get("Location"):
                next_url = urljoin(current_url, response.headers["Location"].strip())
                next_candidate = self._normalize_candidate(next_url)

                if not next_candidate:
                    break

                forbidden, resolved = await self._host_resolves_to_forbidden(next_candidate.host)

                if self.block_private_ips and (forbidden or not resolved):
                    break

                current_url = next_candidate.normalized
                continue

            break

        self._cache_set(self._expanded_cache, url, current_url)

        return current_url

    async def head_or_get(self, url: str, *, method: str = "HEAD") -> HttpProbeResult | None:
        if not self.http_session:
            return None

        try:
            async with async_timeout.timeout(self.http_timeout):
                async with self.http_session.request(
                    method,
                    url,
                    allow_redirects=False,
                    max_redirects=0,
                ) as resp:
                    return HttpProbeResult(status=resp.status, headers=dict(resp.headers))
        except Exception as exc:
            self.logger.debug("%s %s échoue: %s", method, self._mask_url(url), exc)
            return None

    def _is_forbidden_ip(self, ip: ipaddress._BaseAddress) -> bool:
        cgnat = ipaddress.ip_network("100.64.0.0/10")

        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
            or ip in cgnat
        )

    async def _host_resolves_to_forbidden(self, host: str) -> tuple[bool, bool]:
        cache_key = host.lower().strip("[]")
        cached = self._cache_get(self._dns_cache, cache_key)

        if cached is not None:
            return cached

        try:
            literal = ipaddress.ip_address(cache_key)
            result = (self._is_forbidden_ip(literal), True)
            self._cache_set(self._dns_cache, cache_key, result, ttl=300)
            return result
        except ValueError:
            pass

        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    cache_key,
                    None,
                    type=socket.SOCK_STREAM,
                ),
                timeout=3,
            )
        except Exception as exc:
            self.logger.debug("DNS non résolu pour %s: %s", cache_key, exc)
            result = (False, False)
            self._cache_set(self._dns_cache, cache_key, result, ttl=120)
            return result

        resolved_any = False

        for _family, _socktype, _proto, _canonname, sockaddr in infos:
            resolved_any = True

            try:
                resolved_ip = ipaddress.ip_address(sockaddr[0])
            except Exception:
                result = (True, True)
                self._cache_set(self._dns_cache, cache_key, result, ttl=300)
                return result

            if self._is_forbidden_ip(resolved_ip):
                result = (True, True)
                self._cache_set(self._dns_cache, cache_key, result, ttl=300)
                return result

        result = (False, resolved_any)
        self._cache_set(self._dns_cache, cache_key, result, ttl=300)

        return result

    async def _handle_dangerous_message(
        self,
        message: discord.Message,
        results: list[AnalysisResult],
    ) -> None:
        deleted = False

        if self.delete_dangerous:
            try:
                await message.delete()
                deleted = True
            except discord.Forbidden:
                self.logger.warning(
                    "Permission MANAGE_MESSAGES manquante pour supprimer un lien dangereux."
                )
            except discord.HTTPException as exc:
                self.logger.warning("Suppression du message impossible: %s", exc)

        await self._log_results(
            message,
            results,
            reason="Lien dangereux détecté",
            deleted=deleted,
        )

        cooldown_key = f"public:{message.channel.id}:{message.author.id}:{results[0].host}"

        if not self.public_alert or not self._cooldown_ok(cooldown_key):
            return

        domain = self._mask_domain(results[0].host)

        if self.mention_author:
            author_text = message.author.mention
            allowed = discord.AllowedMentions(
                users=[message.author],
                roles=False,
                everyone=False,
                replied_user=False,
            )
        else:
            author_text = discord.utils.escape_mentions(
                discord.utils.escape_markdown(message.author.display_name)
            )
            allowed = discord.AllowedMentions.none()

        action = "bloqué et supprimé" if deleted else "détecté"
        content = f"🛡️ Defender a {action} un lien dangereux envoyé par {author_text} (`{domain}`)."

        try:
            await message.channel.send(
                content,
                delete_after=self.public_alert_delete_after or None,
                allowed_mentions=allowed,
            )
        except discord.HTTPException as exc:
            self.logger.warning("Alerte publique Defender impossible: %s", exc)

    async def _send_public_suspicious_notice(
        self,
        message: discord.Message,
        results: list[AnalysisResult],
    ) -> None:
        cooldown_key = f"suspicious:{message.channel.id}:{message.author.id}:{results[0].host}"

        if not self._cooldown_ok(cooldown_key):
            return

        try:
            await message.reply(
                "🛡️ Defender a repéré un lien douteux. Vérifie-le avec `!scan <lien>` avant de cliquer.",
                mention_author=False,
                delete_after=self.public_alert_delete_after or None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            pass

    async def _log_results(
        self,
        message: discord.Message,
        results: list[AnalysisResult],
        *,
        reason: str,
        deleted: bool = False,
    ) -> None:
        if not results:
            return

        if results[0].level == ThreatLevel.SUSPICIOUS and not self.log_suspicious:
            return

        channel = self._resolve_log_channel(message.guild)

        if channel is None:
            return

        cooldown_key = f"log:{message.guild.id}:{results[0].level}:{results[0].host}"

        if not self._cooldown_ok(cooldown_key):
            return

        embed = discord.Embed(
            title=f"🛡️ Defender — {reason}",
            color=results[0].color,
        )
        embed.add_field(
            name="Auteur",
            value=f"{message.author} (`{message.author.id}`)",
            inline=False,
        )
        embed.add_field(
            name="Salon",
            value=getattr(message.channel, "mention", str(message.channel)),
            inline=True,
        )
        embed.add_field(
            name="Message supprimé",
            value="oui" if deleted else "non",
            inline=True,
        )

        for idx, result in enumerate(results[:5], 1):
            lines = [
                f"Verdict : **{result.label}**",
                f"Score : **{result.score}/100**",
                f"Domaine : `{self._mask_domain(result.host)}`",
                f"URL : `{self._mask_url(result.url)}`",
            ]

            if result.expanded_from:
                lines.append(f"Depuis : `{self._mask_url(result.expanded_from)}`")

            if result.reasons:
                lines.append("Signaux : " + "; ".join(result.reasons[:4]))

            embed.add_field(
                name=f"URL #{idx}",
                value=self._truncate("\n".join(lines), 1024),
                inline=False,
            )

        embed.set_footer(text="EVO Defender — rapport interne")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            self.logger.warning("Log Defender impossible: %s", exc)

    def _resolve_log_channel(self, guild: discord.Guild | None) -> discord.TextChannel | None:
        if guild is None:
            return None

        raw_id = (os.getenv("DEFENDER_LOG_CHANNEL_ID") or "").strip()

        if raw_id.isdigit():
            channel = guild.get_channel(int(raw_id))

            if isinstance(channel, discord.TextChannel):
                return channel

        names = _env_csv("DEFENDER_LOG_CHANNEL_NAMES", "console,mod-logs,logs")
        wanted = {name.casefold() for name in names}

        for channel in guild.text_channels:
            if channel.name.casefold() in wanted:
                return channel

        return None

    def creer_embed(self, result: AnalysisResult, *, detailed: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title="Analyse Defender",
            description=f"**Verdict :** {result.label}\n**Score :** {result.score}/100",
            color=result.color,
        )

        embed.add_field(
            name="URL analysée",
            value=f"`{self._truncate(self._mask_url(result.url), 950)}`",
            inline=False,
        )

        if result.expanded_from:
            embed.add_field(
                name="Redirection",
                value=f"Depuis `{self._mask_url(result.expanded_from)}`",
                inline=False,
            )

        if result.reasons:
            embed.add_field(
                name="Signaux locaux",
                value=self._truncate(
                    "\n".join(f"• {r}" for r in result.reasons[:8]),
                    1024,
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Signaux locaux",
                value="Aucun signal local fort.",
                inline=False,
            )

        if detailed:
            provider_lines = []

            for provider in result.providers:
                if provider.verdict == ThreatLevel.CLEAN:
                    icon = "✅"
                elif provider.verdict == ThreatLevel.DANGEROUS:
                    icon = "⚠️"
                elif provider.verdict == ThreatLevel.SUSPICIOUS:
                    icon = "🟠"
                else:
                    icon = "❔"

                provider_lines.append(f"{icon} **{provider.name}** : {provider.detail}")

            embed.add_field(
                name="Fournisseurs",
                value=self._truncate(
                    "\n".join(provider_lines) or "Aucun fournisseur configuré.",
                    1024,
                ),
                inline=False,
            )

        embed.set_footer(text="EVO Defender — scan anti-phishing")

        return embed

    def _mask_url(self, url: str) -> str:
        masked = re.sub(r"(?i)^http", "hxxp", url or "")
        return masked.replace(".", "[.]")

    def _mask_domain(self, domain: str) -> str:
        return (domain or "").replace(".", "[.]")

    def _truncate(self, value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value

        return value[: max_len - 1].rstrip() + "…"

    def _cooldown_ok(self, key: str) -> bool:
        if self.alert_cooldown_seconds <= 0:
            return True

        now = time.monotonic()
        until = self._alert_cooldowns.get(key, 0)

        if until > now:
            return False

        self._alert_cooldowns[key] = now + self.alert_cooldown_seconds

        return True

    def _cache_get(self, cache: dict[str, tuple[float, Any]], key: str) -> Any | None:
        item = cache.get(key)

        if not item:
            return None

        expires_at, value = item

        if expires_at < time.monotonic():
            cache.pop(key, None)
            return None

        return value

    def _cache_set(
        self,
        cache: dict[str, tuple[float, Any]],
        key: str,
        value: Any,
        *,
        ttl: int | None = None,
    ) -> None:
        if len(cache) >= self.cache_max_entries:
            now = time.monotonic()
            expired = [k for k, (expires_at, _v) in cache.items() if expires_at < now]

            for expired_key in expired[: max(1, len(expired))]:
                cache.pop(expired_key, None)

            if len(cache) >= self.cache_max_entries:
                oldest_key = min(cache.items(), key=lambda item: item[1][0])[0]
                cache.pop(oldest_key, None)

        cache[key] = (
            time.monotonic() + (ttl or self.cache_ttl_seconds),
            value,
        )

    def _init_history(self) -> None:
        if not self.history_enabled:
            return

        key = (os.getenv("FERNET_KEY") or "").strip()

        if not key:
            self.logger.warning(
                "FERNET_KEY absent: historique Defender désactivé, analyse toujours active."
            )
            self.history_enabled = False
            return

        try:
            self.fernet = Fernet(key.encode("utf-8"))
        except Exception as exc:
            self.logger.warning("FERNET_KEY invalide: historique Defender désactivé: %s", exc)
            self.history_enabled = False
            return

        try:
            with sqlite3.connect(self.db_filename) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS historique (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        statut TEXT NOT NULL,
                        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )

                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(historique)").fetchall()
                }

                if "host" not in columns:
                    conn.execute("ALTER TABLE historique ADD COLUMN host TEXT")

                if "score" not in columns:
                    conn.execute("ALTER TABLE historique ADD COLUMN score INTEGER NOT NULL DEFAULT 0")

                if "reasons" not in columns:
                    conn.execute("ALTER TABLE historique ADD COLUMN reasons TEXT")

                conn.commit()

            if os.name != "nt" and os.path.exists(self.db_filename):
                os.chmod(self.db_filename, 0o600)
        except sqlite3.Error as exc:
            self.logger.warning("Historique Defender indisponible: %s", exc)
            self.history_enabled = False

    def _record_history(self, result: AnalysisResult) -> None:
        if not self.history_enabled or not self.fernet:
            return

        try:
            encrypted_url = self.fernet.encrypt(result.url.encode("utf-8")).decode("ascii")
            encrypted_reasons = self.fernet.encrypt(
                json.dumps(result.reasons, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")

            with sqlite3.connect(self.db_filename) as conn:
                conn.execute(
                    """
                    INSERT INTO historique (url, host, statut, score, reasons)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        encrypted_url,
                        result.host,
                        result.level.value,
                        result.score,
                        encrypted_reasons,
                    ),
                )
                conn.commit()
        except (sqlite3.Error, InvalidToken, ValueError) as exc:
            self.logger.warning("Écriture historique Defender impossible: %s", exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DefenderCog(bot))
