#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import math
import logging
import asyncio
import collections
import random
import re
import concurrent.futures  # Pour limiter le pool de threads

import discord
from discord.ext import commands, tasks
import google.generativeai as genai
from dotenv import load_dotenv

#
# 1) Listes de mots-clés et regex compilées pour détecter l'intention
#
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


def sort_keywords_desc(words):
    """Trie la liste par longueur décroissante."""
    return sorted(words, key=len, reverse=True)

_PATTERNS_RAW = {
    "serious_insult": SERIOUS_INSULT_KEYWORDS,
    "discrimination": DISCRIMINATION_KEYWORDS,
    "threat": THREAT_KEYWORDS,
    "light_provocation": LIGHT_PROVOCATION_KEYWORDS,
    "humor": HUMOR_KEYWORDS,
    "sarcasm": SARCASM_KEYWORDS,
}

# Compilation des regex
_COMPILED_PATTERNS = {}
for label, kws in _PATTERNS_RAW.items():
    sorted_kws = sort_keywords_desc(kws)
    _COMPILED_PATTERNS[label] = re.compile(
        rf"\b({'|'.join(map(re.escape, sorted_kws))})\b",
        re.IGNORECASE
    )

def detect_intention(msg: str) -> str:
    """
    Détection des intentions via regex compilées.
    Note: Approximatif, se base sur l'ordre (serious_insult > discrimination > threat...).
    """
    cleaned = re.sub(r"[^a-z0-9\s]", " ", msg.lower())
    for label, pattern in _COMPILED_PATTERNS.items():
        if pattern.search(cleaned):
            return label
    return "neutral"


#
# 2) Tonalités, prompts système
#
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

# Blocs de prompts spécifiques selon la commande
PROMPT_BASES = {
    "bot": (
        "Tu réponds à la question de l’utilisateur en **français** ; "
        "structure‑toi ainsi :\n"
        "1. Phrase d’accroche (confirmation ou courte reformulation).\n"
        "2. Explication claire et concise du sujet (≤ 5 phrases).\n"
        "3. Suggestion ou question d’ouverture pour poursuivre la discussion.\n"
        "Si la demande enfreint le règlement, refuse poliment en citant la règle."
    ),
    "analyse": (
        "Rédige un **compte‑rendu neutre** des 20 derniers messages :\n"
        "• Atmosphère générale\n"
        "• Thèmes principaux\n"
        "• Signes éventuels de tension ou conflit\n"
        "Conclue par **une seule** proposition constructive pour améliorer l’échange."
    ),
    "annonce": (
        "Rédige une **annonce officielle** (pings autorisés) :\n"
        "1. Accroche percutante (≤ 120 car.)\n"
        "2. 2 ou 3 points clés sous forme de liste « • »\n"
        "3. Appel à l’action clair avec date/heure ou canal dédié\n"
        "Ton chaleureux, inclusif, **sans emoji**."
    ),
    "event": (
        "Rédige une **invitation d’événement** enthousiasmante :\n"
        "• Nom de l’activité en **gras**\n"
        "• Date + heure (format JJ/MM – HHh)\n"
        "• Objectif principal\n"
        "• Prérequis éventuels (niveau, stuff…)\n"
        "Termine par : « Réservez votre place dans #organisation ! »."
    ),
    "pl": (
        "Formule une **demande de Power‑Levelling** structurée :\n"
        "• Nombre de places recherchées\n"
        "• Tranches de niveaux concernées\n"
        "• Récompenses proposées (kamas, loot…)\n"
        "• Plage horaire souhaitée\n"
        "Conclue par un court message motivant."
    ),
}

def system_prompt(base: str, tone: str, emoji: str, mention_reglement: str) -> str:
    """
    Construit le prompt 'système' principal pour la requête.
    """
    return (
        "Tu es **EvolutionBOT**, assistant officiel Discord de la guilde Evolution.\n"
        "Ta réponse doit être concise, chaleureuse et **en français**. "
        "Fais référence aux valeurs de convivialité et d’entraide de la guilde.\n"
        f"{base}\n{tone} {emoji}{mention_reglement}"
    )

