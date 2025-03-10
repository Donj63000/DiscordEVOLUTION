#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import unicodedata
import discord
from discord.ext import commands
from collections import defaultdict

CONSOLE_CHANNEL_NAME = "console"
DATA_FILE = "jobs_data.json"
STAFF_ROLE_NAME = "Staff"

def normalize_string(s: str) -> str:
    """
    Normalise un string en minuscule, sans accents/diacritiques.
    Permet de faire des comparaisons insensibles à la casse et aux accents.
    """
    nf = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')

def chunk_list(lst, chunk_size=25):
    """
    Découpe une liste en tranches de taille `chunk_size`.
    """
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

class JobCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jobs_data = {}
        self.initialized = False

    async def cog_load(self):
        await self.initialize_data()

    async def initialize_data(self):
        """
        Charge la base de données (jobs) :
         - Soit depuis le canal 'console' (si on y trouve un message contenant ===BOTJOBS===),
         - Soit depuis le fichier local 'jobs_data.json'.
        """
        console_channel = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if console_channel:
            # Recherche d'un message contenant ===BOTJOBS===
            async for msg in console_channel.history(limit=1000):
                if msg.author == self.bot.user and "===BOTJOBS===" in msg.content:
                    try:
                        start_idx = msg.content.index("```json\n") + len("```json\n")
                        end_idx = msg.content.rindex("\n```")
                        raw_json = msg.content[start_idx:end_idx]
                        self.jobs_data = json.loads(raw_json)
                        break
                    except:
                        pass

        # Si pas trouvé dans le canal console, on essaie le fichier local
        if not self.jobs_data and os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.jobs_data = json.load(f)
            except:
                self.jobs_data = {}

        self.initialized = True

    def save_data_local(self):
        """
        Sauvegarde la DB localement dans un fichier JSON.
        """
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.jobs_data, f, indent=4, ensure_ascii=False)

    async def dump_data_to_console(self, ctx):
        """
        Réécrit la DB dans le canal 'console' (ou en fichier si trop volumineux).
        """
        console_channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not console_channel:
            return
        data_str = json.dumps(self.jobs_data, indent=4, ensure_ascii=False)
        if len(data_str) < 1900:
            await console_channel.send(f"===BOTJOBS===\n```json\n{data_str}\n```")
        else:
            await console_channel.send(
                "===BOTJOBS=== (fichier)",
                file=discord.File(fp=self._as_temp_file(data_str), filename="jobs_data.json")
            )

    def _as_temp_file(self, data_str: str) -> str:
        with open("temp_jobs_data.json", "w", encoding="utf-8") as tmp:
            tmp.write(data_str)
        return "temp_jobs_data.json"

    def get_user_jobs(self, user_id: str):
        """
        Retourne un dict { job_name: level, ... } pour l'utilisateur donné.
        """
        if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
            return self.jobs_data[user_id]["jobs"]
        return {}

    def find_canonical_job_name_in_db(self, input_name: str) -> str:
        """
        Cherche si 'input_name' (en ignorant la casse/accents) est déjà un job existant.
        Si oui, renvoie le nom exact tel que stocké. Sinon, renvoie None.
        """
        normalized_input = normalize_string(input_name)
        all_job_names = set()
        for uid, user_data in self.jobs_data.items():
            for jn in user_data.get("jobs", {}).keys():
                all_job_names.add(jn)

        for existing_job_name in all_job_names:
            if normalize_string(existing_job_name) == normalized_input:
                return existing_job_name
        return None

    def suggest_similar_jobs(self, input_name: str):
        """
        Retourne une liste de métiers 'similaires' (simplifiée : test d'inclusion).
        """
        suggestions = []
        all_jobs = set()
        for uid, data in self.jobs_data.items():
            for jn in data.get("jobs", {}).keys():
                all_jobs.add(jn)

        norm_in = normalize_string(input_name)
        for job_name in all_jobs:
            norm_jn = normalize_string(job_name)
            if norm_in in norm_jn or norm_jn in norm_in:
                suggestions.append(job_name)

        return sorted(suggestions)

    async def confirm_job_creation_flow(self, ctx, job_name: str, level: int, author_id: str, author_name: str):
        """
        Flow de confirmation simple : si le job n'existe pas et qu'on tape !job <job_name> <level>,
        on propose la création. (Fonctionnalité existante.)
        """
        suggestions = self.suggest_similar_jobs(job_name)
        if suggestions:
            suggestion_text = "\n".join(f"- {s}" for s in suggestions)
            prompt = (
                f"Le métier **{job_name}** n'existe pas encore.\n"
                f"Voici des métiers existants qui y ressemblent :\n"
                f"{suggestion_text}\n\n"
                f"Si vous souhaitez VRAIMENT créer ce nouveau métier, tapez **oui**.\n"
                f"Si vous vous êtes trompé, tapez **non** ou **cancel**."
            )
        else:
            prompt = (
                f"Le métier **{job_name}** n'existe pas encore.\n"
                f"Aucun métier similaire trouvé.\n"
                f"Tapez **oui** pour créer ce métier, **non** ou **cancel** pour annuler."
            )

        await ctx.send(prompt)

        def check(m: discord.Message):
            return (m.author == ctx.author and 
                    m.channel == ctx.channel and 
                    m.content.lower() in ["oui", "non", "cancel"])

        try:
            reply = await self.bot.wait_for("message", timeout=30.0, check=check)
        except:
            await ctx.send("Temps écoulé, commande annulée.")
            return

        if reply.content.lower() in ["cancel", "non"]:
            await ctx.send("Ok, pas de création. Commande terminée.")
            return

        # => "oui" => on crée
        if author_id not in self.jobs_data:
            self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
        self.jobs_data[author_id]["name"] = author_name
        self.jobs_data[author_id]["jobs"][job_name] = level

        self.save_data_local()

        embed_ok = discord.Embed(
            title="Nouveau métier créé",
            description=f"Le métier **{job_name}** a été créé et défini au niveau {level} pour {author_name}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed_ok)
        await self.dump_data_to_console(ctx)

    async def interactive_add_job_no_level(self, ctx, job_name: str, author_id: str, author_name: str):
        """
        Lance un dialogue en plusieurs étapes pour !job add <job_name> (sans niveau) :
          1) Vérifie si le job existe
          2) Demande confirmation
          3) Demande le niveau
          4) Mise à jour ou création
        """
        canonical = self.find_canonical_job_name_in_db(job_name)

        if canonical:
            # => Le métier existe déjà
            await ctx.send(
                f"Le métier **{canonical}** existe déjà dans la base.\n"
                "Voulez-vous quand même le mettre à jour pour vous ? (Tapez `oui`, `non` ou `cancel`)"
            )
            def check_yes_no(m: discord.Message):
                return (m.author == ctx.author and
                        m.channel == ctx.channel and
                        m.content.lower() in ["oui", "non", "cancel"])

            try:
                msg_confirm = await self.bot.wait_for("message", timeout=30.0, check=check_yes_no)
            except:
                await ctx.send("Temps écoulé, commande annulée.")
                return

            if msg_confirm.content.lower() in ["non", "cancel"]:
                await ctx.send("Ok, commande abandonnée.")
                return

            # => "oui" => on demande le niveau
            await ctx.send("Quel niveau souhaitez-vous pour ce job ? (entrez un nombre ou `cancel`)")
            def check_level(m: discord.Message):
                return (m.author == ctx.author and m.channel == ctx.channel)

            try:
                msg_level = await self.bot.wait_for("message", timeout=30.0, check=check_level)
            except:
                await ctx.send("Temps écoulé, commande annulée.")
                return

            if msg_level.content.lower() == "cancel":
                await ctx.send("Commande annulée.")
                return

            try:
                level = int(msg_level.content)
            except ValueError:
                await ctx.send("Niveau invalide, commande annulée.")
                return

            # Mise à jour
            if author_id not in self.jobs_data:
                self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
            self.jobs_data[author_id]["name"] = author_name
            self.jobs_data[author_id]["jobs"][canonical] = level
            self.save_data_local()

            emb = discord.Embed(
                title="Mise à jour du job",
                description=(
                    f"Le métier **{canonical}** (initialement demandé : `{job_name}`)\n"
                    f"est défini au niveau **{level}** pour **{author_name}**."
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=emb)
            await self.dump_data_to_console(ctx)

        else:
            # => Le métier n'existe pas
            await ctx.send(
                f"Le métier **{job_name}** n'existe pas dans la base.\n"
                "Souhaitez-vous le créer ? (Tapez `oui`, `non` ou `cancel`)"
            )
            def check_yes_no(m: discord.Message):
                return (m.author == ctx.author and
                        m.channel == ctx.channel and
                        m.content.lower() in ["oui", "non", "cancel"])
            try:
                msg_confirm = await self.bot.wait_for("message", timeout=30.0, check=check_yes_no)
            except:
                await ctx.send("Temps écoulé, commande annulée.")
                return

            if msg_confirm.content.lower() in ["non", "cancel"]:
                await ctx.send("Ok, commande abandonnée.")
                return

            # => "oui"
            await ctx.send("Quel niveau voulez-vous attribuer à ce nouveau métier ? (entrez un nombre ou `cancel`)")
            def check_level(m: discord.Message):
                return (m.author == ctx.author and m.channel == ctx.channel)

            try:
                msg_level = await self.bot.wait_for("message", timeout=30.0, check=check_level)
            except:
                await ctx.send("Temps écoulé, commande annulée.")
                return

            if msg_level.content.lower() == "cancel":
                await ctx.send("Commande annulée.")
                return

            try:
                level = int(msg_level.content)
            except ValueError:
                await ctx.send("Niveau invalide, commande annulée.")
                return

            # Création
            if author_id not in self.jobs_data:
                self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
            self.jobs_data[author_id]["name"] = author_name
            self.jobs_data[author_id]["jobs"][job_name] = level
            self.save_data_local()

            emb = discord.Embed(
                title="Nouveau métier créé",
                description=(
                    f"Le métier **{job_name}** a été créé et défini au niveau **{level}** pour **{author_name}**."
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=emb)
            await self.dump_data_to_console(ctx)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Si un membre quitte, on supprime son entrée de la base.
        """
        user_id = str(member.id)
        if user_id in self.jobs_data:
            del self.jobs_data[user_id]
            self.save_data_local()
            console_channel = discord.utils.get(member.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
            if console_channel:
                data_str = json.dumps(self.jobs_data, indent=4, ensure_ascii=False)
                if len(data_str) < 1900:
                    await console_channel.send(f"===BOTJOBS===\n```json\n{data_str}\n```")
                else:
                    await console_channel.send(
                        "===BOTJOBS=== (fichier)",
                        file=discord.File(fp=self._as_temp_file(data_str), filename="jobs_data.json")
                    )

    @commands.command(name="job")
    async def job_command(self, ctx, *args):
        """
        Commande principale !job
        """
        if not self.initialized:
            await self.initialize_data()

        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        # ---------------------------
        # On enchaîne avec IF / ELIF / ELSE
        # ---------------------------

        # 1) Aide générale si pas d'arguments
        if len(args) == 0:
            usage_msg = (
                "**Utilisation de la commande !job :**\n"
                "- `!job me` : Afficher vos métiers.\n"
                "- `!job liste` : Afficher la liste complète des métiers.\n"
                "- `!job liste metier` : Afficher la liste de tous les noms de métiers.\n"
                "- `!job <pseudo>` : Afficher les métiers d'un joueur.\n"
                "- `!job <job_name>` : Afficher ceux qui ont ce job.\n"
                "- `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job.\n"
                "- `!job add <job_name> <niveau>` : Commande directe pour ajouter un job (multi-mots autorisé).\n"
            )
            await ctx.send(embed=discord.Embed(title="Aide commande !job", description=usage_msg, color=0x00ffff))
            return

        # 2) !job me
        elif len(args) == 1 and args[0].lower() == "me":
            user_jobs = self.get_user_jobs(author_id)
            if not user_jobs:
                await ctx.send(embed=discord.Embed(
                    title="Vos métiers",
                    description=f"{author_name}, vous n'avez aucun job enregistré.",
                    color=discord.Color.orange()
                ))
            else:
                emb = discord.Embed(title=f"Métiers de {author_name}", color=discord.Color.green())
                for job_name, lvl in user_jobs.items():
                    emb.add_field(name=job_name, value=f"Niveau {lvl}", inline=True)
                await ctx.send(embed=emb)
            return

        # 3) !job liste metier
        elif len(args) == 2 and args[0].lower() == "liste" and args[1].lower() == "metier":
            all_jobs = set()
            for uid, data in self.jobs_data.items():
                for jn in data.get("jobs", {}).keys():
                    all_jobs.add(jn)
            if not all_jobs:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return
            sorted_jobs = sorted(all_jobs, key=lambda x: normalize_string(x))
            text = "\n".join(f"• {jn}" for jn in sorted_jobs)
            if len(text) < 4096:
                e = discord.Embed(title="Liste de tous les métiers", description=text, color=discord.Color.purple())
                await ctx.send(embed=e)
            else:
                # Découpage si trop long
                current = ""
                chunks = []
                for line in text.split("\n"):
                    if len(current) + len(line) + 1 > 4096:
                        chunks.append(current)
                        current = line + "\n"
                    else:
                        current += line + "\n"
                chunks.append(current)
                for i, c in enumerate(chunks, start=1):
                    e = discord.Embed(title=f"Liste de tous les métiers (part {i})", description=c, color=discord.Color.purple())
                    await ctx.send(embed=e)
            return

        # 4) !job liste
        elif len(args) == 1 and args[0].lower() == "liste":
            jobs_map = defaultdict(list)
            for uid, data in self.jobs_data.items():
                disp_name = data.get("name", f"ID {uid}")
                for jn, lv in data.get("jobs", {}).items():
                    jobs_map[jn].append((disp_name, lv))
            if not jobs_map:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return

            sorted_job_names = sorted(jobs_map.keys(), key=lambda x: normalize_string(x))
            embed_count = 0
            for chunk in chunk_list(sorted_job_names, 25):
                embed_count += 1
                e = discord.Embed(
                    title=f"Liste complète des métiers (part {embed_count})",
                    color=discord.Color.blurple()
                )
                for jn in chunk:
                    listing = ""
                    for (player_name, lv) in jobs_map[jn]:
                        listing += f"- **{player_name}** : {lv}\n"
                    e.add_field(name=jn, value=listing, inline=False)
                await ctx.send(embed=e)
            return

        # 5) !job add <job_name> (SANS niveau) => conversation
        elif len(args) >= 2 and args[0].lower() == "add":
            # On va vérifier si le dernier argument est un nombre ou non.
            # Si c'est un nombre, c'est le mode direct existant. Sinon, on fait la conversation.
            possible_level = args[-1]
            try:
                level_int = int(possible_level)
                # => On a bien un niveau => code existant de !job add <job_name> <level>
                # Recompose le job_name (tout sauf le dernier arg)
                job_name_tokens = args[1:-1]  # tout sauf "add" et le dernier arg
                job_name = " ".join(job_name_tokens)

                canonical = self.find_canonical_job_name_in_db(job_name)
                if canonical is None:
                    # Creation
                    await self.confirm_job_creation_flow(ctx, job_name, level_int, author_id, author_name)
                else:
                    # Mise à jour
                    if author_id not in self.jobs_data:
                        self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
                    self.jobs_data[author_id]["name"] = author_name
                    self.jobs_data[author_id]["jobs"][canonical] = level_int

                    self.save_data_local()
                    emb = discord.Embed(
                        title="Mise à jour du job",
                        description=(
                            f"Le métier **{canonical}** (initialement demandé : `{job_name}`)\n"
                            f"a été défini au niveau **{level_int}** pour **{author_name}**."
                        ),
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=emb)
                    await self.dump_data_to_console(ctx)
                return

            except ValueError:
                # => pas de niveau => mode conversation
                job_name = " ".join(args[1:])
                await self.interactive_add_job_no_level(ctx, job_name, author_id, author_name)
                return

        # 6) !job <job_name> <level> (version courte, 2 arguments)
        elif len(args) == 2:
            job_name, level_str = args
            try:
                level_int = int(level_str)
            except ValueError:
                # => pas un entier => on ne correspond pas
                pass
            else:
                # On a un niveau
                canonical = self.find_canonical_job_name_in_db(job_name)
                if canonical is None:
                    # Proposer la création
                    await self.confirm_job_creation_flow(ctx, job_name, level_int, author_id, author_name)
                else:
                    # Mise à jour
                    author_jobs = self.jobs_data.get(author_id, {"name": author_name, "jobs": {}})
                    author_jobs["name"] = author_name
                    author_jobs["jobs"][canonical] = level_int
                    self.jobs_data[author_id] = author_jobs

                    self.save_data_local()
                    emb = discord.Embed(
                        title="Mise à jour du job",
                        description=(
                            f"Le métier **{canonical}** (initialement demandé : `{job_name}`)\n"
                            f"est maintenant défini au niveau **{level_int}** pour **{author_name}**."
                        ),
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=emb)
                    await self.dump_data_to_console(ctx)
                return

        # 7) !job <pseudo> ou !job <job_name> (1 argument)
        elif len(args) == 1:
            pseudo_or_job = args[0]
            # On cherche si c'est un pseudo EXACT
            found_user_id = None
            found_user_name = None
            for uid, data in self.jobs_data.items():
                if data.get("name", "").lower() == pseudo_or_job.lower():
                    found_user_id = uid
                    found_user_name = data["name"]
                    break

            if found_user_id:
                # Affiche les jobs du joueur
                user_jobs = self.get_user_jobs(found_user_id)
                if not user_jobs:
                    await ctx.send(f"{found_user_name} n'a aucun job enregistré.")
                else:
                    e = discord.Embed(title=f"Métiers de {found_user_name}", color=discord.Color.gold())
                    for jn, lv in user_jobs.items():
                        e.add_field(name=jn, value=f"Niveau {lv}", inline=True)
                    await ctx.send(embed=e)
                return
            else:
                # Sinon, on liste tous les joueurs qui ont un job correspondant (partiel)
                job_map = defaultdict(list)
                for uid, data in self.jobs_data.items():
                    display_name = data.get("name", f"ID {uid}")
                    for jn, lv in data.get("jobs", {}).items():
                        job_map[jn].append((display_name, lv))

                pseudo_norm = normalize_string(pseudo_or_job)
                matching_jobs = []
                for jn in job_map.keys():
                    if pseudo_norm in normalize_string(jn):
                        matching_jobs.append(jn)

                if not matching_jobs:
                    await ctx.send(f"Aucun joueur nommé **{pseudo_or_job}** et aucun job similaire.")
                    return

                sorted_matches = sorted(matching_jobs, key=lambda x: normalize_string(x))
                chunk_idx = 0
                for chunk in chunk_list(sorted_matches, 25):
                    chunk_idx += 1
                    emb = discord.Embed(
                        title=f"Résultats de la recherche de métier (part {chunk_idx})",
                        description=f"Recherche : {pseudo_or_job}",
                        color=discord.Color.blue()
                    )
                    for jn in chunk:
                        listing = ""
                        for (player, lv) in job_map[jn]:
                            listing += f"- **{player}** : {lv}\n"
                        emb.add_field(name=jn, value=listing, inline=False)
                    await ctx.send(embed=emb)
                return

        # 8) Aucune condition ne correspond => Erreur de syntaxe
        else:
            usage_msg = (
                "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
                "• `!job me` : Afficher vos métiers\n"
                "• `!job liste` : Afficher la liste de tous les métiers\n"
                "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
                "• `!job <pseudo>` : Afficher les métiers d'un joueur\n"
                "• `!job <job_name>` : Afficher ceux qui ont un métier correspondant\n"
                "• `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job (s'il existe ou après confirmation)\n"
                "• `!job add <job_name> <niveau>` : Ajouter un métier (multi-mots autorisé) au niveau spécifié\n"
                "   (ou sans niveau pour un dialogue interactif)\n"
            )
            await ctx.send(embed=discord.Embed(title="Erreur de syntaxe", description=usage_msg, color=discord.Color.red()))

    @commands.command(name="clear")
    @commands.has_role(STAFF_ROLE_NAME)
    async def clear_console_command(self, ctx, channel_name=None):
        """
        Permet de nettoyer le salon console (réservé au STAFF).
        """
        if not channel_name:
            await ctx.send("Utilisation : `!clear console`")
            return
        if channel_name.lower() != "console":
            await ctx.send("Pour l'instant, seule la commande `!clear console` est disponible.")
            return
        channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not channel:
            await ctx.send("Le salon console n'existe pas.")
            return
        deleted_count = 0
        async for msg in channel.history(limit=None, oldest_first=True):
            try:
                await msg.delete()
                deleted_count += 1
            except:
                pass
        await ctx.send(f"Salon console nettoyé, {deleted_count} messages supprimés.")

async def setup(bot: commands.Bot):
    await bot.add_cog(JobCog(bot))
