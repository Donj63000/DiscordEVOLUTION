#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import asyncio
import collections
import random
import re
import unicodedata

from rapidfuzz.distance import Levenshtein

import discord
from discord.ext import commands, tasks
import google.generativeai as genai
from dotenv import load_dotenv

##############################################
# Constantes "globales" et fonctions utilitaires
##############################################

# Exemple de nom de salon "console" si on voulait reproduire la logique de dump,
# non obligatoire ici (contrairement à Code B qui stocke des données JSON).
CONSOLE_CHANNEL_NAME = "console"

# Intervalle de la boucle process_queue
QUEUE_PROCESS_INTERVAL = 5

def chunk_list(txt, size=2000):
    """
    Coupe une longue chaîne en morceaux de taille <= size.
    Équivalent de 'chunkify' du code original,
    mais écrit ici pour reproduire le style de Code B.
    """
    for i in range(0, len(txt), size):
        yield txt[i:i+size]

def normalize_profanity(text: str) -> str:
    """Normalise une chaîne pour la détection d'insultes."""
    # Normalisation basique et retrait des accents
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    no_diac = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")

    # Substitutions leet speak courantes
    leet_table = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})
    leet = no_diac.translate(leet_table)

    # On supprime tout sauf les lettres et chiffres
    return re.sub(r"[^a-z0-9]", "", leet)

def is_exact_match(msg: str, keyword: str) -> bool:
    """Retourne ``True`` si ``keyword`` apparaît dans ``msg`` (distance ≤ 1)."""

    norm_msg = normalize_profanity(msg)
    norm_kw = normalize_profanity(keyword)
    if not norm_kw:
        return False

    klen = len(norm_kw)
    for l in range(max(1, klen - 1), klen + 2):
        for i in range(0, len(norm_msg) - l + 1):
            part = norm_msg[i : i + l]
            if Levenshtein.distance(part, norm_kw) <= 1:
                return True
    return False

##############################################
# Listes de mots-clés / intentions
##############################################

HUMOR_KEYWORDS = [
    "haha","lol","mdr","ptdr","xD","xd","🤣","😂","😅","😆",
    "trop drôle","c'est drôle","excellent","jpp","marrant",
    "mort de rire","je rigole","ça me tue","hilarant","énorme",
    "plié","trop fort","trop marrant","c'est fun","wtf",
    "explosé","je suis mort","dead","gros fou rire","je suis plié",
    "mdrrr","ptdrrr","loool","mdrrrr","ptdrrrr",
    "pété de rire","ça m'a tué","rigolade","rigole fort","délire",
    "je pleure","j'en peux plus","je suffoque","trop bon","mdrrrrrr",
    "trop vrai","rire aux éclats","cette barre","fou rire","mdr 😂",
    "pété","c'est abusé","mdrrrrrrr","ptdrrrrrrr","lolilol",
    "j'en peux vraiment plus","c'est magique","la crise","l'éclate",
    "complètement mort","je suis décédé","au bout de ma vie","très très drôle",
    "j'ai explosé","mécroulé","mdrrrrrrrrr","énormissime","exceptionnel"
]

SARCASM_KEYWORDS = [
    "sarcasme","ironie","sarcastique","ironique","bien sûr",
    "évidemment","comme par hasard","sans blague","tu m'étonnes",
    "c'est ça ouais","bravo champion","mais bien sûr","quel génie",
    "je suis impressionné","quelle surprise","incroyable","tu crois ?",
    "ça se voit pas du tout","c’est évident","noooon sans rire",
    "étonnant","magnifique","brillant","du grand art","bah voyons",
    "génial","c'est sûr","comme c'est étonnant","tu parles",
    "wow incroyable","ah oui vraiment ?","sérieux ?",
    "mais oui bien sûr","on y croit","franchement ?","tellement logique",
    "c'est clair","je n'aurais jamais deviné","quelle originalité",
    "quel talent","jamais vu ça","grandiose","ma-gni-fi-que",
    "quelle intelligence","ça m'étonne même pas","quel exploit",
    "ça alors","tu m'en diras tant","extraordinaire","formidable vraiment",
    "superbe logique","on applaudit","ça promet","ah bah tiens",
    "super original","bravo Einstein"
]

