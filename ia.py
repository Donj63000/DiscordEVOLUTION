#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import asyncio
import collections
import random
import re
import discord
from discord.ext import commands, tasks
import google.generativeai as genai
from dotenv import load_dotenv

def is_exact_match(msg, keyword):
    pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
    return re.search(pattern, msg) is not None

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

def detect_intention(msg):
    cleaned = re.sub(r'[^\w\s]', '', msg.lower())
    for kw in SERIOUS_INSULT_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "serious_insult"
    for kw in DISCRIMINATION_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "discrimination"
    for kw in THREAT_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "threat"
    for kw in LIGHT_PROVOCATION_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "light_provocation"
    for kw in HUMOR_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "humor"
    for kw in SARCASM_KEYWORDS:
        if is_exact_match(cleaned, kw):
            return "sarcasm"
    return "neutral"

def chunkify(txt, size=2000):
    for i in range(0, len(txt), size):
        yield txt[i:i+size]

class IACog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.history_limit = 20
        self.max_prompt_size = 5000
        self.quota_block_duration = 3600
        self.quota_exceeded_until = 0
        self.debug_mode = True
        self.annonce_channel_name = "annonces"
        self.event_channel_name = "organisation"
        self.pl_channel_name = "xplock-rondesasa-ronde"
        self.last_reglement_reminder = 0
        self.reglement_cooldown = 600
        self.user_contexts = {}
        self.spam_times = {}
        self.spam_interval = 5
        self.spam_threshold = 4
        self.request_queue = collections.deque()
        self.pending_requests = False
        self.user_styles = {}
        self.warning_limit = 3
        self.mute_duration = 600
        self.configure_logging()
        self.configure_gemini()
        self.knowledge_text = self.get_knowledge_text()
        self.process_queue.start()

    @tasks.loop(seconds=5)
    async def process_queue(self):
        if self.pending_requests and time.time() >= self.quota_exceeded_until:
            while self.request_queue:
                ctx, prompt_callable = self.request_queue.popleft()
                try:
                    await prompt_callable(ctx)
                except:
                    pass
            self.pending_requests = False

    def cog_unload(self):
        self.process_queue.cancel()

    def configure_logging(self):
        lvl = logging.DEBUG if self.debug_mode else logging.INFO
        logging.basicConfig(level=lvl, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.logger = logging.getLogger("IACog")

    def configure_gemini(self):
        load_dotenv()
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Missing GEMINI_API_KEY.")
        genai.configure(api_key=self.api_key)
        self.model_pro = genai.GenerativeModel("gemini-1.5-pro")
        self.model_flash = genai.GenerativeModel("gemini-1.5-flash")

    def get_knowledge_text(self):
        return (
            "RÈGLEMENT OFFICIEL DE LA GUILDE EVOLUTION – Édition du 19/02/2025\n\n"
            "“Ensemble, nous évoluerons plus vite que seuls.”\n\n"
            "Bienvenue au sein de la guilde Evolution ! Nous sommes heureux de t’accueillir "
            "dans notre communauté. Ce règlement est conçu pour assurer une ambiance conviviale, "
            "respectueuse et motivante, tout en permettant à chacun de progresser selon son rythme "
            "et ses envies. Veille à bien le lire et à l’appliquer : chaque membre y contribue pour "
            "faire de cette guilde un endroit agréable où jouer ensemble.\n\n"
            "=====================================================================\n"
            "VALEURS & VISION\n"
            "=====================================================================\n"
            "• Convivialité & Partage : Respect, bonne humeur, entraide.\n"
            "• Progression Collective : Chaque point d’XP que tu apportes compte.\n"
            "• Transparence & Communication : Annonces sur Discord, canaux dédiés, etc.\n\n"
            "=====================================================================\n"
            "RESPECT & CONVIVIALITÉ 🤝\n"
            "=====================================================================\n"
            "• Aucune insulte, harcèlement ou discriminations (racisme, sexisme…) n’est toléré.\n"
            "• Politesse et bienveillance : dire “Bonjour”, rester courtois même en cas de désaccord.\n"
            "• Gestion des conflits : préviens le Staff, ou discutez en MP pour calmer la tension.\n\n"
            "=====================================================================\n"
            "DISCORD OBLIGATOIRE & COMMUNICATION 🗣️\n"
            "=====================================================================\n"
            "• L’usage de Discord est indispensable pour suivre les infos, annonces, sondages.\n"
            "• Canaux importants : #general, #annonces, #entraide, #organisation.\n"
            "• Commande !ticket pour contacter le Staff en privé.\n\n"
            "=====================================================================\n"
            "PARTICIPATION & VIE DE GUILDE 🌍\n"
            "=====================================================================\n"
            "• Présence régulière (même brève) pour maintenir le lien.\n"
            "• Organisation d’événements (Donjons, chasses, soirées quizz).\n"
            "• Entraide : Partage d’astuces, d’accompagnements en donjons.\n\n"
            "=====================================================================\n"
            "PERCEPTEURS & RESSOURCES 🏰\n"
            "=====================================================================\n"
            "• Droit de pose d’un Percepteur : dès 500 000 XP de contribution guilde.\n"
            "• Défense : tout le monde est invité à défendre un perco attaqué.\n"
            "• Communication : coordonnez-vous sur Discord pour éviter les conflits de zone.\n\n"
            "=====================================================================\n"
            "CONTRIBUTION D’XP À LA GUILDE 📊\n"
            "=====================================================================\n"
            "• Taux d’XP flexible entre 1 % et 99 % (0 % interdit sauf dérogation via !ticket).\n"
            "• 1 % minimum : un effort collectif très léger, mais utile pour la guilde.\n\n"
            "=====================================================================\n"
            "RECRUTEMENT & NOUVEAUX MEMBRES 🔑\n"
            "=====================================================================\n"
            "• Invitations contrôlées (Staff/vétérans).\n"
            "• Période d’essai possible (2-3 jours).\n"
            "• Discord obligatoire.\n\n"
            "=====================================================================\n"
            "ORGANISATION INTERNE & STAFF 🛡️\n"
            "=====================================================================\n"
            "• Fusion des rôles de trésoriers, bras droits, etc. Tous sont “Staff”.\n"
            "• Le Staff gère le recrutement, la modération et l’animation.\n"
            "• Meneur = décision finale mais fait confiance au Staff.\n\n"
            "=====================================================================\n"
            "SANCTIONS & DISCIPLINE ⚠️\n"
            "=====================================================================\n"
            "• Avertissements progressifs et décisions collégiales pour les cas graves.\n"
            "• Transparence : le joueur concerné est informé des raisons.\n\n"
            "=====================================================================\n"
            "MULTI-GUILDE 🔄\n"
            "=====================================================================\n"
            "• Toléré si ça ne nuit pas à Evolution. Conflits d’intérêt à discuter avec le Staff.\n"
            "• Le Staff doit être fidèle à Evolution.\n\n"
            "=====================================================================\n"
            "ÉVÉNEMENTS, SONDAGES & ANIMATIONS 🎉\n"
            "=====================================================================\n"
            "• Utiliser !sondage <Titre> ; <Choix1> ; ... ; temps=JJ:HH:MM> pour créer un sondage (#annonces).\n"
            "• !activite creer <Titre> <JJ/MM/AAAA HH:MM> [desc] : Crée une activité (donjon/sortie).\n"
            "• Concours, cadeaux, etc.\n\n"
            "=====================================================================\n"
            "CONCLUSION & AVENIR 🎇\n"
            "=====================================================================\n"
            "• Bienvenue chez Evolution ! Merci de respecter ces règles.\n"
            "• Toute suggestion d’amélioration est la bienvenue.\n\n"
            "Règlement en vigueur à compter du 21/02/2025.\n"
            "“Le véritable pouvoir d’une guilde se révèle lorsque tous ses membres unissent leurs forces.”\n\n"
            "=====================================================================\n"
            "LISTE DES COMMANDES DU BOT EVOLUTION (DÉTAILLÉES)\n"
            "=====================================================================\n"
            "📌 **Mini-Guides & Commandes Racines**\n"
            "• __!ia__ : Guide sur l’IA (ex.: !bot, !analyse).\n"
            "• __!membre__ : Récap global des sous-commandes (ex.: principal, addmule).\n"
            "• __!job__ : Guide des sous-commandes liées aux métiers (ex.: !job me, !job liste).\n"
            "• __!rune__ : Outil de calcul (probabilités runes). Fonctionnalité partielle.\n"
            "• __!regles__ : Résumé simplifié du règlement d'Evolution.\n\n"
            "📌 **Commandes Générales**\n"
            "• __!ping__ : Vérifie que le bot répond.\n"
            "• __!scan <URL>__ : Analyse un lien.\n"
            "• __!rune jet <valeur_jet> <stat>__ : Calcule les probabilités.\n\n"
            "📌 **Commandes Membres**\n"
            "• __!membre principal <NomPerso>__\n"
            "• __!membre addmule <NomMule>__\n"
            "• __!membre delmule <NomMule>__\n"
            "• __!membre moi__\n"
            "• __!membre liste__\n"
            "• __!membre <pseudo>__\n\n"
            "📌 **Commandes Job**\n"
            "• __!job me__\n"
            "• __!job liste__\n"
            "• __!job liste metier__\n"
            "• __!job <pseudo>__\n"
            "• __!job <job_name>__\n"
            "• __!job <job_name> <niveau>__\n\n"
            "📌 **Commande Ticket**\n"
            "• __!ticket__\n\n"
            "📌 **Commandes IA**\n"
            "• __!bot <message>__\n"
            "• __!analyse__\n\n"
            "📌 **Commandes Sondage**\n"
            "• __!sondage <Titre> ; <Choix1> ; ... ; temps=JJ:HH:MM>\n"
            "• __!close_sondage <message_id>\n\n"
            "📌 **Commandes Activités**\n"
            "• __!activite creer <Titre> <JJ/MM/AAAA HH:MM> [desc]\n"
            "• __!activite liste__\n"
            "• __!activite info <id>__\n"
            "• __!activite join <id> / !activite leave <id>\n"
            "• __!activite annuler <id> / !activite modifier <id>\n\n"
            "📌 **Commandes Staff**\n"
            "• __!staff__\n"
            "• __!annonce <texte>__\n"
            "• __!event <texte>__\n"
            "• __!recrutement <pseudo>__\n"
            "• __!membre del <pseudo>__\n"
            "=====================================================================\n"
        )

    @commands.command(name="ia")
    async def ia_help_command(self, ctx):
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

    async def handle_ai_request(self, ctx, user_message):
        now = time.time()
        uid = ctx.author.id
        if uid not in self.user_contexts:
            self.user_contexts[uid] = collections.deque(maxlen=50)
        if uid not in self.spam_times:
            self.spam_times[uid] = []
        self.spam_times[uid].append(now)
        self.spam_times[uid] = [t for t in self.spam_times[uid] if now - t < self.spam_interval]
        if len(self.spam_times[uid]) > self.spam_threshold:
            await ctx.send("Tu sembles spammer le bot. Merci de ralentir.")
            return

        intention = detect_intention(user_message)
        possible_tones = TONE_VARIATIONS.get(intention, TONE_VARIATIONS["neutral"])
        chosen_tone = random.choice(possible_tones)
        style_user = self.user_styles.get(uid, "neutre")
        if intention in ["humor","sarcasm","light_provocation","neutral"]:
            emo = random.choice(EMOJIS_FRIENDLY)
        else:
            emo = random.choice(EMOJIS_FIRM)

        # Si c'est une insulte, une discrimination ou une menace, juste avertir poliment
        mention_reglement = ""
        if intention in ["serious_insult", "discrimination", "threat"]:
            if (now - self.last_reglement_reminder) > self.reglement_cooldown:
                mention_reglement = " Merci de garder un langage convenable. (Réf. Règlement)"

        st = (
            f"Tu es EvolutionBOT, assistant de la guilde. L'utilisateur a un style '{style_user}'. "
            f"{chosen_tone} {emo}{mention_reglement}"
        )

        user_history = list(self.user_contexts[uid])
        user_history.append(user_message)
        self.user_contexts[uid] = collections.deque(user_history, maxlen=50)

        channel_history = []
        async for m in ctx.channel.history(limit=self.history_limit):
            if not m.author.bot:
                channel_history.append(m)
        channel_history.sort(key=lambda x: x.created_at)
        hist_txt = "".join(f"{m.author.display_name}: {m.content}\n" for m in channel_history)

        final_prompt = (
            f"{st}\n\n"
            f"knowledge_text:\n{self.knowledge_text}\n\n"
            f"Contexte({self.history_limit}):\n{hist_txt}\n\n"
            f"Message de {ctx.author.display_name}: {user_message}"
        )

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

        try:
            resp, model_used = await self.generate_content_with_fallback_async(final_prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                # Mettre à jour la variable last_reglement_reminder si c’est un gros écart
                if intention in ["serious_insult","discrimination","threat"]:
                    self.last_reglement_reminder = time.time()
                for c in chunkify(rep):
                    await ctx.send(c)
            else:
                await ctx.send("Aucune réponse de l'IA.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("**Quota IA dépassé**, réessayez plus tard.")
            else:
                await ctx.send(f"Erreur IA: {e}")

    async def generate_content_async(self, model, prompt):
        loop = asyncio.get_running_loop()
        def sync_call():
            return model.generate_content(prompt)
        return await loop.run_in_executor(None, sync_call)

    async def generate_content_with_fallback_async(self, prompt):
        try:
            r = await self.generate_content_async(self.model_pro, prompt)
            return r, "PRO"
        except Exception as e1:
            if any(x in str(e1).lower() for x in ["429","quota","unavailable"]):
                try:
                    r2 = await self.generate_content_async(self.model_flash, prompt)
                    return r2, "FLASH"
                except Exception as e2:
                    if "429" in str(e2):
                        self.quota_exceeded_until = time.time() + self.quota_block_duration
                    raise e2
            else:
                raise e1

    @commands.command(name="bot")
    async def free_command(self, ctx, *, user_message=None):
        if not user_message:
            await ctx.send("Usage : `!bot <votre question>`")
            return
        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"**IA saturée**. Requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.handle_ai_request(co, user_message)))
            self.pending_requests = True
            return
        await self.handle_ai_request(ctx, user_message)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        c = await self.bot.get_context(message)
        if c.valid and c.command:
            return
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

    @commands.command(name="analyse")
    async def analyse_command(self, ctx):
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

    async def analyse_fallback(self, ctx, prompt):
        try:
            resp, model_used = await self.generate_content_with_fallback_async(prompt)
            if resp and hasattr(resp, "text"):
                rep = resp.text.strip() or "(vide)"
                for c in chunkify(rep):
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
        if not user_message:
            await ctx.send("Usage: !annonce <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name="annonces")
        if not chan:
            await ctx.send("Canal introuvable.")
            return
        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.annonce_fallback(co, chan, user_message)))
            self.pending_requests = True
            return
        await self.annonce_fallback(ctx, chan, user_message)

    async def annonce_fallback(self, ctx, chan, user_message):
        st = "Tu es EvolutionBOT, crée une annonce fun et commence par '@everyone'."
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
                for c in chunkify(final):
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
        if not user_message:
            await ctx.send("Usage: !event <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name="organisation")
        if not chan:
            await ctx.send("Canal introuvable.")
            return
        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.event_fallback(co, chan, user_message)))
            self.pending_requests = True
            return
        await self.event_fallback(ctx, chan, user_message)

    async def event_fallback(self, ctx, chan, user_message):
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
                for c in chunkify(rep):
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
        if not user_message:
            await ctx.send("Usage: !pl <texte>")
            return
        chan = discord.utils.get(ctx.guild.text_channels, name="xplock-rondesasa-ronde")
        if not chan:
            await ctx.send("Canal introuvable.")
            return
        if time.time() < self.quota_exceeded_until:
            qlen = len(self.request_queue)
            await ctx.send(f"IA saturée, requête en file. ({qlen} en file)")
            self.request_queue.append((ctx, lambda co: self.pl_fallback(co, chan, user_message)))
            self.pending_requests = True
            return
        await self.pl_fallback(ctx, chan, user_message)

    async def pl_fallback(self, ctx, chan, user_message):
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
                for c in chunkify(rep):
                    await chan.send(c)
            else:
                await ctx.send("Pas de réponse IA pour PL.")
        except Exception as e:
            if "429" in str(e):
                await ctx.send("Quota dépassé.")
            else:
                await ctx.send(str(e))

async def setup(bot: commands.Bot):
    await bot.add_cog(IACog(bot))
