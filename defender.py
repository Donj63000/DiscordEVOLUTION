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

# Pour la détection de noms de domaines IDN (optionnel pour l’homographe)
import idna

# Limitation du nombre de scans simultanés
from asyncio import Semaphore

# ---------------------------------------------------------------------------
# 1) CONFIG LOGGER GLOBAL
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,  # Pour déboguer davantage, passer en logging.DEBUG
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("defender_discord.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# ---------------------------------------------------------------------------
# 2) CONSTANTES ET REGEX
# ---------------------------------------------------------------------------
URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

MAX_CONCURRENT_SCANS = 5      # Limite de scans simultanés
MAX_RETRIES = 3               # Nb de tentatives en cas de 429/503
BACKOFF_BASE = 2              # Base du backoff exponentiel

BLOCK_PRIVATE_IPS = True      # Bloquer l'accès aux IP privées (SSRF)
USE_ENV_FERNET_KEY = True     # Tenter la clé FERNET_KEY depuis l'env

# ---------------------------------------------------------------------------
# 3) LA COG DEFENDER
# ---------------------------------------------------------------------------
class DefenderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("Defender")

        # Clés d'API éventuellement depuis les variables d'environnement
        self.GOOGLE_SAFE_BROWSING_API_KEY = os.getenv("GSB_API_KEY")
        self.VIRUSTOTAL_API_KEY = os.getenv("VT_API_KEY")
        self.PHISHTANK_APP_KEY = os.getenv("PHISHTANK_APP_KEY")  # facultatif

        # Whitelist de domaines (à personnaliser si besoin)
        self.LISTE_BLANCHE_DOMAINE = []

        # Shortlinks potentiels
        self.SHORTLINK_DOMAINS = [
            "bit.ly", "tinyurl.com", "t.co", "goo.gl",
            "ow.ly", "is.gd", "buff.ly", "buffly.com"
        ]

        # Cache pour l'expansion de shortlinks (éviter de tout re-télécharger)
        self.cache_expanded_urls = {}
        self.CACHE_EXPIRATION = 3600  # 1h

        # La session aiohttp sera créée dans cog_load
        self.http_session = None

        # Fichiers de config
        self.KEY_FILE = "secret.key"
        self.DB_FILENAME = "historique_defender.db"
        self.LOG_FILENAME = "defender_discord.log"

        # Génération / chargement de la clé de chiffrement (Fernet)
        self.init_fernet_key()

        # DB pour stocker l’historique
        self.initialiser_db()

        # Sécuriser les fichiers sur systèmes Unix (chmod)
        self.securiser_fichiers()

        # Semaphore pour limiter les scans simultanés
        self.scan_semaphore = Semaphore(MAX_CONCURRENT_SCANS)

        self.logger.info("DefenderCog initialisé (la session aiohttp sera créée dans cog_load).")

    # -----------------------------------------------------------------------
    # 4) GESTION DE CHARGEMENT/DECHARGEMENT DU COG
    # -----------------------------------------------------------------------
    async def cog_load(self):
        """Nouveau hook disponible dans discord.py >= 2.3 pour init asynchrone."""
        self.http_session = aiohttp.ClientSession()
        self.logger.info("ClientSession créée dans cog_load().")

    async def cog_unload(self):
        """Fermeture asynchrone propre de la session HTTP."""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        self.logger.info("Session HTTP fermée dans cog_unload().")

    # -----------------------------------------------------------------------
    # 5) INITIALISATION DE LA CLE FERNET ET DB
    # -----------------------------------------------------------------------
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
            # Sous Windows, chmod ne fonctionne pas toujours comme attendu
            try:
                os.chmod(self.LOG_FILENAME, 0o600)
                os.chmod(self.DB_FILENAME, 0o600)
                self.logger.info("Fichiers Defender sécurisés (Unix).")
            except Exception as e:
                self.logger.error(f"Erreur chmod (Defender): {e}")

    # -----------------------------------------------------------------------
    # 6) COMMANDE MANUELLE  !scan <URL>
    # -----------------------------------------------------------------------
    @commands.command(name="scan", help="Analyse un lien + supprime le message de commande.")
    async def scan_command(self, ctx, *, url: str = None):
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            self.logger.warning("Permission manquante pour supprimer le message de commande.")
        except discord.HTTPException as ex:
            self.logger.warning(f"Impossible de supprimer le message de commande : {ex}")

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

    # -----------------------------------------------------------------------
    # 7) INTERCEPTION AUTOMATIQUE DES MESSAGES (on_message)
    # -----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        found_urls = URL_REGEX.findall(message.content)
        if not found_urls:
            # Important : laisser passer d’éventuelles commandes
            await self.bot.process_commands(message)
            return

        new_content = message.content
        results = []
        worst_status = None  # On stocke le plus mauvais statut (SÛR, INDÉTERMINÉ, DANGEREUX)

        async with self.scan_semaphore:
            for raw_url in found_urls:
                statut, color, url_affiche = await self.analyser_url(raw_url)
                if statut is None:
                    continue  # URL invalide ?

                results.append((raw_url, statut, url_affiche))

                # Si c'est un lien DANGEREUX, on remplace par un placeholder
                if "DANGEREUX" in statut:
                    new_content = new_content.replace(raw_url, "[dangerous link removed]")

                # Calcul de la « sévérité »
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
            # Aucune URL valable => on laisse passer les commandes
            await self.bot.process_commands(message)
            return

        # Édition du message si on a supprimé un lien dangereux
        if new_content != message.content:
            try:
                await message.edit(content=new_content)
            except discord.Forbidden:
                self.logger.warning("Impossible d'éditer => permissions manquantes.")
            except discord.HTTPException as e:
                self.logger.warning(f"Erreur lors de l'édition du message: {e}")

        # Couleur globale en fonction du "plus mauvais statut"
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
        embed.set_footer(text="EVO Defender© By Coca - Analysis via PhishTank & VirusTotal")

        # Ajout d’un field par URL détectée
        for original_url, statut, url_affiche in results:
            embed.add_field(
                name=f"URL détectée : {original_url}",
                value=f"**Statut :** {statut}\n**Affichage :** {url_affiche}",
                inline=False
            )

        await message.reply(embed=embed, mention_author=False)

        # Enfin, on laisse passer les commandes
        await self.bot.process_commands(message)

    # -----------------------------------------------------------------------
    # 8) ANALYSE PRINCIPALE  (PhishTank, VirusTotal, etc.)
    # -----------------------------------------------------------------------
    async def analyser_url(self, raw_url: str, second_pass: bool = False):
        url_nettoyee, whitelisted = await self.valider_et_nettoyer_url(raw_url)
        if not url_nettoyee:
            return None, None, None

        if whitelisted:
            statut = "SÛR (Liste Blanche)"
            color = 0x2ECC71
            self.enregistrer_historique(url_nettoyee, statut)
            return statut, color, url_nettoyee

        # ---------------------------------------------------------
        # A) APPEL PHISHTANK
        # ---------------------------------------------------------
        est_sure_pt, details_pt = await self.verifier_url_phishtank(url_nettoyee)

        # ---------------------------------------------------------
        # B) APPEL VIRUSTOTAL
        # ---------------------------------------------------------
        est_sure_vt, details_vt = await self.verifier_url_virustotal(url_nettoyee)

        # ---------------------------------------------------------
        # C) APPEL SAFE BROWSING (OPTIONNEL)
        # ---------------------------------------------------------
        if self.GOOGLE_SAFE_BROWSING_API_KEY:
            est_sure_sb, details_sb = await self.verifier_url_safe_browsing(url_nettoyee)
        else:
            # Si aucune clé GSB => on suppose SÛR
            est_sure_sb, details_sb = True, None

        # ---------------------------------------------------------
        # D) FUSION DES VERDICTS
        # ---------------------------------------------------------
        flags_danger = [(x is False) for x in (est_sure_pt, est_sure_vt, est_sure_sb)]
        flags_sure   = [(x is True)  for x in (est_sure_pt, est_sure_vt, est_sure_sb)]

        if any(flags_danger):
            statut = "DANGEREUX ⚠️"
            color  = 0xE74C3C
        elif all(flags_sure):
            statut = "SÛR ✅"
            color  = 0x2ECC71
        else:
            statut = "INDÉTERMINÉ ❓"
            color  = 0xF1C40F

        # Tentative d’analyse en second pass si c’est indéterminé
        if ("INDÉTERMINÉ" in statut) and (not second_pass):
            self.logger.info(f"Statut indéterminé pour {url_nettoyee}, nouvelle tentative dans 5s...")
            await asyncio.sleep(5)
            return await self.analyser_url(raw_url, second_pass=True)

        self.enregistrer_historique(url_nettoyee, statut)

        url_affiche = (self.mask_dangerous(url_nettoyee)
                       if "DANGEREUX" in statut else url_nettoyee)
        return statut, color, url_affiche

    # -----------------------------------------------------------------------
    # 9) PHISHTANK (nouvelle source pour phishing)
    # -----------------------------------------------------------------------
    async def verifier_url_phishtank(self, url: str):
        endpoint = "https://checkurl.phishtank.com/checkurl/"
        data = {"url": url, "format": "json"}

        if self.PHISHTANK_APP_KEY:
            data["app_key"] = self.PHISHTANK_APP_KEY

        try:
            async with async_timeout.timeout(10):
                async with self.http_session.post(endpoint, data=data) as resp:
                    if resp.status != 200:
                        return None, None
                    payload = await resp.json()
        except Exception as e:
            self.logger.error(f"PhishTank : {e}")
            return None, None

        try:
            r = payload["results"]
            if r["in_database"] and r["verified"]:
                # Si c'est dans la base et vérifié => phishing
                return False, r
            else:
                return True, None
        except (KeyError, TypeError):
            return None, None

    # -----------------------------------------------------------------------
    # 10) VIRUSTOTAL
    # -----------------------------------------------------------------------
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
                                # Rate-limit ou service indisponible : backoff exponentiel
                                await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                                continue

                            if resp.status == 404:
                                # URL inconnue => la soumettre
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

    # -----------------------------------------------------------------------
    # 11) SAFE BROWSING (OPTIONNEL)
    # -----------------------------------------------------------------------
    async def verifier_url_safe_browsing(self, url: str):
        if not self.GOOGLE_SAFE_BROWSING_API_KEY:
            return True, None

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
                        if resp.status == 204:
                            # 204 => pas de menace
                            return True, None

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

    # -----------------------------------------------------------------------
    # 12) ENREGISTREMENT DE L’HISTORIQUE
    # -----------------------------------------------------------------------
    def enregistrer_historique(self, url: str, statut: str):
        """Chiffrement du lien avant insertion, pour éviter de stocker en clair."""
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

    # -----------------------------------------------------------------------
    # 13) VALIDATION ET NETTOYAGE DE L’URL
    # -----------------------------------------------------------------------
    async def valider_et_nettoyer_url(self, url: str):
        url = url.strip()
        if not self.est_url_valide(url):
            return None, False

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, False

        try:
            host_only = parsed.netloc.split(":")[0]
            # Convertir en punycode
            puny_host = idna.encode(host_only).decode("ascii")
        except (idna.IDNAError, UnicodeError):
            self.logger.warning(f"Impossible de punycode => {url}")
            return None, False

        port_part = ""
        if ":" in parsed.netloc:
            port_part = ":" + parsed.netloc.split(":", 1)[1]
        puny_netloc = puny_host + port_part
        puny_url = parsed._replace(netloc=puny_netloc).geturl()

        # Bloquer IP privées ?
        if BLOCK_PRIVATE_IPS:
            if self.is_private_or_local(puny_host):
                self.logger.warning(f"Refus d'une IP/host interne => {url}")
                return None, False

        # Whitelist ?
        domain = puny_host.lower()
        whitelisted = any(
            domain == w or domain.endswith("." + w)
            for w in self.LISTE_BLANCHE_DOMAINE
        )

        # Expansion des shortlinks
        if domain in self.SHORTLINK_DOMAINS:
            puny_url = await self.expand_url(puny_url, max_redirects=3)

        return puny_url, whitelisted

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

    # -----------------------------------------------------------------------
    # 14) EXPANSION DES SHORTLINKS (HEAD/GET)
    # -----------------------------------------------------------------------
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

    # -----------------------------------------------------------------------
    # 15) DÉTECTION IP PRIVÉE
    # -----------------------------------------------------------------------
    def is_private_or_local(self, host: str) -> bool:
        """Retourne True si la résolution du host donne une IP privée/locale."""
        try:
            addr = socket.gethostbyname(host)
        except socket.gaierror:
            return False
        except Exception:
            return True

        if addr.startswith("127.") or addr.startswith("10."):
            return True
        if addr.startswith("192.168."):
            return True

        octets = addr.split(".")
        if len(octets) == 4:
            first = int(octets[0])
            second = int(octets[1])
            # 172.16.x.x à 172.31.x.x
            if first == 172 and 16 <= second <= 31:
                return True

        if addr == "0.0.0.0" or addr == "::1":
            return True

        return False

    # -----------------------------------------------------------------------
    # 16) MASQUAGE ET CREATION EMBED
    # -----------------------------------------------------------------------
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
        embed.set_footer(text="EVO Defender© By Coca - Analysis via PhishTank & VirusTotal")
        return embed


# ---------------------------------------------------------------------------
# 17) FONCTION SETUP POUR LE LOAD_EXTENSION (OBLIGATOIRE)
# ---------------------------------------------------------------------------
async def setup(bot: commands.Bot):
    """Fonction attendue par bot.load_extension("defender") pour ajouter la Cog."""
    await bot.add_cog(DefenderCog(bot))
