#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import base64
import sqlite3
import logging
import time
import socket
from urllib.parse import urlparse, urljoin

import asyncio
import aiohttp
import async_timeout
import validators
import discord
from discord.ext import commands
from cryptography.fernet import Fernet

# Pour la validation avancée (IDN, homograph)
# pip install idna confusable_homoglyphs
import idna
from confusable_homoglyphs import confusables

# Contrôle de flux
from asyncio import Semaphore

# ----------------------------------------
# CONSTANTES DE CONFIG
# ----------------------------------------

URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

MAX_CONCURRENT_SCANS = 5      # Limite de scans simultanés
MAX_RETRIES = 3               # Nb de tentatives en cas de 429/503
BACKOFF_BASE = 2              # Base du backoff exponentiel
BLOCK_PRIVATE_IPS = True      # Bloquer IP privées (SSRF)
USE_ENV_FERNET_KEY = True     # Tenter la clé FERNET_KEY en variable d’env


class DefenderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("Defender")
        self.logger.setLevel(logging.INFO)

        self.GOOGLE_SAFE_BROWSING_API_KEY = os.getenv("GSB_API_KEY")
        self.VIRUSTOTAL_API_KEY = os.getenv("VT_API_KEY")

        self.LISTE_BLANCHE_DOMAINE = []
        self.SHORTLINK_DOMAINS = [
            "bit.ly", "tinyurl.com", "t.co", "goo.gl",
            "ow.ly", "is.gd", "buff.ly", "buffly.com"
        ]

        self.cache_expanded_urls = {}
        self.CACHE_EXPIRATION = 3600

        self.http_session = aiohttp.ClientSession()

        self.KEY_FILE = "secret.key"
        self.DB_FILENAME = "historique_defender.db"
        self.LOG_FILENAME = "defender_discord.log"

        self.init_fernet_key()
        self.initialiser_db()
        self.securiser_fichiers()

        self.scan_semaphore = Semaphore(MAX_CONCURRENT_SCANS)

        self.logger.info("DefenderCog initialisé avec succès.")

    def cog_unload(self):
        if not self.http_session.closed:
            asyncio.create_task(self.http_session.close())
        self.logger.info("DefenderCog déchargé (session fermée).")

    def init_fernet_key(self):
        env_key = os.getenv("FERNET_KEY", "").strip()
        if USE_ENV_FERNET_KEY and env_key:
            self.logger.info("Clé Fernet chargée depuis la variable d'environnement FERNET_KEY.")
            self.key = env_key.encode()
        else:
            if not os.path.exists(self.KEY_FILE):
                self.key = Fernet.generate_key()
                with open(self.KEY_FILE, "wb") as f:
                    f.write(self.key)
                self.logger.info("Nouvelle clé de chiffrement générée (Defender).")
            else:
                with open(self.KEY_FILE, "rb") as f:
                    self.key = f.read()
                self.logger.info("Clé de chiffrement chargée (fichier local).")

        self.fernet = Fernet(self.key)

    def initialiser_db(self):
        try:
            conn = sqlite3.connect(self.DB_FILENAME)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historique (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    statut TEXT NOT NULL,
                    date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
            self.logger.info("Base de données Defender initialisée.")
        except sqlite3.Error as e:
            self.logger.error(f"Erreur init DB: {e}")

    def securiser_fichiers(self):
        if os.name != "nt":
            try:
                os.chmod(self.LOG_FILENAME, 0o600)
                os.chmod(self.DB_FILENAME, 0o600)
                self.logger.info("Fichiers Defender sécurisés (Unix).")
            except Exception as e:
                self.logger.error(f"Erreur chmod (Defender): {e}")

    def enregistrer_historique(self, url: str, statut: str):
        try:
            conn = sqlite3.connect(self.DB_FILENAME)
            cursor = conn.cursor()
            encrypted = self.fernet.encrypt(url.encode()).decode()
            cursor.execute("INSERT INTO historique (url, statut) VALUES (?, ?)", (encrypted, statut))
            conn.commit()
            conn.close()
            self.logger.info(f"[Historique] {self.mask_sensitive_info(url)} => {statut}")
        except sqlite3.Error as e:
            self.logger.error(f"Erreur BD (historique): {e}")

    @commands.command(name="scan", help="Analyse un lien + supprime le message de commande.")
    async def scan_command(self, ctx, *, url: str = None):
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            self.logger.warning("Permission manquante pour supprimer le message de commande.")

        if not url:
            await ctx.send("Usage : `!scan <URL>`", delete_after=10)
            return

        async with self.scan_semaphore:
            statut, color, url_affiche = await self.analyser_url(url)

        if statut is None:
            await ctx.send(f"URL invalide ou non supportée : {url}", delete_after=10)
            return

        embed = self.creer_embed(url_affiche, statut, color)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        found_urls = URL_REGEX.findall(message.content)
        if not found_urls:
            return

        new_content = message.content
        results = []
        worst_status = None

        async with self.scan_semaphore:
            for raw_url in found_urls:
                statut, color, url_affiche = await self.analyser_url(raw_url)
                if statut is None:
                    continue
                results.append((raw_url, statut, url_affiche))

                if "DANGEREUX" in statut:
                    new_content = new_content.replace(raw_url, "[dangerous link removed]")

                severity = 0
                if "INDÉTERMINÉ" in statut:
                    severity = 1
                if "DANGEREUX" in statut:
                    severity = 2

                current_worst = 0
                if worst_status == "INDÉTERMINÉ":
                    current_worst = 1
                elif worst_status == "DANGEREUX":
                    current_worst = 2

                if severity > current_worst:
                    worst_status = (
                        "DANGEREUX" if severity == 2
                        else "INDÉTERMINÉ" if severity == 1
                        else "SÛR"
                    )

        if not results:
            return

        if new_content != message.content:
            try:
                await message.edit(content=new_content)
            except discord.Forbidden:
                self.logger.warning("Impossible d'éditer => permissions manquantes.")
            except discord.HTTPException as e:
                self.logger.warning(f"Erreur lors de l'édition du message: {e}")

        if worst_status == "DANGEREUX":
            final_color = 0xE74C3C
        elif worst_status == "INDÉTERMINÉ":
            final_color = 0xF1C40F
        else:
            final_color = 0x2ECC71

        embed = discord.Embed(
            title="Analyse Defender",
            description=f"Détection et analyse de **{len(results)}** URL(s)",
            color=final_color
        )
        embed.set_footer(text="EVO Defender© By Coca - Analysis via Safe Browsing & VirusTotal")

        for original_url, statut, url_affiche in results:
            embed.add_field(
                name=f"URL détectée : {original_url}",
                value=f"**Statut :** {statut}\n**Affichage :** {url_affiche}",
                inline=False
            )

        await message.reply(embed=embed, mention_author=False)

    async def analyser_url(self, raw_url: str, second_pass: bool = False):
        url_nettoyee, whitelisted = await self.valider_et_nettoyer_url(raw_url)
        if not url_nettoyee:
            return None, None, None

        if whitelisted:
            statut = "SÛR (Liste Blanche)"
            color = 0x2ECC71
            self.enregistrer_historique(url_nettoyee, statut)
            return statut, color, url_nettoyee

        est_sure_sb, _ = await self.verifier_url_safe_browsing(url_nettoyee)
        est_sure_vt, _ = await self.verifier_url_virustotal(url_nettoyee)

        if est_sure_sb is False or est_sure_vt is False:
            statut = "DANGEREUX ⚠️"
            color = 0xE74C3C
        elif est_sure_sb is True and est_sure_vt is True:
            statut = "SÛR ✅"
            color = 0x2ECC71
        else:
            statut = "INDÉTERMINÉ ❓"
            color = 0xF1C40F

        if ("INDÉTERMINÉ" in statut) and (not second_pass):
            self.logger.info(f"Statut indéterminé pour {url_nettoyee}, nouvelle tentative dans 5s...")
            await asyncio.sleep(5)
            return await self.analyser_url(raw_url, second_pass=True)

        self.enregistrer_historique(url_nettoyee, statut)
        url_affiche = (self.mask_dangerous(url_nettoyee)
                       if "DANGEREUX" in statut else url_nettoyee)
        return statut, color, url_affiche

    async def valider_et_nettoyer_url(self, url: str):
        url = url.strip()
        if not self.est_url_valide(url):
            return None, False

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, False

        try:
            host_only = parsed.netloc.split(":")[0]
            puny_host = idna.encode(host_only).decode("ascii")
        except (idna.IDNAError, UnicodeError):
            self.logger.warning(f"Impossible de punycode => {url}")
            return None, False

        port_part = ""
        if ":" in parsed.netloc:
            port_part = ":" + parsed.netloc.split(":", 1)[1]
        puny_netloc = puny_host + port_part
        puny_url = parsed._replace(netloc=puny_netloc).geturl()

        if BLOCK_PRIVATE_IPS:
            if self.is_private_or_local(puny_host):
                self.logger.warning(f"Refus d'une IP/host interne => {url}")
                return None, False

        url = puny_url
        domain = puny_host.lower()
        whitelisted = any(
            domain == w or domain.endswith("." + w)
            for w in self.LISTE_BLANCHE_DOMAINE
        )

        if domain in self.SHORTLINK_DOMAINS:
            url = await self.expand_url(url, max_redirects=3)

        return url, whitelisted

    def est_url_valide(self, url: str) -> bool:
        if not validators.url(url):
            return False
        if len(url) > 2048:
            return False
        if self.contient_scripts_malveillants(url):
            return False
        for ch in url:
            if ord(ch) < 0x20:
                return False
        return True

    def contient_scripts_malveillants(self, url: str) -> bool:
        patterns = [
            r'<script.*?>.*?</script>',
            r'javascript:',
            r'(\%3C|\<)(\%2F|\/)script(\%3E|\>)',
            r'eval\(',
            r'alert\('
        ]
        for pat in patterns:
            if re.search(pat, url, re.IGNORECASE):
                return True
        return False

    async def verifier_url_safe_browsing(self, url: str):
        if not self.GOOGLE_SAFE_BROWSING_API_KEY:
            return None, None

        endpoint = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
        payload = {
            "client": {"clientId": "defender_discord_bot", "clientVersion": "1.0"},
            "threatInfo": {
                "threatTypes": [
                    "MALWARE", "SOCIAL_ENGINEERING",
                    "POTENTIALLY_HARMFUL_APPLICATION", "UNWANTED_SOFTWARE"
                ],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}]
            }
        }
        params = {"key": self.GOOGLE_SAFE_BROWSING_API_KEY}

        for attempt in range(MAX_RETRIES):
            try:
                async with async_timeout.timeout(10):
                    async with self.http_session.post(endpoint, params=params, json=payload) as resp:
                        if resp.status in (429, 503):
                            await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                            continue
                        data = await resp.json()
                        if "matches" in data:
                            return False, data["matches"]
                        return True, None
            except Exception as e:
                self.logger.error(f"Erreur Safe Browsing: {e}")
                return None, None

        self.logger.warning("Échec Safe Browsing => INDÉTERMINÉ")
        return None, None

    async def verifier_url_virustotal(self, url: str):
        if not self.VIRUSTOTAL_API_KEY:
            return None, None
        try:
            url_b64 = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
            endpoint = f"https://www.virustotal.com/api/v3/urls/{url_b64}"
            headers = {"x-apikey": self.VIRUSTOTAL_API_KEY}

            for attempt in range(MAX_RETRIES):
                try:
                    async with async_timeout.timeout(10):
                        async with self.http_session.get(endpoint, headers=headers) as resp:
                            if resp.status in (429, 503):
                                await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                                continue

                            if resp.status == 404:
                                await self.soumettre_virustotal(url)
                                return None, None

                            resp.raise_for_status()
                            data = await resp.json()

                    stats = data["data"]["attributes"]["last_analysis_stats"]
                    malicious = stats.get("malicious", 0)
                    suspicious = stats.get("suspicious", 0)

                    if malicious > 0 or suspicious > 0:
                        return False, data
                    return True, None

                except Exception as e:
                    self.logger.error(f"Erreur VirusTotal (tentative {attempt+1}): {e}")
                    await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))

            self.logger.warning("Échec VirusTotal => INDÉTERMINÉ")
            return None, None

        except Exception as e:
            self.logger.error(f"Erreur VirusTotal (préparation): {e}")
            return None, None

    async def soumettre_virustotal(self, url: str):
        if not self.VIRUSTOTAL_API_KEY:
            return
        endpoint = "https://www.virustotal.com/api/v3/urls"
        headers = {"x-apikey": self.VIRUSTOTAL_API_KEY}
        data = {"url": url}
        try:
            async with async_timeout.timeout(10):
                async with self.http_session.post(endpoint, headers=headers, data=data):
                    pass
        except Exception as e:
            self.logger.error(f"Erreur soumission VirusTotal: {e}")

    def mask_dangerous(self, url: str) -> str:
        return re.sub(r"(?i)^http", "hxxp", url)

    def mask_sensitive_info(self, msg: str) -> str:
        pattern = re.compile(
            r"(?i)\b((?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/[^\s]*)?"
        )
        return pattern.sub(
            lambda m: f"{m.group(1).split('.')[0]}***.{'.'.join(m.group(1).split('.')[1:])}",
            msg
        )

    def creer_embed(self, url_affiche: str, statut: str, color: int) -> discord.Embed:
        embed = discord.Embed(
            title="Analyse Defender",
            description=f"**URL analysée :** {url_affiche}",
            color=color
        )
        embed.add_field(name="Statut", value=statut, inline=False)
        embed.set_footer(text="EVO Defender© By Coca - Analysis via Safe Browsing & VirusTotal")
        return embed

    async def expand_url(self, short_url: str, max_redirects: int = 3) -> str:
        now = time.time()
        if short_url in self.cache_expanded_urls:
            expanded_url, timestamp = self.cache_expanded_urls[short_url]
            if now - timestamp < self.CACHE_EXPIRATION:
                return expanded_url
            else:
                del self.cache_expanded_urls[short_url]

        current_url = short_url
        for _ in range(max_redirects):
            try:
                resp_head = await self.head_or_get(current_url, method="HEAD")
                if resp_head is None or not (200 <= resp_head.status < 300):
                    resp_get = await self.head_or_get(current_url, method="GET")
                    response = resp_get
                else:
                    response = resp_head

                if response is None:
                    break

                if 300 <= response.status < 400 and "Location" in response.headers:
                    next_url = response.headers["Location"]
                    if not urlparse(next_url).netloc:
                        next_url = urljoin(current_url, next_url)
                    if BLOCK_PRIVATE_IPS:
                        host_only = urlparse(next_url).netloc.split(":")[0]
                        if self.is_private_or_local(host_only):
                            self.logger.warning(f"Refus IP locale => {next_url}")
                            break
                    current_url = next_url
                else:
                    self.cache_expanded_urls[short_url] = (current_url, now)
                    return current_url

            except Exception as e:
                self.logger.warning(f"Erreur lors de l'expansion de shortlink: {e}")
                break

        self.cache_expanded_urls[short_url] = (current_url, now)
        return current_url

    async def head_or_get(self, url: str, method: str = "HEAD"):
        try:
            async with async_timeout.timeout(5):
                async with self.http_session.request(method, url, allow_redirects=False) as resp:
                    return resp
        except Exception as e:
            self.logger.debug(f"{method} {url} échoue: {e}")
            return None

    def is_private_or_local(self, host: str) -> bool:
        try:
            addr = socket.gethostbyname(host)
            if addr.startswith("127.") or addr.startswith("10."):
                return True
            if addr.startswith("192.168."):
                return True
            octets = addr.split(".")
            if len(octets) == 4:
                first = int(octets[0])
                second = int(octets[1])
                if first == 172 and 16 <= second <= 31:
                    return True
            if addr == "0.0.0.0" or addr == "::1":
                return True
            return False
        except Exception:
            return True


# --------------------------------------------------------------
# AJOUT D'UN MAIN QUI ACTIVE L'INTENT MESSAGE_CONTENT
# POUR QUE on_message CAPTE LES LIENS
# --------------------------------------------------------------
async def main():
    # 1) On active l'intent message_content
    intents = discord.Intents.default()
    intents.message_content = True

    # 2) On crée le bot
    bot = commands.Bot(command_prefix="!", intents=intents)

    # 3) On charge la Cog Defender
    await bot.add_cog(DefenderCog(bot))

    # 4) On lance le bot
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        print("Pas de DISCORD_TOKEN défini !")
        return

    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    # On démarre l'event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot interrompu manuellement.")
