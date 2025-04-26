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

# Limite de scans concurrents pour éviter la surcharge
MAX_CONCURRENT_SCANS = 5

# Tenter le back-off exponentiel en cas de 429 ou 503
MAX_RETRIES = 3
BACKOFF_BASE = 2  # en secondes

# On considère que certaines IP (localhost, réseaux privés) doivent être bloquées
# pour éviter SSRF
BLOCK_PRIVATE_IPS = True

# Usage d'un KMS ou variable d'env pour la clé Fernet ?
# - Par défaut, on essaie de lire FERNET_KEY en variable d’environnement
# - Si absente, on tente fallback sur un fichier "secret.key"
USE_ENV_FERNET_KEY = True


class DefenderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Logger dédié pour éviter de modifier la config globale
        self.logger = logging.getLogger("Defender")
        self.logger.setLevel(logging.INFO)

        # Clés d'API
        self.GOOGLE_SAFE_BROWSING_API_KEY = os.getenv("GSB_API_KEY")
        self.VIRUSTOTAL_API_KEY = os.getenv("VT_API_KEY")

        # Liste blanche de domaines (ex. "exemple.com")
        self.LISTE_BLANCHE_DOMAINE = []

        # Liste explicite de domaines shortlinks
        self.SHORTLINK_DOMAINS = [
            "bit.ly", "tinyurl.com", "t.co", "goo.gl",
            "ow.ly", "is.gd", "buff.ly", "buffly.com"
        ]

        # Cache pour éviter de ré-expandre à chaque fois :
        # { short_url: (expanded_url, timestamp) }
        self.cache_expanded_urls = {}
        self.CACHE_EXPIRATION = 3600  # 1 heure (en secondes)

        # Session aiohttp pour les requêtes asynchrones
        self.http_session = aiohttp.ClientSession()

        # Gestions clés et fichiers
        self.KEY_FILE = "secret.key"
        self.DB_FILENAME = "historique_defender.db"
        self.LOG_FILENAME = "defender_discord.log"

        # Initialise la clé (via variable d'env + fallback secret.key)
        self.init_fernet_key()

        # Initialisation DB + sécurisation
        self.initialiser_db()
        self.securiser_fichiers()

        # Sémaphore pour limiter le nombre de scans en parallèle
        self.scan_semaphore = Semaphore(MAX_CONCURRENT_SCANS)

        self.logger.info("DefenderCog initialisé avec succès.")

    def cog_unload(self):
        """
        Méthode appelée lors du déchargement du Cog.
        On en profite pour fermer la session HTTP proprement.
        """
        if not self.http_session.closed:
            asyncio.create_task(self.http_session.close())
        self.logger.info("DefenderCog déchargé (session fermée).")

    # -----------------------------------------------------
    # 1) GESTION ET ROTATION SECURISEES DE LA CLE FERNET
    # -----------------------------------------------------

    def init_fernet_key(self):
        """
        Tente de récupérer la clé Fernet depuis la variable d'environnement FERNET_KEY.
        Sinon fallback sur un fichier local "secret.key" (non recommandé en prod).
        Cette partie peut être remplacée par un appel KMS (Vault, SSM, etc.).
        """
        env_key = os.getenv("FERNET_KEY", "").strip()
        if USE_ENV_FERNET_KEY and env_key:
            # On récupère la clé depuis l'environnement
            self.logger.info("Clé Fernet chargée depuis la variable d'environnement FERNET_KEY.")
            self.key = env_key.encode()
        else:
            # Fallback sur le fichier local
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

    def rotate_key(self, old_key: bytes, new_key: bytes):
        """
        Exemple de rotation de clé (pseudo-code).
        On récupère tous les enregistrements, on les déchiffre avec l'ancienne
        clé, puis on les ré-insère chiffrés avec la nouvelle clé.
        """
        self.logger.warning("DÉBUT rotation de clé Fernet (exemple).")
        try:
            old_fernet = Fernet(old_key)
            new_fernet = Fernet(new_key)

            conn = sqlite3.connect(self.DB_FILENAME)
            cursor = conn.cursor()
            cursor.execute("SELECT id, url FROM historique")
            rows = cursor.fetchall()
            for row in rows:
                row_id = row[0]
                encrypted_url = row[1]
                # déchiffre
                plain_url = old_fernet.decrypt(encrypted_url.encode()).decode()
                # rechiffre
                re_encrypted = new_fernet.encrypt(plain_url.encode()).decode()
                cursor.execute("UPDATE historique SET url=? WHERE id=?", (re_encrypted, row_id))

            conn.commit()
            conn.close()
            self.logger.warning("Rotation de clé réussie.")
        except Exception as e:
            self.logger.error(f"Erreur pendant la rotation de clé: {e}")

    # -----------------------------------------------------
    # 2) INITIALISATION DB + PERMISSIONS
    # -----------------------------------------------------

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
        # Sur Unix, on limite les permissions aux seuls utilisateurs autorisés
        if os.name != "nt":
            try:
                os.chmod(self.LOG_FILENAME, 0o600)
                os.chmod(self.DB_FILENAME, 0o600)
                self.logger.info("Fichiers Defender sécurisés (Unix).")
            except Exception as e:
                self.logger.error(f"Erreur chmod (Defender): {e}")

    # -----------------------------------------------------
    # 3) ECRITURE HISTORIQUE
    # -----------------------------------------------------

    def enregistrer_historique(self, url: str, statut: str):
        """
        Enregistre l'URL analysée et son statut (sûr/dangereux/indéterminé)
        en base de données, après chiffrement de l'URL.
        """
        try:
            conn = sqlite3.connect(self.DB_FILENAME)
            cursor = conn.cursor()
            encrypted = self.fernet.encrypt(url.encode()).decode()
            cursor.execute(
                "INSERT INTO historique (url, statut) VALUES (?, ?)",
                (encrypted, statut)
            )
            conn.commit()
            conn.close()
            self.logger.info(f"[Historique] {self.mask_sensitive_info(url)} => {statut}")
        except sqlite3.Error as e:
            self.logger.error(f"Erreur BD (historique): {e}")

    # -----------------------------------------------------
    # 4) COMMANDES DISCORD
    # -----------------------------------------------------

    @commands.command(name="scan",
                      help="Analyse un lien (Safe Browsing + VirusTotal) et supprime le message de commande.")
    async def scan_command(self, ctx, *, url: str = None):
        """
        Commande !scan <URL> : supprime le message de commande et
        effectue une analyse complète sur l'URL.
        """
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            self.logger.warning("Permission manquante pour supprimer le message de commande.")

        if not url:
            await ctx.send("Usage : `!scan <URL>`", delete_after=10)
            return

        # On place l'appel dans un bloc "with semaphore" pour contrôle de flux
        async with self.scan_semaphore:
            statut, color, url_affiche = await self.analyser_url(url)
        if statut is None:
            await ctx.send(f"URL invalide ou non supportée : {url}", delete_after=10)
            return

        embed = self.creer_embed(url_affiche, statut, color)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Méthode qui intercepte les messages pour scanner
        les éventuels liens. Ne PAS rappeler process_commands ici
        pour éviter les doublons de réponses.
        """
        if message.author.bot:
            return

        found_urls = URL_REGEX.findall(message.content)
        if not found_urls:
            return  # Aucun lien => rien à faire

        new_content = message.content
        results = []
        worst_status = None

        # Un seul bloc "with self.scan_semaphore" ici
        # (mais si vous voulez un traitement concurrent, on peut le mettre par lien)
        async with self.scan_semaphore:
            for raw_url in found_urls:
                statut, color, url_affiche = await self.analyser_url(raw_url)
                if statut is None:
                    continue

                results.append((raw_url, statut, url_affiche))

                # Remplacement si dangereux
                if "DANGEREUX" in statut:
                    new_content = new_content.replace(raw_url, "[dangerous link removed]")

                # Détermine le pire statut
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
                    worst_status = ("DANGEREUX" if severity == 2
                                    else "INDÉTERMINÉ" if severity == 1
                                    else "SÛR")

        # Si aucun lien valide => rien à faire
        if not results:
            return

        # Édition du message si besoin
        if new_content != message.content:
            try:
                await message.edit(content=new_content)
            except discord.Forbidden:
                self.logger.warning("Impossible d'éditer le message => permissions manquantes.")
            except discord.HTTPException as e:
                self.logger.warning(f"Erreur lors de l'édition du message: {e}")

        # Couleur embed
        if worst_status == "DANGEREUX":
            final_color = 0xE74C3C
        elif worst_status == "INDÉTERMINÉ":
            final_color = 0xF1C40F
        else:
            final_color = 0x2ECC71

        embed = discord.Embed(
            title="Analyse Defender",
            description=f"Détection et analyse de **{len(results)}** URL(s) dans ce message.",
            color=final_color
        )
        embed.set_footer(text="EVO Defender© By Coca - Analysis via Safe Browsing & VirusTotal")

        for original_url, statut, url_affiche in results:
            embed.add_field(
                name=f"URL détectée : {original_url}",
                value=f"**Statut :** {statut}\n**Affichage :** {url_affiche}",
                inline=False
            )

        # On répond au message original
        await message.reply(embed=embed, mention_author=False)

    # -----------------------------------------------------
    # 5) ANALYSE DE L'URL
    # -----------------------------------------------------

    async def analyser_url(self, raw_url: str, second_pass: bool = False):
        """
        Analyse asynchrone d'une URL en s'appuyant sur Google Safe Browsing et VirusTotal.
        Fait un second passage après ~5s si le statut est indéterminé (et que ce n'est pas déjà un second passage).

        Retourne (statut, color, url_affiche) ou (None, None, None) si invalide.
        """
        url_nettoyee, whitelisted = await self.valider_et_nettoyer_url(raw_url)
        if not url_nettoyee:
            return None, None, None

        # Domaine whiteliste
        if whitelisted:
            statut = "SÛR (Liste Blanche)"
            color = 0x2ECC71
            self.enregistrer_historique(url_nettoyee, statut)
            return statut, color, url_nettoyee

        # On peut faire des appels en parallèle, ex:
        # (est_sure_sb, data_sb), (est_sure_vt, data_vt) = await asyncio.gather(
        #     self.verifier_url_safe_browsing(url_nettoyee),
        #     self.verifier_url_virustotal(url_nettoyee)
        # )
        # Pour la lisibilité, on reste séquentiel

        est_sure_sb, _ = await self.verifier_url_safe_browsing(url_nettoyee)
        est_sure_vt, _ = await self.verifier_url_virustotal(url_nettoyee)

        # Déterminer statut
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
            self.logger.info(f"Statut indéterminé pour {url_nettoyee}. Nouvelle tentative dans 5s...")
            await asyncio.sleep(5)
            return await self.analyser_url(raw_url, second_pass=True)

        self.enregistrer_historique(url_nettoyee, statut)
        url_affiche = (self.mask_dangerous(url_nettoyee)
                       if "DANGEREUX" in statut else url_nettoyee)
        return statut, color, url_affiche

    # -----------------------------------------------------
    # 6) VALIDATION AVANCEE DES URL
    # -----------------------------------------------------

    async def valider_et_nettoyer_url(self, url: str):
        """
        Vérifie si l'URL est correctement formée, commence par http/https,
        et si elle appartient éventuellement à la liste blanche.
        Puis, si c'est un shortlink connu, on suit la redirection (expand) en asynchrone.

        On ajoute ici un filtrage homographe & punycode, on rejette
        les IP privées si BLOCK_PRIVATE_IPS est True.
        """
        url = url.strip()

        # Filtre basique
        if not self.est_url_valide(url):
            return None, False

        # Normalisation IDN → punycode
        # On tente de transformer le netloc en punycode, pour éviter les homograph
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, False

        try:
            # netloc sans le port
            host_only = parsed.netloc.split(":")[0]
            # punycode
            puny_host = idna.encode(host_only).decode("ascii")
        except (idna.IDNAError, UnicodeError):
            self.logger.warning(f"Impossible de punycode => {url}")
            return None, False

        # Reconstruire l'URL complet avec le punycode
        # (on garde le port si présent)
        port_part = ""
        if ":" in parsed.netloc:
            port_part = ":" + parsed.netloc.split(":", 1)[1]
        puny_netloc = puny_host + port_part
        puny_url = parsed._replace(netloc=puny_netloc).geturl()

        # On vérifie s'il y a des caractères confusables
        # -> si confusables est non-vide, on peut log/rejeter ou marquer danger
        # confusables_list = confusables(host_only, greedy=True)
        # if confusables_list:
        #     self.logger.warning(f"[Homograph] Domain confusable: {host_only} -> {confusables_list}")

        # Vérifier si pas d'IP privée en cas de SSRF
        if BLOCK_PRIVATE_IPS:
            if self.is_private_or_local(puny_host):
                self.logger.warning(f"Refus d'une IP/host interne => {url}")
                return None, False

        # On met puny_url comme URL "officielle"
        url = puny_url

        # Liste blanche
        domain = puny_host.lower()
        whitelisted = any(
            domain == w or domain.endswith("." + w)
            for w in self.LISTE_BLANCHE_DOMAINE
        )

        # Si c'est un domaine shortlink → expansion
        if domain in self.SHORTLINK_DOMAINS:
            url = await self.expand_url(url, max_redirects=3)

        return url, whitelisted

    def est_url_valide(self, url: str) -> bool:
        """
        Vérifie via validators si l'URL est formellement valide et ne contient pas de code malveillant.
        Ajoute un contrôle sur les caractères invisibles / de contrôle.
        """
        # 1) Vérif standard
        if not validators.url(url):
            return False
        if len(url) > 2048:
            return False

        # 2) Détection de scripts, patterns JS
        if self.contient_scripts_malveillants(url):
            return False

        # 3) Caractères de contrôle ou invisibles
        # (ex: U+202E, ou 0x00 - 0x1F)
        for ch in url:
            if ord(ch) < 0x20:  # ASCII < 32
                return False
            # On peut ajouter d'autres checks (mirroring, RTLO, etc.)

        return True

    def contient_scripts_malveillants(self, url: str) -> bool:
        """
        Détection simple de patterns JavaScript malveillants dans l'URL.
        """
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

    # -----------------------------------------------------
    # 7) VERIFICATION SAFE BROWSING & VIRUSTOTAL
    #    AVEC BACKOFF / RETRIES
    # -----------------------------------------------------

    async def verifier_url_safe_browsing(self, url: str):
        """
        Vérification asynchrone via Google Safe Browsing.
        Retourne (True, None) si sûre, (False, data) si rapport négatif,
        (None, None) si impossible de conclure (ex: pas de clé).
        Avec un back-off exponentiel si 429 ou 503.
        """
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
                            # On applique un backoff exponentiel
                            await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))
                            continue

                        data = await resp.json()
                        if "matches" in data:
                            return False, data["matches"]
                        return True, None

            except Exception as e:
                self.logger.error(f"Erreur Safe Browsing: {e}")
                return None, None

        # Au bout de N tentatives, on retourne None
        self.logger.warning("Échec Safe Browsing après retries => INDÉTERMINÉ")
        return None, None

    async def verifier_url_virustotal(self, url: str):
        """
        Vérification asynchrone via VirusTotal.
        Retourne (True, None) si sûre, (False, data) si rapport négatif,
        (None, None) si pas de clé ou erreur/timeout.
        Avec backoff si 429/503.
        """
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
                                # Soumettre l'URL si elle n'existe pas
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
                    # On retente => backoff
                    await asyncio.sleep(BACKOFF_BASE * (2 ** attempt))

            # échec final
            self.logger.warning("Échec VirusTotal après retries => INDÉTERMINÉ")
            return None, None

        except Exception as e:
            self.logger.error(f"Erreur VirusTotal (préparation): {e}")
            return None, None

    async def soumettre_virustotal(self, url: str):
        """Soumet l'URL à VirusTotal quand on ne la trouve pas (404)."""
        if not self.VIRUSTOTAL_API_KEY:
            return
        endpoint = "https://www.virustotal.com/api/v3/urls"
        headers = {"x-apikey": self.VIRUSTOTAL_API_KEY}
        data = {"url": url}
        try:
            async with async_timeout.timeout(10):
                async with self.http_session.post(endpoint, headers=headers, data=data):
                    pass  # Pas besoin de récupérer la réponse
        except Exception as e:
            self.logger.error(f"Erreur soumission VirusTotal: {e}")

    # -----------------------------------------------------
    # 8) MASQUAGE + EMBED + EXPANSION
    # -----------------------------------------------------

    def mask_dangerous(self, url: str) -> str:
        """Masque un lien jugé dangereux en remplaçant 'http' par 'hxxp'."""
        return re.sub(r"(?i)^http", "hxxp", url)

    def mask_sensitive_info(self, msg: str) -> str:
        """
        Remplace partiellement l'URL pour ne pas logguer d'info sensible en clair.
        """
        pattern = re.compile(
            r"(?i)\b((?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/[^\s]*)?"
        )
        return pattern.sub(
            lambda m: f"{m.group(1).split('.')[0]}***.{'.'.join(m.group(1).split('.')[1:])}",
            msg
        )

    def creer_embed(self, url_affiche: str, statut: str, color: int) -> discord.Embed:
        """Construit un Embed Discord récapitulant le statut d'une URL."""
        embed = discord.Embed(
            title="Analyse Defender",
            description=f"**URL analysée :** {url_affiche}",
            color=color
        )
        embed.add_field(name="Statut", value=statut, inline=False)
        embed.set_footer(text="EVO Defender© By Coca - Analysis via Safe Browsing & VirusTotal")
        return embed

    async def expand_url(self, short_url: str, max_redirects: int = 3) -> str:
        """
        Tente de suivre les redirections d'une URL shortlink ou autre,
        dans la limite de max_redirects, via des requêtes asynchrones HEAD/GET.
        Maintient un cache local TTL pour éviter de tout refaire trop souvent.
        """
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
                # Tente HEAD d'abord
                resp_head = await self.head_or_get(current_url, method="HEAD")
                if resp_head is None or not (200 <= resp_head.status < 300):
                    # si HEAD échoue, GET
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
                    # Contrôle IP locale après redirection ?
                    if BLOCK_PRIVATE_IPS:
                        host_only = urlparse(next_url).netloc.split(":")[0]
                        if self.is_private_or_local(host_only):
                            self.logger.warning(f"Refus d'une IP/host interne => {next_url}")
                            break
                    current_url = next_url
                else:
                    self.cache_expanded_urls[short_url] = (current_url, now)
                    return current_url

            except Exception as e:
                self.logger.warning(f"Erreur lors de l'expansion de shortlink: {e}")
                break

        # Au terme des redirections, on prend la dernière connue
        self.cache_expanded_urls[short_url] = (current_url, now)
        return current_url

    async def head_or_get(self, url: str, method: str = "HEAD"):
        """
        Effectue une requête HEAD ou GET asynchrone avec un timeout.
        Retourne l'objet `aiohttp.ClientResponse` ou None si erreur.
        """
        try:
            async with async_timeout.timeout(5):
                async with self.http_session.request(method, url, allow_redirects=False) as resp:
                    return resp
        except Exception as e:
            self.logger.debug(f"{method} {url} échoue: {e}")
            return None

    # -----------------------------------------------------
    # 9) FONCTION UTILE : DETECTER IP PRIVEE / LOCALHOST
    # -----------------------------------------------------

    def is_private_or_local(self, host: str) -> bool:
        """
        Vérifie si `host` est une IP locale (127.0.0.1) ou privée (RFC1918),
        ou un hostname se résolvant en IP locale.
        """
        try:
            addr = socket.gethostbyname(host)
            # Check si c'est 127.*, 10.*, 192.168.* ou 172.16-31.*
            if addr.startswith("127.") or addr.startswith("10."):
                return True
            if addr.startswith("192.168."):
                return True
            # 172.16.0.0 à 172.31.255.255
            octets = addr.split(".")
            if len(octets) == 4:
                first = int(octets[0])
                second = int(octets[1])
                if first == 172 and 16 <= second <= 31:
                    return True
            # Link-local, etc.
            if addr == "0.0.0.0" or addr == "::1":
                return True
            return False
        except Exception:
            # Si échec de résolution DNS => on ignore, on considère que c'est pas safe
            return True


async def setup(bot: commands.Bot):
    await bot.add_cog(DefenderCog(bot))