LIGHT_PROVOCATION_KEYWORDS = [
    "noob","1v1","t'es nul","même pas cap","petit joueur","facile",
    "ez","easy","tu fais quoi là","débutant","faible","peureux",
    "lâche","viens te battre","c'est tout ?","tu crains","trop facile",
    "pas de niveau","tu dors ?","t'es où ?","va t'entraîner",
    "t'as peur","tu fais pitié","ramène-toi","petite nature",
    "niveau zéro","on t'attend","viens","faiblard","fragile",
    "boulet","t'es éclaté","niveau débutant","c'est faible",
    "tu vaux rien","tu stresses ?","viens tester","tu fuis ?",
    "ça joue petit bras","on t'entend plus","je t'attends",
    "t'es pas prêt","je m'ennuie là","pas terrible","t'as craqué",
    "je pensais mieux","mou du genou","viens voir","joue mieux",
    "arrête le massacre","c'est gênant","reviens quand tu seras prêt",
    "t'es perdu ?","tu t'en sors ?","pathétique","petit bras","trop lent",
    "fatigué ?","t'es à la ramasse"
]

SERIOUS_INSULT_KEYWORDS = [
    "connard","enfoiré","fdp","fils de pute","pute","salope",
    "ta mère","bâtard","enculé","sous-merde","ordure","abruti",
    "con","trou du cul","abruti fini","crétin","débile","demeuré",
    "mongol","attardé","gros porc","grosse merde","sale chien",
    "chien","clochard","déchet","pauvre type","minable","raté",
    "sombre merde","vieux con","grosse pute","sous-race","cafard",
    "pauvre merde","sac à merde","pauvre con","sale merde",
    "fumier","parasite","toxico","gros naze","enculé de ta race",
    "fils de chien","tête de cul","sale pute","putain","sous-homme",
    "abruti congénital","grosse raclure","pourriture","grosse ordure",
    "misérable","rat d'égout","sangsue","sale ordure","vermine",
    "détraqué","fou furieux","tête de noeud","tg","ta gueule"
]

DISCRIMINATION_KEYWORDS = [
    "raciste","racisme","nègre","negro","bougnoule","chinetoque",
    "bridé","pédé","tapette","tarlouze","goudou","pd",
    "sale arabe","sale juif","youpin","feuj","sale noir",
    "sale blanc","sale asiat","sale chinois","sale homo",
    "sale gay","handicapé","mongolien","autiste",
    "sale musulman","terroriste","sale renoi","rebeu","sale rebeu",
    "babtou","sale babtou","niaque","trisomique","retardé",
    "bouffeur de porc","sale pédale","sale gouine","bicot",
    "sale hindou","négresse","beurrette","sale polak",
    "sale rom","gitano","manouche","sale catho","sale athée",
    "sale mécréant","sale pakpak","bougnoulisation",
    "boucaque","cafre","negresse","sale migrant","barbu",
    "sale chrétien","sale protestant","sale bouddhiste"
]

