#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import base64
import sqlite3
import logging
from urllib.parse import urlparse

import asyncio  # <-- pour pouvoir faire un sleep asynchrone
import requests
import validators
import discord
from discord.ext import commands
from cryptography.fernet import Fernet

URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

class DefenderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.GOOGLE_SAFE_BROWSING_API_KEY = os.getenv("GSB_API_KEY")
        self.VIRUSTOTAL_API_KEY = os.getenv("VT_API_KEY")
        
        # Liste blanche de domaines (vide ici, à remplir si besoin)
        self.LISTE_BLANCHE_DOMAINE = []

        # Gestions clés et fichiers
        self.KEY_FILE = "secret.key"
        if not os.path.exists(self.KEY_FILE):
            self.key = Fernet.generate_key()
            with open(self.KEY_FILE, "wb") as f:
                f.write(self.key)
            logging.info("Nouvelle clé de chiffrement générée (Defender).")
        else:
            with open(self.KEY_FILE, "rb") as f:
                self.key = f.read()
            logging.info("Clé de chiffrement chargée (Defender).")
        self.fernet = Fernet(self.key)

        self.DB_FILENAME = "historique_defender.db"
        self.LOG_FILENAME = "defender_discord.log"

        self.initialiser_logging()
        self.initialiser_db()
        self.securiser_fichiers()
        logging.info("DefenderCog initialisé avec succès.")

    def initialiser_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler(self.LOG_FILENAME, encoding="utf-8"),
                logging.StreamHandler()
            ]
        )

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
            logging.info("Base de données Defender initialisée.")
        except sqlite3.Error as e:
            logging.error(f"Erreur init DB: {e}")

    def securiser_fichiers(self):
        # Sur Unix, on limite les permissions aux seuls utilisateurs autorisés
        if os.name != "nt":
            try:
                os.chmod(self.LOG_FILENAME, 0o600)
                os.chmod(self.DB_FILENAME, 0o600)
                logging.info("Fichiers Defender sécurisés (Unix).")
            except Exception as e:
                logging.error(f"Erreur chmod (Defender): {e}")

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
            logging.info(f"[Historique] {self.mask_sensitive_info(url)} => {statut}")
        except sqlite3.Error as e:
            logging.error(f"Erreur BD (historique): {e}")

    @commands.command(name="scan", help="Analyse un lien (Safe Browsing + VirusTotal) et supprime le message de commande.")
    async def scan_command(self, ctx, *, url: str = None):
        """
        Commande !scan <URL> : supprime le message de commande et
        effectue une analyse complète sur l'URL.
        """
        # On tente de supprimer le message original de la commande
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            logging.warning("Permission manquante pour supprimer le message de commande.")

        if not url:
            await ctx.send("Usage : `!scan <URL>`", delete_after=10)
            return

        # Analyse asynchrone (1er passage + éventuel second passage si besoin)
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
        # 1) On ignore les messages des bots
        if message.author.bot:
            return

        # 2) Recherche de tous les liens dans le message
        found_urls = URL_REGEX.findall(message.content)
        if not found_urls:
            # Aucun lien => rien à faire, on sort
            return

        new_content = message.content
        results = []
        worst_status = None

        # 3) Analyse de chaque URL de manière asynchrone
        for raw_url in found_urls:
            statut, color, url_affiche = await self.analyser_url(raw_url)
            if statut is None:
                # L'URL n'est pas valide ou pas supportée
                continue

            results.append((raw_url, statut, url_affiche))

            # Si l'URL est jugée dangereuse, on remplace dans le message
            if "DANGEREUX" in statut:
                new_content = new_content.replace(raw_url, "[dangerous link removed]")

            # On détermine le pire statut rencontré (sûr < indéterminé < dangereux)
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
                if severity == 2:
                    worst_status = "DANGEREUX"
                elif severity == 1:
                    worst_status = "INDÉTERMINÉ"
                else:
                    worst_status = "SÛR"

        # 4) Si on a trouvé au moins un lien valide, on édite le message si besoin
        if not results:
            return  # Aucun lien analysable

        if new_content != message.content:
            try:
                await message.edit(content=new_content)
            except discord.Forbidden:
                logging.warning("Impossible d'éditer le message => permissions manquantes.")
            except discord.HTTPException as e:
                logging.warning(f"Erreur lors de l'édition du message: {e}")

        # 5) Choix de la couleur de l'embed en fonction du pire statut
        if worst_status == "DANGEREUX":
            final_color = 0xE74C3C
        elif worst_status == "INDÉTERMINÉ":
            final_color = 0xF1C40F
        else:
            final_color = 0x2ECC71

        # 6) On construit un embed récapitulant les analyses
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

        # 7) On répond au message original (reply) sans notifier l’auteur
        await message.reply(embed=embed, mention_author=False)

    async def analyser_url(self, raw_url: str, second_pass: bool = False):
        """
        Analyse asynchrone d'une URL en s'appuyant sur Google Safe Browsing et VirusTotal.
        Fait un second passage après ~5s si le statut est indéterminé (et que ce n'est pas déjà un second passage).
        
        Retourne (statut, color, url_affiche) ou (None, None, None) si invalide.
        """
        url_nettoyee, whitelisted = self.valider_et_nettoyer_url(raw_url)
        if not url_nettoyee:
            return None, None, None

        # Si domaine en liste blanche : statut direct "Sûr"
        if whitelisted:
            statut = "SÛR (Liste Blanche)"
            color = 0x2ECC71
            self.enregistrer_historique(url_nettoyee, statut)
            return statut, color, url_nettoyee

        # Vérification Safe Browsing
        est_sure_sb, _ = self.verifier_url_safe_browsing(url_nettoyee)
        # Vérification VirusTotal
        est_sure_vt, _ = self.verifier_url_virustotal(url_nettoyee)

        # Détermination du statut final
        if est_sure_sb is False or est_sure_vt is False:
            statut = "DANGEREUX ⚠️"
            color = 0xE74C3C
        elif est_sure_sb is True and est_sure_vt is True:
            statut = "SÛR ✅"
            color = 0x2ECC71
        else:
            # Cas indéterminé (None, ou un mélange True/None)
            statut = "INDÉTERMINÉ ❓"
            color = 0xF1C40F

        # Si c'est indéterminé au premier passage, on attend 5s et on réessaie une fois
        if ("INDÉTERMINÉ" in statut) and (not second_pass):
            logging.info(f"Statut indéterminé pour {url_nettoyee}. Nouvelle tentative dans 5s...")
            await asyncio.sleep(5)  # On attend 5 secondes
            return await self.analyser_url(raw_url, second_pass=True)

        # Enregistrement en base
        self.enregistrer_historique(url_nettoyee, statut)

        # Si l’URL est dangereuse, on remplace le « http » par « hxxp »
        url_affiche = self.mask_dangerous(url_nettoyee) if "DANGEREUX" in statut else url_nettoyee
        return statut, color, url_affiche

    def valider_et_nettoyer_url(self, url: str):
        """
        Vérifie si l'URL est correctement formée, commence par http/https,
        et si elle appartient éventuellement à la liste blanche.
        """
        url = url.strip()
        if not self.est_url_valide(url):
            return None, False
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, False

        domain = parsed.netloc.lower().split(':')[0]
        whitelisted = any(
            domain == w or domain.endswith("." + w)
            for w in self.LISTE_BLANCHE_DOMAINE
        )
        return url, whitelisted

    def est_url_valide(self, url: str) -> bool:
        """Vérifie via validators si l'URL est formellement valide et ne contient pas de code malveillant."""
        if not validators.url(url):
            return False
        if len(url) > 2048:
            return False
        if self.contient_scripts_malveillants(url):
            return False
        return True

    def contient_scripts_malveillants(self, url: str) -> bool:
        """Détection simple de patterns JavaScript malveillants dans l'URL."""
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

    def verifier_url_safe_browsing(self, url: str):
        """
        Vérification via Google Safe Browsing. 
        Retourne (True, None) si sûre, (False, data) si match négatif, 
        (None, None) si impossible de conclure (ex: pas de clé).
        """
        if not self.GOOGLE_SAFE_BROWSING_API_KEY:
            return None, None
        endpoint = 'https://safebrowsing.googleapis.com/v4/threatMatches:find'
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
        params = {'key': self.GOOGLE_SAFE_BROWSING_API_KEY}
        try:
            resp = requests.post(endpoint, params=params, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if "matches" in data:
                return False, data["matches"]
            return True, None
        except (requests.RequestException, json.JSONDecodeError):
            return None, None

    def verifier_url_virustotal(self, url: str):
        """
        Vérification via l'API VirusTotal.
        Retourne (True, None) si sûre, (False, data) si rapport négatif,
        (None, None) si pas de clé ou erreur/timeout.
        """
        if not self.VIRUSTOTAL_API_KEY:
            return None, None
        try:
            # Encodage de l'URL pour l'endpoint VT (Base64 URL-safe, sans '=' final)
            url_b64 = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
            endpoint = f"https://www.virustotal.com/api/v3/urls/{url_b64}"
            headers = {'x-apikey': self.VIRUSTOTAL_API_KEY}
            resp = requests.get(endpoint, headers=headers, timeout=10)

            # Si l'URL n'existe pas encore dans la base, on la soumet puis on renvoie None
            if resp.status_code == 404:
                self.soumettre_virustotal(url)
                return None, None

            resp.raise_for_status()
            data = resp.json()
            stats = data['data']['attributes']['last_analysis_stats']
            malicious = stats.get('malicious', 0)
            suspicious = stats.get('suspicious', 0)

            if malicious > 0 or suspicious > 0:
                return False, data
            return True, None
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            logging.error(f"Erreur VirusTotal: {e}")
            return None, None

    def soumettre_virustotal(self, url: str):
        """
        Soumet l'URL à VirusTotal quand on ne la trouve pas (404).
        """
        try:
            endpoint = 'https://www.virustotal.com/api/v3/urls'
            headers = {'x-apikey': self.VIRUSTOTAL_API_KEY}
            data = {'url': url}
            requests.post(endpoint, headers=headers, data=data, timeout=10)
        except Exception as e:
            logging.error(f"Erreur soumission VirusTotal: {e}")

    def mask_dangerous(self, url: str) -> str:
        """
        Masque un lien jugé dangereux en remplaçant 'http' par 'hxxp'.
        """
        return re.sub(r'(?i)^http', 'hxxp', url)

    def mask_sensitive_info(self, msg: str) -> str:
        """
        Remplace partiellement l'URL pour ne pas logguer d'info sensible en clair.
        """
        pattern = re.compile(
            r'(?i)\b((?:https?://)?(?:www\.)?[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?:/[^\s]*)?'
        )
        return pattern.sub(
            lambda m: f"{m.group(1).split('.')[0]}***.{'.'.join(m.group(1).split('.')[1:])}",
            msg
        )

    def creer_embed(self, url_affiche: str, statut: str, color: int) -> discord.Embed:
        """
        Construit un Embed Discord récapitulant le statut d'une URL.
        """
        embed = discord.Embed(
            title="Analyse Defender",
            description=f"**URL analysée :** {url_affiche}",
            color=color
        )
        embed.add_field(name="Statut", value=statut, inline=False)
        embed.set_footer(text="EVO Defender© By Coca - Analysis via Safe Browsing & VirusTotal")
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(DefenderCog(bot))