def secure_text(txt: str) -> str:
    """
    Évite l'injection de code Markdown ou les mentions massives.
    Ajout: échappement des backticks simples.
    """
    from discord.utils import escape_markdown, escape_mentions

    # On protège tous les backticks pour éviter l'injection
    # (Ici, on peut simplement s’assurer qu’on ne produit pas de triple backticks.)
    txt = txt.replace("```", "`\u200b``")
    txt = txt.replace(">>>", ">\u200b>>")
    txt = escape_markdown(txt)
    txt = escape_mentions(txt)
    txt = txt.replace("@everyone", "@\u200beveryone")
    txt = txt.replace("@here", "@\u200bhere")
    return txt

def chunkify(txt: str, size=2000):
    """
    Découpe un long texte en morceaux de taille <= size (pour l'envoi sur Discord).
    Note: Idéalement, on ferait une découpe par tokens ; ici, c'est par caractères.
    """
    for i in range(0, len(txt), size):
        yield txt[i:i+size]


#
# 4) Classe principale du cog
#
class IACog(commands.Cog):
    ROLE_INVITE = "Membre validé d'Evolution"

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Historique max à analyser
        self.history_limit = 20

        # Limite de longueur du prompt (en caractères) avant tronquage
        self.max_prompt_size = 8000

        # Durée du blocage si quota dépassé (en secondes)
        self.quota_block_duration = 600
        self.quota_exceeded_until = 0

        self.debug_mode = True
        self.logger = self._setup_logger()

        # On utilise un ThreadPoolExecutor dédié,
        # pour éviter de bloquer s'il y a un prompt lent
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        # Canaux de destination en dur
        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"

        # Rappels règlement
        self.last_reglement_reminder = 0
        self.reglement_cooldown = 600

        # Contextes & anti-spam
        self.user_contexts = {}  # {user_id: deque of (msg, ts)}
        self.spam_times = {}
        self.spam_interval = 5
        self.spam_threshold = 4

        # File d'attente
        self.request_queue: asyncio.Queue[tuple[commands.Context, callable]] = asyncio.Queue()

        # On charge l'API
        self.configure_gemini()
        self.knowledge_text = self.get_knowledge_text()

        # IMPORTANT : on ne démarre PAS les tasks dans __init__ !
        # (pour éviter RuntimeError: no running event loop)

        self.logger.info("IACog __init__ terminé, Cog chargé en mémoire (tasks non démarrées).")

    async def cog_load(self) -> None:
        """
        Méthode spéciale appelée automatiquement par discord.py quand le Cog
        est entièrement chargé (la boucle asyncio du bot est disponible).
        On démarre ici les loops.
        """
        self.process_queue.start()
        self.cleanup_contexts.start()
        self.logger.info("IACog: Les tasks process_queue et cleanup_contexts ont été démarrées.")

    async def cog_unload(self) -> None:
        """
        Arrêt propre du Cog. On stoppe les loops, on vide la file d'attente
        et on ferme l'executor.
        """
        self.process_queue.cancel()
        self.cleanup_contexts.cancel()
        await self.request_queue.join()
        self.executor.shutdown(wait=True)
        self.logger.info("IACog déchargé proprement : tasks stoppées, executor fermé.")

    def _setup_logger(self) -> logging.Logger:
        """Crée un logger local nommé evo.ia avec un seul StreamHandler."""
        logger = logging.getLogger("evo.ia")

        # Pour éviter la multiplication de handlers
        if not logger.handlers:
            logger.setLevel(logging.DEBUG if self.debug_mode else logging.INFO)
            handler = logging.StreamHandler()
            handler.setLevel(logging.DEBUG if self.debug_mode else logging.INFO)
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            handler.setFormatter(fmt)
            logger.addHandler(handler)

        return logger

    def configure_gemini(self):
        """
        Initialise la configuration de l'API Gemini/PaLM2
        en chargeant la clé depuis l'environnement (.env).
        """
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing GEMINI_API_KEY.")

        genai.configure(api_key=self.api_key)
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

        masked_key = self.api_key[:4] + "..." + self.api_key[-4:]
        self.logger.info(f"Gemini API key chargée : {masked_key}")

        # Pour éviter un dump accidentel, on supprime la clé en mémoire
        del self.api_key

    def get_knowledge_text(self) -> str:
        """
        Retourne le règlement + la liste des commandes, pour l'ajouter
        en 'knowledge' dans le prompt.
        """
        return (
            "RÈGLEMENT OFFICIEL DE LA GUILDE EVOLUTION – Édition du 19/02/2025\n\n"
            "“Ensemble, nous évoluerons plus vite que seuls.”\n\n"
            "Bienvenue au sein de la guilde Evolution ! [... texte tronqué pour concision ... ]\n"
            "=====================================================================\n"
            "📌 **Commandes Staff**\n"
            "• __!staff__\n"
            "• __!annonce <texte>__\n"
            "• __!event <texte>__\n"
            "• __!recrutement <pseudo>__\n"
            "• __!membre del <pseudo>__\n"
            "=====================================================================\n"
        )

    @tasks.loop(seconds=1)
    async def process_queue(self):
        """Boucle d’exécution de la file d’attente toutes les 1 s."""
        if time.time() < self.quota_exceeded_until:
            # On est en période de blocage => on ne traite pas
            return

        while not self.request_queue.empty():
            ctx, prompt_callable = await self.request_queue.get()
            try:
                await prompt_callable(ctx)
            except Exception as exc:
                self.logger.exception("Unhandled error in process_queue", exc_info=exc)
                await ctx.send("Erreur interne lors du traitement de la requête.")
            finally:
                self.request_queue.task_done()

    @tasks.loop(hours=1)
    async def cleanup_contexts(self):
        """
        Purge des contextes vieux de plus de 7 jours.
        """
        cutoff = time.time() - 7*24*3600
        to_delete = []
        for uid, dq in self.user_contexts.items():
            new_dq = collections.deque(
                ((m, t) for (m, t) in dq if t >= cutoff),
                maxlen=dq.maxlen
            )
            if new_dq:
                self.user_contexts[uid] = new_dq
            else:
                to_delete.append(uid)

        for uid in to_delete:
            del self.user_contexts[uid]

    #
    # Fonctions d’appel Gemini (async) avec fallback si quota saturé
    #
    async def generate_content_async(self, model: genai.GenerativeModel, prompt: str):
        """
        Appel synchrone du model.generate_content() via run_in_executor(self.executor).
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: model.generate_content(prompt)
        )

    async def generate_content_with_fallback_async(self, prompt: str):
        """
        Tente d'abord le modèle PRO, si 429/quota, on essaie FLASH.
        Si FLASH retombe en 429, on bloque.
        """
        try:
            resp = await self.generate_content_async(self.model_pro, prompt)
            return resp, "PRO"
        except Exception as e1:
            if any(x in str(e1).lower() for x in ["429", "quota", "unavailable"]):
                self.logger.warning("Quota saturé (modèle PRO), tentative FLASH.")
                try:
                    resp2 = await self.generate_content_async(self.model_flash, prompt)
                    return resp2, "FLASH"
                except Exception as e2:
                    if "429" in str(e2).lower():
                        # On bloque globalement
                        self.quota_exceeded_until = time.time() + self.quota_block_duration
                        self.logger.warning("Quota saturé (modèle FLASH). Bloqué.")
                    raise e2
            raise e1

    #
    # 6) Commande !ia => affichage d’aide sur l’IA
    #
    @commands.command(name="ia")
    async def ia_help_command(self, ctx: commands.Context):
        """
        Affiche un petit texte d'aide sur les commandes IA.
        """
        txt = (
            "**Commandes IA :**\n"
            "`!bot` ou `!free <message>` – Pose une question à l’IA (réponse publique)\n"
            "`!analyse` (Staff) – Supprime la commande et donne un résumé du fil de discussion\n"
            "`!annonce <texte>` (Staff) – Supprime la cmd, crée une annonce\n"
            "`!event <texte>` (Staff) – Supprime la cmd, crée un événement\n"
            "`!pl <texte>` (Staff) – Supprime la cmd, crée une annonce PL\n"
            "Mentionnez @EvolutionBOT pour solliciter l'IA.\n"
            "`!ia` pour revoir ce guide."
        )
        await ctx.send(txt)

    #
    # 7) Commande !bot / !free -> handle_ai_request
    #
    @commands.command(name="bot", aliases=["free"])
    async def bot_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Commande principale pour appeler l'IA : !bot <question>.
        """
        if not user_message:
            await ctx.send("Usage : `!bot <votre question>`")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = self.request_queue.qsize()
            await ctx.send(f"**IA saturée**. Requête ajoutée en file. ({qlen} en file)")
            await self.request_queue.put((ctx, lambda co: self.handle_ai_request(co, user_message)))
            return

        await self.handle_ai_request(ctx, user_message)

    async def handle_ai_request(self, ctx: commands.Context, user_message: str):
        """
        Traitement des requêtes IA classiques.
        """
        now = time.time()

        # Anti-spam par utilisateur
        dq = self.spam_times.setdefault(ctx.author.id, collections.deque(maxlen=self.spam_threshold+1))
        dq.append(now)
        while dq and now - dq[0] > self.spam_interval:
            dq.popleft()

        if len(dq) > self.spam_threshold:
            wait = math.ceil(self.spam_interval - (now - dq[0]))
            if wait < 1:
                wait = 1
            await ctx.send(f"Ralentis un peu… réessaye dans {wait} s.")
            return

        user_message = secure_text(user_message)

        # Intention
        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)

        if intention in ["humor", "sarcasm", "light_provocation", "neutral"]:
            emoji = random.choice(EMOJIS_FRIENDLY)
        else:
            emoji = random.choice(EMOJIS_FIRM)

        mention_reglement = ""
        if intention in ["serious_insult", "discrimination", "threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        # Prompt
        base = PROMPT_BASES["bot"]
        system_str = system_prompt(base, chosen_tone, emoji, mention_reglement)

        # Historique perso
        self.user_contexts.setdefault(ctx.author.id, collections.deque(maxlen=50))
        self.user_contexts[ctx.author.id].append((user_message, now))

        # Historique de salon
        channel_history = []
        async for m in ctx.channel.history(limit=self.history_limit):
            if not m.author.bot:
                channel_history.append((m.created_at, m.author.display_name, secure_text(m.content)))
        channel_history.sort(key=lambda x: x[0])
        hist_txt = "".join(f"{author}: {content}\n" for (_, author, content) in channel_history)

        final_prompt = (
            f"{system_str}\n\n"
            f"knowledge_text:\n{self.knowledge_text}\n\n"
            f"Contexte({self.history_limit}):\n{hist_txt}\n\n"
            f"Message de {ctx.author.display_name}: {user_message}"
        )

        # Tronquage si trop gros
        while len(final_prompt) > self.max_prompt_size and hist_txt.count("\n") > 1:
            lines = hist_txt.split("\n")
            half = len(lines)//2
            hist_txt = "\n".join(lines[half:])
            final_prompt = (
                f"{system_str}\n\n"
                f"knowledge_text:\n{self.knowledge_text}\n\n"
                f"(Historique partiellement tronqué)\n\n"
                f"Message de {ctx.author.display_name}: {user_message}"
            )

        if len(final_prompt) > self.max_prompt_size:
            surplus = len(final_prompt) - self.max_prompt_size
            final_prompt = final_prompt[surplus:]

        # Appel IA
        try:
            resp, model_used = await self.generate_content_with_fallback_async(final_prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                if intention in ["serious_insult", "discrimination", "threat"]:
                    self.last_reglement_reminder = time.time()
                for chunk in chunkify(rep):
                    await ctx.send(chunk)
            else:
                await ctx.send("Aucune réponse IA.")
        except Exception as exc:
            if "429" in str(exc):
                await ctx.send("**Quota IA dépassé**, réessayez plus tard.")
            else:
                self.logger.exception("Erreur IA handle_ai_request", exc_info=exc)
                await ctx.send("Erreur interne de l'IA (voir logs).")

    #
    # 8) Mention directe => handle_ai_request
    #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Si @EvolutionBOT est mentionné dans un message normal,
        on traite comme !bot.
        """
        if message.author.bot:
            return
        ctx = await self.bot.get_context(message)
        # Si c'est déjà une commande, on n'intercepte pas
        if ctx.valid and ctx.command:
            return

        if self.bot.user.mention in message.content:
            raw = secure_text(message.content.replace(self.bot.user.mention, "").strip())
            if raw:
                if time.time() < self.quota_exceeded_until:
                    qlen = self.request_queue.qsize()
                    await ctx.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
                    await self.request_queue.put((ctx, lambda co: self.handle_ai_request(co, raw)))
                    return
                await self.handle_ai_request(ctx, raw)

    #
    # 9) !analyse (Staff)
    #
    @commands.command(name="analyse")
    @commands.has_role("Staff")
    async def analyse_command(self, ctx: commands.Context):
        """
        Lit les 100 derniers messages, supprime la commande,
        fait un compte-rendu neutre via IA.
        """
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        lim = 100
        messages = []
        async for m in ctx.channel.history(limit=lim):
            if not m.author.bot:
                messages.append(m)
        messages.sort(key=lambda x: x.created_at)

        joined = []
        for x in messages:
            safe = secure_text(x.content)
            joined.append(f"{x.author.display_name}: {safe}")
        user_message = "\n".join(joined)

        if time.time() < self.quota_exceeded_until:
            qlen = self.request_queue.qsize()
            await ctx.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
            await self.request_queue.put((ctx, lambda co: self.analyse_fallback(co, user_message)))
            return

        await self.analyse_fallback(ctx, user_message)

    async def analyse_fallback(self, ctx: commands.Context, user_message: str):
        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)

        now = time.time()
        mention_reglement = ""
        if intention in ["serious_insult", "discrimination", "threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emoji = random.choice(EMOJIS_FRIENDLY)
        else:
            emoji = random.choice(EMOJIS_FIRM)

        base = PROMPT_BASES["analyse"]
        system_str = system_prompt(base, chosen_tone, emoji, mention_reglement)

        final_prompt = f"{system_str}\n\n{user_message}"

        try:
            resp, model_used = await self.generate_content_with_fallback_async(final_prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                if intention in ["serious_insult", "discrimination", "threat"]:
                    self.last_reglement_reminder = time.time()
                for chunk in chunkify(rep):
                    await ctx.send(chunk)
            else:
                await ctx.send("Aucune réponse d'analyse.")
        except Exception as exc:
            if "429" in str(exc):
                await ctx.send("**Quota dépassé**.")
            else:
                self.logger.exception("Erreur dans analyse_fallback", exc_info=exc)
                await ctx.send("Erreur interne analyse_fallback.")

    #
    # 10) !annonce, !event, !pl => Staff
    #
    @commands.command(name="annonce")
    @commands.has_role("Staff")
    async def annonce_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Staff: poste une annonce dans #annonces
        """
        if not user_message:
            await ctx.send("Usage: !annonce <texte>")
            return
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        chan = discord.utils.get(ctx.guild.text_channels, name=self.annonce_channel_name)
        if not chan:
            await ctx.send("Canal introuvable.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = self.request_queue.qsize()
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            await self.request_queue.put((ctx, lambda co: self.annonce_fallback(co, chan, user_message)))
            return

        await self.annonce_fallback(ctx, chan, user_message)

    async def annonce_fallback(self, ctx: commands.Context, chan: discord.TextChannel, user_message: str):
        user_message = secure_text(user_message)

        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)

        now = time.time()
        mention_reglement = ""
        if intention in ["serious_insult","discrimination","threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emoji = random.choice(EMOJIS_FRIENDLY)
        else:
            emoji = random.choice(EMOJIS_FIRM)

        base = PROMPT_BASES["annonce"]
        system_str = system_prompt(base, chosen_tone, emoji, mention_reglement)
        prompt = f"{system_str}\n\n{user_message}"

        try:
            resp, model_used = await self.generate_content_with_fallback_async(prompt)
            if resp and hasattr(resp, "text"):
                final = resp.text.strip() or "(vide)"
                if intention in ["serious_insult","discrimination","threat"]:
                    self.last_reglement_reminder = time.time()
                await chan.send("**Annonce :**")
                for chunk in chunkify(final):
                    await chan.send(chunk)
            else:
                await ctx.send("Pas d'annonce générée.")
        except Exception as exc:
            if "429" in str(exc):
                await ctx.send("Quota dépassé.")
            else:
                self.logger.exception("Erreur dans annonce_fallback", exc_info=exc)
                await ctx.send("Erreur interne annonce_fallback.")

    @commands.command(name="event")
    @commands.has_role("Staff")
    async def event_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Staff: crée une invitation d'événement dans #organisation
        """
        if not user_message:
            await ctx.send("Usage: !event <texte>")
            return
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        chan = discord.utils.get(ctx.guild.text_channels, name=self.event_channel_name)
        if not chan:
            await ctx.send("Canal introuvable.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = self.request_queue.qsize()
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            await self.request_queue.put((ctx, lambda co: self.event_fallback(co, chan, user_message)))
            return

        await self.event_fallback(ctx, chan, user_message)

    async def event_fallback(self, ctx: commands.Context, chan: discord.TextChannel, user_message: str):
        user_message = secure_text(user_message)

        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)

        now = time.time()
        mention_reglement = ""
        if intention in ["serious_insult","discrimination","threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emoji = random.choice(EMOJIS_FRIENDLY)
        else:
            emoji = random.choice(EMOJIS_FIRM)

        base = PROMPT_BASES["event"]
        system_str = system_prompt(base, chosen_tone, emoji, mention_reglement)
        prompt = f"{system_str}\n\n{user_message}"

        try:
            resp, model_used = await self.generate_content_with_fallback_async(prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                if intention in ["serious_insult","discrimination","threat"]:
                    self.last_reglement_reminder = time.time()
                await chan.send("**Nouvel Événement :**")
                for chunk in chunkify(rep):
                    await chan.send(chunk)
                role_val = discord.utils.get(ctx.guild.roles, name=self.ROLE_INVITE)
                if role_val:
                    await chan.send(role_val.mention)
            else:
                await ctx.send("Événement non généré.")
        except Exception as exc:
            if "429" in str(exc):
                await ctx.send("Quota dépassé.")
            else:
                self.logger.exception("Erreur dans event_fallback", exc_info=exc)
                await ctx.send("Erreur interne event_fallback.")

    @commands.command(name="pl")
    @commands.has_role("Staff")
    async def pl_command(self, ctx: commands.Context, *, user_message: str = None):
        """
        Staff: crée une annonce PL dans #xplock-rondesasa-ronde
        """
        if not user_message:
            await ctx.send("Usage: !pl <texte>")
            return
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

        chan = discord.utils.get(ctx.guild.text_channels, name=self.pl_channel_name)
        if not chan:
            await ctx.send("Canal introuvable.")
            return

        if time.time() < self.quota_exceeded_until:
            qlen = self.request_queue.qsize()
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            await self.request_queue.put((ctx, lambda co: self.pl_fallback(co, chan, user_message)))
            return

        await self.pl_fallback(ctx, chan, user_message)

    async def pl_fallback(self, ctx: commands.Context, chan: discord.TextChannel, user_message: str):
        user_message = secure_text(user_message)

        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)

        now = time.time()
        mention_reglement = ""
        if intention in ["serious_insult","discrimination","threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emoji = random.choice(EMOJIS_FRIENDLY)
        else:
            emoji = random.choice(EMOJIS_FIRM)

        base = PROMPT_BASES["pl"]
        system_str = system_prompt(base, chosen_tone, emoji, mention_reglement)
        prompt = f"{system_str}\n\n{user_message}"

        try:
            resp, model_used = await self.generate_content_with_fallback_async(prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                if intention in ["serious_insult","discrimination","threat"]:
                    self.last_reglement_reminder = time.time()
                await chan.send("**Annonce PL :**")
                for chunk in chunkify(rep):
                    await chan.send(chunk)
            else:
                await ctx.send("Pas de réponse IA pour PL.")
        except Exception as exc:
            if "429" in str(exc):
                await ctx.send("Quota dépassé.")
            else:
                self.logger.exception("Erreur dans pl_fallback", exc_info=exc)
                await ctx.send("Erreur interne pl_fallback.")

#
# 12) setup du cog
#
async def setup(bot: commands.Bot):
    """
    Fonction d'initialisation du cog.
    On instancie la classe IACog et on l'ajoute au bot.
    """
    cog = IACog(bot)
    await bot.add_cog(cog)