THREAT_KEYWORDS = [
    "je vais te tuer","je vais t'éclater","je vais te frapper",
    "fais gaffe à toi","menace","t'es mort","je vais te défoncer",
    "tu vas voir","fais attention à toi","tu vas le regretter",
    "je vais te casser la gueule","je vais te faire mal",
    "attention à toi","je sais où tu habites","ça va mal finir",
    "tu vas prendre cher","tu vas payer","tu vas souffrir",
    "gare à toi","prépare-toi à souffrir","ça va chauffer",
    "je te retrouve","je vais te retrouver","tu vas comprendre",
    "tu vas morfler","je vais m'occuper de toi","tu vas pleurer",
    "je te démonte","tu vas déguster","je vais te régler ton compte",
    "fini pour toi","tu vas crever","tu vas saigner","je vais te massacrer",
    "tu vas en baver","tu vas regretter","ta vie est finie",
    "je vais te terminer","tu ne t'en sortiras pas","je vais te briser",
    "tu vas ramasser","je te promets l'enfer","je vais te détruire",
    "tu vas périr","tu vas t'en souvenir","c'est la fin pour toi",
    "tu vas tomber","tu ne verras pas demain","tu vas disparaître"
]

EMOJIS_FRIENDLY = ["😄","😉","🤗","🥳","🙂"]
EMOJIS_FIRM = ["😠","🙅","🚫","⚠️","😡"]

TONE_VARIATIONS = {
    "humor": [
        "Réponse humoristique, conviviale",
        "Réponds sur un ton joyeux et détendu",
        "Fais une remarque légère, agrémentée d'un soupçon de dérision amicale"
    ],
    "sarcasm": [
        "Ton ironique, garde une pointe de second degré",
        "Un brin d'ironie, sans vexer",
        "Réponds de façon un peu sarcastique mais restes subtil"
    ],
    "light_provocation": [
        "Provocation légère, reste calme et joueur",
        "Ton défi léger, sans escalade",
        "Réplique avec un esprit compétitif bon enfant"
    ],
    "serious_insult": [
        "Insulte grave, réponds calmement et signale poliment le règlement",
        "Langage inapproprié, demande de rester respectueux",
        "Montre ton désaccord sans agressivité, rappelle que ce n’est pas toléré"
    ],
    "discrimination": [
        "Propos discriminatoires, rappelle que c'est interdit ici",
        "Réponse ferme, mentionne les règles contre la discrimination",
        "Signale que ces propos ne sont pas tolérés et renvoie au règlement"
    ],
    "threat": [
        "Menace détectée, réponds avec fermeté et rappelle la charte",
        "Alerte menace, mentionne qu’on ne tolère aucune intimidation",
        "Menace claire, indique que cela viole les règles de respect"
    ],
    "neutral": [
        "Réponse chaleureuse et neutre",
        "Ton classique, cordial et empathique",
        "Réponds poliment, sur un ton neutre et bienveillant"
    ]
}

USER_STYLES = ["affectueux","direct","enthousiaste"]

##############################################
# Classe Cog gérant la logique IA
##############################################

