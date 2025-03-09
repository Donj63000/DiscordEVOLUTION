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
    Générateur qui découpe une liste (lst) en paquets de taille chunk_size.
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
        Charge les données soit depuis le canal console Discord (si disponible),
        soit depuis un fichier local jobs_data.json. Cette étape évite
        toute perte de DB existante.
        """
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

        # Si pas trouvé de JSON dans la console, on essaie le fichier local
        if not self.jobs_data and os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.jobs_data = json.load(f)
            except:
                self.jobs_data = {}

        self.initialized = True

    def save_data_local(self):
        """
        Sauvegarde la DB localement dans un fichier JSON, pour éviter
        toute perte de données.
        """
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.jobs_data, f, indent=4, ensure_ascii=False)

    async def dump_data_to_console(self, ctx):
        """
        Réécrit la DB complète dans le canal console (ou en fichier joint si trop volumineux).
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
        Retourne un dictionnaire {job_name: niveau, ...} pour un utilisateur donné (via user_id).
        """
        if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
            return self.jobs_data[user_id]["jobs"]
        return {}

    def find_canonical_job_name_in_db(self, input_name: str) -> str:
        """
        Cherche dans TOUTE la base de données si un métier (job_name) existe déjà
        correspondant à input_name (comparaison insensible à la casse et aux accents).
        S'il existe, on renvoie le 'job_name' tel qu'il est stocké dans la DB.
        Sinon, on renvoie None.

        Cette fonction permet d'éviter de créer des doublons (Pêcheur vs pecheur, etc.).
        """
        normalized_input = normalize_string(input_name)

        # Rassemble tous les job_names déjà existants
        all_job_names = set()
        for uid, user_data in self.jobs_data.items():
            for jn in user_data.get("jobs", {}).keys():
                all_job_names.add(jn)

        # On compare en normalisant
        for existing_job_name in all_job_names:
            if normalize_string(existing_job_name) == normalized_input:
                return existing_job_name

        return None

    def suggest_similar_jobs(self, input_name: str, max_distance=3):
        """
        Retourne une liste de noms de métiers (déjà existants) proches de input_name
        (par exemple en calculant une distance de Levenshtein ou en se contentant d'une
         similarité grossière). Ici, pour simplifier, on va juste regarder si
         l'input_name est "inclus" en normalisé, ou l'inverse.

        Pour une vraie solution, vous pouvez implémenter un algorithme de distance
        d'édition. Pour la démonstration, on se limite à un test de "in" + normalisation.
        """
        normalized_input = normalize_string(input_name)
        suggestions = []
        # Récupère tous les métiers existants
        all_job_names = set()
        for uid, user_data in self.jobs_data.items():
            for jn in user_data.get("jobs", {}).keys():
                all_job_names.add(jn)

        for job_name in all_job_names:
            norm_jn = normalize_string(job_name)
            if (normalized_input in norm_jn) or (norm_jn in normalized_input):
                suggestions.append(job_name)

        return sorted(suggestions)

    async def confirm_job_creation_flow(self, ctx, job_name: str, level: int, author_id: str, author_name: str):
        """
        Dialogue interactif pour créer un nouveau métier si l'utilisateur le souhaite.
        - 1) on propose un message "Le métier n'existe pas encore. Voulez-vous le créer ?".
        - 2) on propose aussi les suggestions de métiers proches.
        - 3) l'utilisateur peut taper 'cancel' pour annuler, 'oui' pour créer, ou bien 'non' 
             s'il pense avoir fait une faute et veut arrêter le process.

        S'il opte pour 'oui', on crée le job dans la base et on l'attribue à l'utilisateur.
        """
        # On affiche d'abord les suggestions de métiers similaires
        suggestions = self.suggest_similar_jobs(job_name)
        if suggestions:
            suggestion_text = "\n".join(f"- **{s}**" for s in suggestions)
            prompt = (
                f"**Le métier `{job_name}` n'existe pas dans la base.**\n\n"
                f"Auriez-vous fait une faute d'orthographe ?\n"
                f"Voici quelques métiers existants qui ressemblent à votre saisie :\n"
                f"{suggestion_text}\n\n"
                f"Si vous voulez vraiment **créer** un nouveau métier nommé `{job_name}`, "
                f"tapez `oui`. Si vous annulez la création, tapez `non`. (Tapez `cancel` pour abandonner complètement.)"
            )
        else:
            prompt = (
                f"**Le métier `{job_name}` n'existe pas dans la base.**\n\n"
                f"Aucun métier similaire trouvé.\n"
                f"Voulez-vous créer le nouveau métier **{job_name}** ? (Tapez `oui` pour créer, `non` pour refuser, `cancel` pour annuler la commande.)"
            )

        await ctx.send(prompt)

        def check(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["oui", "non", "cancel"]

        try:
            confirm_msg = await self.bot.wait_for("message", timeout=30.0, check=check)
        except:
            await ctx.send("Temps écoulé, commande annulée.")
            return

        if confirm_msg.content.lower() == "cancel":
            await ctx.send("Commande annulée.")
            return

        if confirm_msg.content.lower() == "non":
            await ctx.send("Aucune action effectuée. (Vous pouvez réessayer avec la bonne orthographe.)")
            return

        if confirm_msg.content.lower() == "oui":
            # Création du métier
            if author_id not in self.jobs_data:
                self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
            else:
                self.jobs_data[author_id]["name"] = author_name

            self.jobs_data[author_id]["jobs"][job_name] = level
            self.save_data_local()
            embed_add = discord.Embed(
                title="Nouveau métier créé",
                description=(
                    f"Le métier **{job_name}** a été créé et défini au niveau **{level}** "
                    f"pour **{author_name}**."
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=embed_add)
            await self.dump_data_to_console(ctx)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Si un membre quitte le serveur, on supprime ses données de la DB (pour faire le ménage).
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
        Commande principale !job.
        
        Usage:
          - !job me
          - !job liste
          - !job liste metier
          - !job <pseudo>
          - !job <job_name>
          - !job <job_name> <niveau>
          - !job add <job_name> <niveau>  (et NON plus "!job add <job_name>" seul)
        """
        if not self.initialized:
            await self.initialize_data()
        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        # -------------------------------------
        # Aide générale si aucun argument
        # -------------------------------------
        if len(args) == 0:
            usage_msg = (
                "**Utilisation de la commande !job :**\n"
                "- `!job me` : Afficher vos métiers.\n"
                "- `!job liste` : Afficher la liste complète des métiers.\n"
                "- `!job liste metier` : Afficher la liste de tous les noms de métiers.\n"
                "- `!job <pseudo>` : Afficher les métiers d'un joueur.\n"
                "- `!job <job_name>` : Afficher tous les joueurs qui ont ce job.\n"
                "- `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job.\n"
                "- `!job add <job_name> <niveau>` : Commande directe pour ajouter un job.\n"
            )
            embed_help = discord.Embed(title="Aide commande !job", description=usage_msg, color=discord.Color.blue())
            await ctx.send(embed=embed_help)
            return

        # -------------------------------------
        # !job me
        # -------------------------------------
        if len(args) == 1 and args[0].lower() == "me":
            user_jobs = self.get_user_jobs(author_id)
            if not user_jobs:
                embed_no_jobs = discord.Embed(
                    title="Vos métiers",
                    description=f"{author_name}, vous n'avez aucun job enregistré.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed_no_jobs)
            else:
                embed_my_jobs = discord.Embed(title=f"Métiers de {author_name}", color=discord.Color.green())
                for job_name, level in user_jobs.items():
                    embed_my_jobs.add_field(name=job_name, value=f"Niveau {level}", inline=True)
                await ctx.send(embed=embed_my_jobs)
            return

        # -------------------------------------
        # !job liste metier
        # -------------------------------------
        if len(args) == 2 and args[0].lower() == "liste" and args[1].lower() == "metier":
            all_jobs = set()
            for uid, user_data in self.jobs_data.items():
                for job_name in user_data.get("jobs", {}).keys():
                    all_jobs.add(job_name)
            if not all_jobs:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return
            sorted_jobs = sorted(all_jobs, key=lambda x: normalize_string(x))
            embed_job_list = discord.Embed(title="Liste de tous les métiers", color=discord.Color.purple())
            text_jobs = "\n".join(f"• {jn}" for jn in sorted_jobs)
            # Découpe le message si trop long
            if len(text_jobs) > 4096:
                chunks = []
                current = ""
                for line in text_jobs.split("\n"):
                    if len(current) + len(line) + 1 > 4096:
                        chunks.append(current)
                        current = line + "\n"
                    else:
                        current += line + "\n"
                chunks.append(current)
                for i, c in enumerate(chunks, start=1):
                    e = discord.Embed(title=f"Liste de tous les métiers (part {i})", description=c, color=discord.Color.purple())
                    await ctx.send(embed=e)
            else:
                embed_job_list.description = text_jobs
                await ctx.send(embed=embed_job_list)
            return

        # -------------------------------------
        # !job liste
        # -------------------------------------
        if len(args) == 1 and args[0].lower() == "liste":
            jobs_map = defaultdict(list)
            for uid, user_data in self.jobs_data.items():
                user_display_name = user_data.get("name", f"ID {uid}")
                for job_name, lvl in user_data.get("jobs", {}).items():
                    jobs_map[job_name].append((user_display_name, lvl))
            if not jobs_map:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return
            sorted_job_names = sorted(jobs_map.keys(), key=lambda x: normalize_string(x))
            embed_count = 0
            # Découper l'affichage par tranche de 25 jobs
            for chunk in chunk_list(sorted_job_names, 25):
                embed_count += 1
                embed_global = discord.Embed(
                    title=f"Liste complète des métiers (part {embed_count})",
                    color=discord.Color.blurple()
                )
                for job_name in chunk:
                    listing = ""
                    for (player_name, lv) in jobs_map[job_name]:
                        listing += f"- **{player_name}** : {lv}\n"
                    embed_global.add_field(name=job_name, value=listing, inline=False)
                await ctx.send(embed=embed_global)
            return

        # -------------------------------------
        # !job add <job_name> <level>
        # -------------------------------------
        if len(args) == 3 and args[0].lower() == "add":
            input_job_name = " ".join(args[1:-1])  # Au cas où <job_name> contienne des espaces
            level_str = args[-1]
            try:
                level_int = int(level_str)
            except ValueError:
                await ctx.send("Syntaxe invalide. Exemple : `!job add Bucheron 5`.")
                return

            # Cherche si ce job existe déjà
            canonical_job_name = self.find_canonical_job_name_in_db(input_job_name)
            if canonical_job_name is None:
                # Lancement du flow interactif pour éventuellement créer le métier
                await self.confirm_job_creation_flow(ctx, input_job_name, level_int, author_id, author_name)
            else:
                # OK, on met à jour l'utilisateur
                if author_id not in self.jobs_data:
                    self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
                else:
                    self.jobs_data[author_id]["name"] = author_name

                self.jobs_data[author_id]["jobs"][canonical_job_name] = level_int

                self.save_data_local()
                embed_add = discord.Embed(
                    title="Mise à jour du job",
                    description=(
                        f"Le métier **{canonical_job_name}** (initialement demandé : `{input_job_name}`)\n"
                        f"a été défini au niveau **{level_int}** pour **{author_name}**."
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed_add)
                await self.dump_data_to_console(ctx)
            return

        # -------------------------------------
        # !job <job_name> <level>  (version courte, ex: "!job pêcheur 10")
        # -------------------------------------
        if len(args) == 2:
            # On recompose job_name s'il y a un risque d'espace, mais ici len=2 → 2 tokens
            #   => job_name = args[0], level_str = args[1]
            job_name = args[0]
            level_str = args[1]
            try:
                level_int = int(level_str)
            except ValueError:
                pass
            else:
                canonical_job_name = self.find_canonical_job_name_in_db(job_name)
                if canonical_job_name is None:
                    # Lancement du flow interactif pour éventuellement créer le métier
                    await self.confirm_job_creation_flow(ctx, job_name, level_int, author_id, author_name)
                else:
                    author_jobs = self.jobs_data.get(author_id, {"name": author_name, "jobs": {}})
                    author_jobs["name"] = author_name
                    author_jobs["jobs"][canonical_job_name] = level_int
                    self.jobs_data[author_id] = author_jobs

                    self.save_data_local()
                    embed_update = discord.Embed(
                        title="Mise à jour du job",
                        description=(
                            f"Le métier **{canonical_job_name}** (initialement demandé : `{job_name}`)\n"
                            f"est maintenant défini au niveau **{level_int}** pour **{author_name}**."
                        ),
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed_update)
                    await self.dump_data_to_console(ctx)
                return

        # -------------------------------------
        # !job <pseudo> ou !job <job_name>
        # -------------------------------------
        if len(args) == 1:
            pseudo_or_job = args[0]
            # 1) On cherche si c'est un pseudo EXACT
            found_user_id = None
            found_user_name = None
            for uid, user_data in self.jobs_data.items():
                if user_data.get("name", "").lower() == pseudo_or_job.lower():
                    found_user_id = uid
                    found_user_name = user_data["name"]
                    break

            if found_user_id:
                # On affiche les jobs de ce joueur
                user_jobs = self.get_user_jobs(found_user_id)
                if not user_jobs:
                    await ctx.send(f"{found_user_name} n'a aucun job enregistré.")
                else:
                    embed_user_jobs = discord.Embed(
                        title=f"Métiers de {found_user_name}",
                        color=discord.Color.gold()
                    )
                    for job_name, lvl in user_jobs.items():
                        embed_user_jobs.add_field(name=job_name, value=f"Niveau {lvl}", inline=True)
                    await ctx.send(embed=embed_user_jobs)
                return
            else:
                # 2) Sinon, on cherche si c'est un job (partiel) => on liste tous les joueurs
                job_map = defaultdict(list)
                for uid, data in self.jobs_data.items():
                    display_name = data.get("name", f"ID {uid}")
                    for jn, lv in data.get("jobs", {}).items():
                        job_map[jn].append((display_name, lv))

                # On cherche les jobs qui correspondent (en normalisant) à l'arg
                pseudo_or_job_norm = normalize_string(pseudo_or_job)
                matching_jobs = []
                for jn in job_map.keys():
                    if pseudo_or_job_norm in normalize_string(jn):
                        matching_jobs.append(jn)

                if not matching_jobs:
                    await ctx.send(f"Aucun joueur nommé **{pseudo_or_job}** et aucun job similaire.")
                    return

                sorted_matches = sorted(matching_jobs, key=lambda x: normalize_string(x))
                chunk_idx = 0
                for chunk in chunk_list(sorted_matches, 25):
                    chunk_idx += 1
                    result_embed = discord.Embed(
                        title=f"Résultats de la recherche de métier (part {chunk_idx})",
                        description=f"Recherche : {pseudo_or_job}",
                        color=discord.Color.blue()
                    )
                    for jn in chunk:
                        listing = ""
                        for (player, lv) in job_map[jn]:
                            listing += f"- **{player}** : {lv}\n"
                        result_embed.add_field(name=jn, value=listing, inline=False)
                    await ctx.send(embed=result_embed)
                return

        # -------------------------------------
        # Sinon → message d'erreur de syntaxe
        # -------------------------------------
        usage_msg = (
            "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
            "• `!job me` : Afficher vos métiers\n"
            "• `!job liste` : Afficher la liste de tous les métiers\n"
            "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
            "• `!job <pseudo>` : Afficher les métiers d'un joueur\n"
            "• `!job <job_name>` : Afficher ceux qui ont un métier correspondant\n"
            "• `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job (s'il existe déjà ou après confirmation)\n"
            "• `!job add <job_name> <niveau>` : Ajouter un métier au niveau spécifié (s'il existe ou après confirmation)\n"
        )
        error_embed = discord.Embed(title="Erreur de syntaxe", description=usage_msg, color=discord.Color.red())
        await ctx.send(embed=error_embed)

    @commands.command(name="clear")
    @commands.has_role(STAFF_ROLE_NAME)
    async def clear_console_command(self, ctx, channel_name=None):
        """
        Permet de nettoyer le salon "console" (limité aux rôles staff).
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
