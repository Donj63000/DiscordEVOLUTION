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
        console_channel = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        if console_channel:
            # Recherche d'un message contenant ===BOTJOBS=== et un bloc JSON
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

        if not self.jobs_data and os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.jobs_data = json.load(f)
            except:
                self.jobs_data = {}

        self.initialized = True

    def save_data_local(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.jobs_data, f, indent=4, ensure_ascii=False)

    async def dump_data_to_console(self, ctx):
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
        if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
            return self.jobs_data[user_id]["jobs"]
        return {}

    def find_canonical_job_name_in_db(self, input_name: str) -> str:
        """
        Cherche dans TOUTE la base de données si un métier (job_name) existe déjà
        correspondant à input_name (comparaison insensible à la casse et aux accents).
        S'il existe, on renvoie le 'job_name' exact tel qu'il est stocké dans la DB.
        Sinon, on renvoie None.
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
        Donne la liste des métiers déjà existants qui "ressemblent" à input_name,
        ici on fait un test simplifié (dans un vrai cas on pourrait utiliser la distance
        d'édition Levenshtein).
        """
        suggestions = []
        all_jobs = set()
        for uid, data in self.jobs_data.items():
            for jn in data.get("jobs", {}).keys():
                all_jobs.add(jn)

        norm_in = normalize_string(input_name)
        for job_name in all_jobs:
            norm_jn = normalize_string(job_name)
            # On teste juste si l'un contient l'autre :
            if norm_in in norm_jn or norm_jn in norm_in:
                suggestions.append(job_name)

        return sorted(suggestions)

    async def confirm_job_creation_flow(self, ctx, job_name: str, level: int, author_id: str, author_name: str):
        """
        Dialogue interactif pour confirmer la création d'un nouveau métier si l'utilisateur le veut.
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
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["oui","non","cancel"]

        try:
            reply = await self.bot.wait_for("message", timeout=30.0, check=check)
        except:
            await ctx.send("Temps écoulé, commande annulée.")
            return

        if reply.content.lower() == "cancel" or reply.content.lower() == "non":
            await ctx.send("Ok, pas de création. Commande terminée.")
            return

        # S'il a dit "oui", on crée
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

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
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
        if not self.initialized:
            await self.initialize_data()

        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        # ---------------------------
        # Aide par défaut si pas d'args
        # ---------------------------
        if len(args) == 0:
            usage_msg = (
                "**Utilisation de la commande !job :**\n"
                "- `!job me` : Afficher vos métiers.\n"
                "- `!job liste` : Afficher la liste complète des métiers.\n"
                "- `!job liste metier` : Afficher la liste de tous les noms de métiers.\n"
                "- `!job <pseudo>` : Afficher les métiers d'un joueur.\n"
                "- `!job <job_name>` : Afficher ceux qui ont ce job.\n"
                "- `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job.\n"
                "- `!job add <job_name> <niveau>` : Commande directe pour ajouter un job (même multi-mots).\n"
            )
            await ctx.send(embed=discord.Embed(title="Aide commande !job", description=usage_msg, color=0x00ffff))
            return

        # ---------------------------
        # !job me
        # ---------------------------
        if len(args) == 1 and args[0].lower() == "me":
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

        # ---------------------------
        # !job liste metier
        # ---------------------------
        if len(args) == 2 and args[0].lower() == "liste" and args[1].lower() == "metier":
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
                # chunk si trop grand
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

        # ---------------------------
        # !job liste
        # ---------------------------
        if len(args) == 1 and args[0].lower() == "liste":
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

        # ------------------------------------------------
        # !job add <job_name...> <level>
        #  ==> Gère plusieurs mots pour job_name
        # ------------------------------------------------
        if len(args) >= 3 and args[0].lower() == "add":
            # On prend tous les arguments sauf le 1er (add) et le dernier (level)
            # Le job_name peut contenir plusieurs mots
            *job_name_tokens, level_str = args[1:]
            job_name = " ".join(job_name_tokens)
            # Convertit en int
            try:
                level_int = int(level_str)
            except ValueError:
                await ctx.send("Syntaxe invalide. Exemple : `!job add Grand Sculpteur 100`")
                return

            # Cherche si le job existe déjà
            canonical = self.find_canonical_job_name_in_db(job_name)
            if canonical is None:
                # Proposer la création
                await self.confirm_job_creation_flow(ctx, job_name, level_int, author_id, author_name)
            else:
                # Mettre à jour
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

        # ------------------------------------------------
        # !job <job_name> <level> (version courte)
        #  Mais ici, <job_name> ne peut pas contenir d'espaces
        #  => si vous voulez gérer plusieurs mots en version courte,
        #     il faut faire la même logique que ci-dessus (>=2).
        # ------------------------------------------------
        if len(args) == 2:
            job_name, level_str = args
            try:
                level_int = int(level_str)
            except ValueError:
                pass
            else:
                canonical = self.find_canonical_job_name_in_db(job_name)
                if canonical is None:
                    # Proposer la création
                    await self.confirm_job_creation_flow(ctx, job_name, level_int, author_id, author_name)
                else:
                    # Mettre à jour
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

        # ---------------------------
        # !job <pseudo> (1 arg)  OU  !job <job_name> (1 arg)
        # ---------------------------
        if len(args) == 1:
            # 1) on teste si c'est un pseudo
            pseudo_or_job = args[0]
            found_user_id = None
            found_user_name = None
            for uid, data in self.jobs_data.items():
                if data.get("name", "").lower() == pseudo_or_job.lower():
                    found_user_id = uid
                    found_user_name = data["name"]
                    break

            if found_user_id:
                # On affiche les jobs de ce joueur
                user_jobs = self.get_user_jobs(found_user_id)
                if not user_jobs:
                    await ctx.send(f"{found_user_name} n'a aucun job enregistré.")
                else:
                    e = discord.Embed(title=f"Métiers de {found_user_name}", color=discord.Color.gold())
                    for jn, lv in user_jobs.items():
                        e.add_field(name=jn, value=f"Niveau {lv}", inline=True)
                    await ctx.send(embed=e)
                return

            # 2) sinon, on cherche tous les joueurs qui ont un job correspondant
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

        # ---------------------------
        # Erreur de syntaxe
        # ---------------------------
        usage_msg = (
            "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
            "• `!job me` : Afficher vos métiers\n"
            "• `!job liste` : Afficher la liste de tous les métiers\n"
            "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
            "• `!job <pseudo>` : Afficher les métiers d'un joueur\n"
            "• `!job <job_name>` : Afficher ceux qui ont un métier correspondant\n"
            "• `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job (s'il existe ou après confirmation)\n"
            "• `!job add <job_name> <niveau>` : Ajouter un métier (multi-mots autorisé) au niveau spécifié\n"
        )
        await ctx.send(embed=discord.Embed(title="Erreur de syntaxe", description=usage_msg, color=discord.Color.red()))

    @commands.command(name="clear")
    @commands.has_role(STAFF_ROLE_NAME)
    async def clear_console_command(self, ctx, channel_name=None):
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