class IACog(commands.Cog):
    """
    Cog gérant l'Intelligence Artificielle (réponses en langage naturel, etc.).
    Inspiré du Code B : utilisation de cog_load pour l'initialisation,
    fonctions chunk_list globales, etc.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Définition ici des variables essentielles qui existeront
        # après l’initialisation dans cog_load()
        # Nombre de messages du canal conservés pour le contexte des réponses IA
        self.history_limit = 100
        self.max_prompt_size = 5000
        self.quota_block_duration = 3600
        self.quota_exceeded_until = 0
        self.debug_mode = True

        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"

        # Éléments pour la gestion du règlement
        self.last_reglement_reminder = 0
        self.reglement_cooldown = 600

        # Contextes utilisateurs
        self.user_contexts = {}
        # Anti-spam
        self.spam_times = {}
        self.spam_interval = 5
        self.spam_threshold = 4

        # Queue de requêtes IA
        self.request_queue = collections.deque()
        self.pending_requests = False

        # Customisation utilisateur
        self.user_styles = {}

        # Warnings, mute, etc. (non utilisé dans la démo)
        self.warning_limit = 3
        self.mute_duration = 600

        # Les attributs ci-dessous seront configurés dans self.initialize_ia()
        self.logger = None
        self.api_key = None
        self.model_pro = None
        self.model_flash = None
        self.knowledge_text = ""

        # On lance la loop "process_queue" (mais elle démarrera
        # réellement après le cog_load, selon Discord.py).
        self.process_queue.start()

    async def cog_load(self):
        """
        Méthode appelée automatiquement par discord.py quand le Cog est chargé.
        Similaire à l’initialize_data() de Code B : ici on configure l’IA, etc.
        """
        await self.initialize_ia()

    async def initialize_ia(self):
        """
        Initialisation : configuration du logging, chargement de la clé .env,
        préparation de l'IA, etc.
        """
        self.configure_logging()
        self.configure_gemini()
        self.knowledge_text = self.get_knowledge_text()
        self.logger.info("IACog initialisé avec succès.")

    def configure_logging(self):
        """
        Configure le logging (niveau DEBUG si self.debug_mode, sinon INFO).
        """
        lvl = logging.DEBUG if self.debug_mode else logging.INFO
        logging.basicConfig(level=lvl, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.logger = logging.getLogger("IACog")

    def configure_gemini(self):
        """
        Charge la clé d'API et prépare les modèles Generative AI.
        """
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing GEMINI_API_KEY dans .env.")
        genai.configure(api_key=self.api_key)
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

    def get_knowledge_text(self) -> str:
        """
        Renvoie un texte 'de connaissance' (règlement, etc.) qui sera utilisé
        dans la construction de prompts IA.
        """
        return (
            "RÈGLEMENT OFFICIEL DE LA GUILDE EVOLUTION – Édition du 19/02/2025\n\n"
            "“Ensemble, nous évoluerons plus vite que seuls.”\n\n"
            "Bienvenue au sein de la guilde Evolution ! Nous sommes heureux de t’accueillir "
            "dans notre communauté. Ce règlement est conçu pour assurer une ambiance conviviale, "
            "respectueuse et motivante, tout en permettant à chacun de progresser selon son rythme "
            "et ses envies. [...] \n\n"
            "=====================================================================\n"
            "(Texte complet du règlement, inchangé par souci de concision)\n"
            "=====================================================================\n"
            "LISTE DES COMMANDES DU BOT EVOLUTION\n"
            "=====================================================================\n"
            "• !ia pour revoir ce guide\n"
            "• !bot <message>\n"
            "• !analyse\n"
            "• !annonce <texte> (Staff)\n"
            "• !event <texte> (Staff)\n"
            "• !pl <texte>\n"
            "etc.\n"
        )

    @tasks.loop(seconds=QUEUE_PROCESS_INTERVAL)
    async def process_queue(self):
        """
        Boucle périodique qui traite la file de requêtes IA (self.request_queue).
        """
        # On ne traite la file que si on n'est plus bloqué par un quota
        # et s'il y a des requêtes en attente.
        if self.pending_requests and time.time() >= self.quota_exceeded_until:
            while self.request_queue:
                ctx, prompt_callable = self.request_queue.popleft()
                try:
                    await prompt_callable(ctx)
                except Exception as e:
                    self.logger.warning(f"Erreur dans process_queue : {e}")
            self.pending_requests = False

    def cog_unload(self):
        """
        Si jamais on décharge le Cog, on arrête la boucle.
        """
        self.process_queue.cancel()

    ##############################################
    # Fonctions de détection d'intention
    ##############################################

    def detect_intention(self, msg: str) -> str:
        """
        Détecte l'intention (humor, sarcasm, light_provocation, serious_insult,
        discrimination, threat, ou neutral) selon les mots-clés.
        """
        for kw in SERIOUS_INSULT_KEYWORDS:
            if is_exact_match(msg, kw):
                return "serious_insult"

        for kw in DISCRIMINATION_KEYWORDS:
            if is_exact_match(msg, kw):
                return "discrimination"

        for kw in THREAT_KEYWORDS:
            if is_exact_match(msg, kw):
                return "threat"

        for kw in LIGHT_PROVOCATION_KEYWORDS:
            if is_exact_match(msg, kw):
                return "light_provocation"

        for kw in HUMOR_KEYWORDS:
            if is_exact_match(msg, kw):
                return "humor"

        for kw in SARCASM_KEYWORDS:
            if is_exact_match(msg, kw):
                return "sarcasm"

        return "neutral"

    ##############################################
    # Fonctions d'appel à l'API IA
    ##############################################

    async def generate_content_async(self, model, prompt: str):
        """
        Appel synchrone du modèle, exécuté dans un thread (run_in_executor).
        """
        loop = asyncio.get_running_loop()

        def sync_call():
            return model.generate_content(prompt)

        return await loop.run_in_executor(None, sync_call)

    async def generate_content_with_fallback_async(self, prompt: str):
        """
        Tente d'abord le modèle PRO, puis bascule sur FLASH en cas d'erreur de quota (429).
        """
        try:
            r = await self.generate_content_async(self.model_pro, prompt)
            return r, "PRO"
        except Exception as e1:
            if any(x in str(e1).lower() for x in ["429","quota","unavailable"]):
                # Fallback sur le modèle FLASH
                try:
                    r2 = await self.generate_content_async(self.model_flash, prompt)
                    return r2, "FLASH"
                except Exception as e2:
                    if "429" in str(e2):
                        self.quota_exceeded_until = time.time() + self.quota_block_duration
                    raise e2
            else:
                raise e1

    ##############################################
    # Commandes utilisateur et logique IA
    ##############################################

    @commands.command(name="ia")
    async def ia_help_command(self, ctx):
        """
        Commande !ia : affiche le guide sur l'IA.
        """
        txt = (
            "**Commandes IA :**\n"
            "!annonce <texte> (Staff)\n"
            "!analyse\n"
            "!bot <message>\n"
            "!event <texte> (Staff)\n"
            "!pl <texte>\n"
            "Mentionnez @EvolutionBOT pour solliciter l'IA\n"
            "!ia pour revoir ce guide"
        )
        await ctx.send(txt)

    @commands.command(name="bot")
    async def free_command(self, ctx, *, user_message=None):
        """
        Commande !bot <message> : envoie la requête à l'IA.
        """
        if not user_message:
            await ctx.send("Usage : `!bot <votre question>`")
            return

        # Si on est en blocage de quota, on place la requête en file.
        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.handle_ai_request(co, user_message)))
            self.pending_requests = True
            return

        await self.handle_ai_request(ctx, user_message)

    async def handle_ai_request(self, ctx, user_message: str):
        """
        Logique commune pour traiter la requête IA (anti-spam, intention, etc.).
        """
        now = time.time()
        uid = ctx.author.id

        # Historique utilisateur
        if uid not in self.user_contexts:
            self.user_contexts[uid] = collections.deque(maxlen=50)

        # Anti-spam
        if uid not in self.spam_times:
            self.spam_times[uid] = []
        self.spam_times[uid].append(now)
        self.spam_times[uid] = [t for t in self.spam_times[uid] if now - t < self.spam_interval]
        if len(self.spam_times[uid]) > self.spam_threshold:
            await ctx.send("Tu sembles spammer le bot. Merci de ralentir.")
            return

        # Détection d'intention
        intention = self.detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)
        style_user = self.user_styles.get(uid, "neutre")

        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emo = random.choice(EMOJIS_FRIENDLY)
        else:
            emo = random.choice(EMOJIS_FIRM)

        mention_reglement = ""
        if intention in ["serious_insult","discrimination","threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"
                self.last_reglement_reminder = now

        # Contexte complet
        st = (
            f"Tu es EvolutionBOT, assistant de la guilde. "
            f"L'utilisateur a un style '{style_user}'. "
            f"{chosen_tone} {emo}{mention_reglement}"
        )

        user_history = list(self.user_contexts[uid])
        user_history.append(user_message)
        self.user_contexts[uid] = collections.deque(user_history, maxlen=50)

        # Récupération d'historique du channel
        channel_history = []
        async for m in ctx.channel.history(limit=self.history_limit):
            if not m.author.bot:
                channel_history.append(m)
        channel_history.sort(key=lambda x: x.created_at)
        hist_txt = "".join(f"{m.author.display_name}: {m.content}\n" for m in channel_history)

        # Construction du prompt final
        final_prompt = (
            f"{st}\n\n"
            f"knowledge_text:\n{self.knowledge_text}\n\n"
            f"Contexte({self.history_limit}):\n{hist_txt}\n\n"
            f"Message de {ctx.author.display_name}: {user_message}"
        )

        # Tronquer si prompt trop long
        if len(final_prompt) > self.max_prompt_size:
            surplus = len(final_prompt) - self.max_prompt_size
            if surplus < len(hist_txt):
                hist_txt = hist_txt[surplus:]
            else:
                hist_txt = "(Contexte tronqué)"

            final_prompt = (
                f"{st}\n\n"
                f"knowledge_text:\n{self.knowledge_text}\n\n"
                f"{hist_txt}\n\n"
                f"Message de {ctx.author.display_name}: {user_message}"
            )

        # Appel IA
        try:
            resp, model_used = await self.generate_content_with_fallback_async(final_prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                for c in chunk_list(rep):
                    await ctx.send(c)
            else:
                await ctx.send("Aucune réponse de l'IA.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("**Quota IA dépassé**, réessayez plus tard.")
            else:
                await ctx.send(f"Erreur IA: {e}")

    @commands.command(name="analyse")
    async def analyse_command(self, ctx):
        """
        Commande !analyse : demande un résumé des derniers messages du channel.
        Supprime le message d'origine pour discrétion.
        """
        lim = 100
        messages = []
        async for m in ctx.channel.history(limit=lim):
            if not m.author.bot:
                messages.append(m)
        messages.sort(key=lambda x: x.created_at)

        st = "Tu es EvolutionBOT, fais un rapport neutre sur les derniers messages (ambiance, conflits)."
        joined = "".join(f"{x.author.display_name}: {x.content}\n" for x in messages)
        pr = f"{st}\n{joined}"

        try:
            await ctx.message.delete()
        except:
            pass

        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.analyse_fallback(co, pr)))
            self.pending_requests = True
            return

        await self.analyse_fallback(ctx, pr)

    async def analyse_fallback(self, ctx, prompt: str):
        """
        Version fallback (mise en file éventuelle) de l'analyse.
        """
        try:
            resp, model_used = await self.generate_content_with_fallback_async(prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                for c in chunk_list(rep):
                    await ctx.send(c)
            else:
                await ctx.send("Aucune réponse d'analyse.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("**Quota dépassé**.")
            else:
                await ctx.send(f"Erreur: {e}")

    @commands.has_role("Staff")
    @commands.command(name="annonce")
    async def annonce_command(self, ctx, *, user_message=None):
        """
        Commande !annonce <texte> (Staff) : crée une annonce dans #annonces.
        """
        if not user_message:
            await ctx.send("Usage: !annonce <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name=self.annonce_channel_name)
        if not chan:
            await ctx.send("Canal 'annonces' introuvable.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.annonce_fallback(co, chan, user_message)))
            self.pending_requests = True
            return

        await self.annonce_fallback(ctx, chan, user_message)

    async def annonce_fallback(self, ctx, chan, user_message: str):
        """
        Génère un message d'annonce (tag @everyone) de façon IA.
        """
        st = "Tu es EvolutionBOT, crée une annonce sympathique sans trop d'humour et commence par '@everyone'."
        pr = f"{st}\n{user_message}"
        try:
            await ctx.message.delete()
        except:
            pass

        try:
            resp, model_used = await self.generate_content_with_fallback_async(pr)
            if resp and hasattr(resp, "text"):
                final = resp.text.strip() or "(vide)"
                await chan.send(f"**Annonce [{model_used}] :**")
                for c in chunk_list(final):
                    await chan.send(c)
            else:
                await ctx.send("Pas d'annonce générée.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("Quota dépassé.")
            else:
                await ctx.send(str(e))

    @commands.has_role("Staff")
    @commands.command(name="event")
    async def event_command(self, ctx, *, user_message=None):
        """
        Commande !event <texte> (Staff) : crée un événement dans #organisation.
        """
        if not user_message:
            await ctx.send("Usage: !event <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name=self.event_channel_name)
        if not chan:
            await ctx.send("Canal d'organisation introuvable.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.event_fallback(co, chan, user_message)))
            self.pending_requests = True
            return

        await self.event_fallback(ctx, chan, user_message)

    async def event_fallback(self, ctx, chan, user_message: str):
        """
        Génère un message d'événement (invitation).
        """
        st = "Tu es EvolutionBOT, rédige une invitation d'événement incitant à participer."
        pr = f"{st}\n\n{user_message}"
        try:
            await ctx.message.delete()
        except:
            pass

        try:
            resp, model_used = await self.generate_content_with_fallback_async(pr)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                await chan.send(f"**Nouvel Événement [{model_used}] :**")
                for c in chunk_list(rep):
                    await chan.send(c)
                role_val = discord.utils.get(ctx.guild.roles, name="Membre validé d'Evolution")
                if role_val:
                    await chan.send(role_val.mention)
            else:
                await ctx.send("Événement non généré.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("Quota dépassé.")
            else:
                await ctx.send(str(e))

    @commands.command(name="pl")
    async def pl_command(self, ctx, *, user_message=None):
        """
        Commande !pl <texte> : génère une annonce de PL dans le canal xplock-rondesasa-ronde.
        """
        if not user_message:
            await ctx.send("Usage: !pl <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name=self.pl_channel_name)
        if not chan:
            await ctx.send("Canal introuvable pour PL.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.pl_fallback(co, chan, user_message)))
            self.pending_requests = True
            return

        await self.pl_fallback(ctx, chan, user_message)

    async def pl_fallback(self, ctx, chan, user_message: str):
        """
        Génère un message d'annonce de PL.
        """
        st = "Tu es EvolutionBOT, rédige une annonce de PL claire et motivante."
        pr = f"{st}\n\n{user_message}"
        try:
            await ctx.message.delete()
        except:
            pass

        try:
            resp, model_used = await self.generate_content_with_fallback_async(pr)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                await chan.send(f"**Nouvelle Annonce PL [{model_used}] :**")
                for c in chunk_list(rep):
                    await chan.send(c)
            else:
                await ctx.send("Pas de réponse IA pour PL.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("Quota dépassé.")
            else:
                await ctx.send(str(e))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Listener qui intercepte les mentions directes du bot.
        """
        # Ignorer les messages du bot
        if message.author.bot:
            return

        # On récupère le contexte, on vérifie si c'est une commande
        c = await self.bot.get_context(message)
        if c.valid and c.command:
            return

        # Si la mention du bot est dedans, on traite comme un appel IA
        if self.bot.user.mention in message.content:
            q = message.content.replace(self.bot.user.mention, "").strip()
            if q:
                if time.time() < self.quota_exceeded_until:
                    qlen = len(self.request_queue)
                    await c.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
                    self.request_queue.append((c, lambda co: self.handle_ai_request(co, q)))
                    self.pending_requests = True
                    return
                await self.handle_ai_request(c, q)


##############################################
# Configuration d'extension
##############################################

async def setup(bot: commands.Bot):
    """
    Méthode appelée par bot.load_extension(...).
    Ajoute simplement le IACog au bot.
    """
    await bot.add_cog(IACog(bot))
