#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import unicodedata
import discord
from discord.ext import commands
from collections import defaultdict

DATA_FILE = "jobs_data.json"

def load_data():
    """
    Charge les données de jobs depuis le fichier JSON (sous forme de dict).
    Retourne {} si le fichier n'existe pas ou s'il y a une erreur de lecture.
    """
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_data(data):
    """
    Sauvegarde les données de jobs dans le fichier JSON
    (indentation, UTF-8, ensure_ascii=False).
    """
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def normalize_string(s: str) -> str:
    """
    Convertit la chaîne 's' en minuscule, supprime les accents
    et les caractères diacritiques pour permettre des comparaisons plus souples.
    """
    # Passage en minuscules + normalisation NFD pour séparer les diacritiques
    nf = unicodedata.normalize('NFD', s.lower())
    # On supprime les caractères de type 'Mark' (accents, etc.)
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')

def chunk_list(lst, chunk_size=25):
    """
    Génère des sous-listes de taille maximale `chunk_size`.
    Utile pour respecter la limite de 25 champs par embed sur Discord.
    """
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

class JobCog(commands.Cog):
    """
    Un Cog qui gère la commande !job.

    Usage résumé :
    - !job                          -> Affiche une mini-aide
    - !job me                       -> Affiche vos métiers
    - !job liste                    -> Affiche la liste complète de tous les métiers et qui les possède
    - !job liste metier            -> Affiche uniquement la liste de tous les noms de métiers
    - !job <pseudo>                -> Affiche les métiers du joueur portant ce pseudo
    - !job <job_name>              -> Affiche tous ceux qui ont ce job (si aucun pseudo ne correspond)
    - !job <job_name> <niveau>     -> Ajoute ou met à jour votre job
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jobs_data = load_data()

    @commands.command(name="job")
    async def job_command(self, ctx, *args):
        """
        Gère la commande "!job" avec plusieurs usages possibles :
        - !job
        - !job me
        - !job liste
        - !job liste metier
        - !job <pseudo>
        - !job <job_name>
        - !job <job_name> <niveau>
        """
        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        def get_user_jobs(user_id: str):
            """
            Retourne un dict {job_name: level} pour user_id.
            Si l'utilisateur n'existe pas ou n'a pas de jobs, renvoie {}.
            """
            if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
                return self.jobs_data[user_id]["jobs"]
            return {}

        # (A) Aide si aucun argument
        if len(args) == 0:
            usage_msg = (
                "**Utilisation de la commande !job :**\n"
                "- `!job me` : Afficher vos métiers.\n"
                "- `!job liste` : Afficher la liste complète des métiers et des joueurs.\n"
                "- `!job liste metier` : Afficher la liste de tous les noms de métiers.\n"
                "- `!job <pseudo>` : Afficher les métiers d'un joueur (par pseudo exact).\n"
                "- `!job <job_name>` : Afficher toutes les personnes ayant ce job.\n"
                "- `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job.\n"
            )
            embed_help = discord.Embed(
                title="Aide commande !job",
                description=usage_msg,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed_help)
            return

        arg1 = args[0].lower()

        # (B) !job me -> affiche les métiers de l'utilisateur
        if len(args) == 1 and arg1 == "me":
            user_jobs = get_user_jobs(author_id)
            if not user_jobs:
                embed_no_jobs = discord.Embed(
                    title="Vos métiers",
                    description=f"{author_name}, vous n'avez aucun job enregistré.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed_no_jobs)
            else:
                # Liste les métiers sous forme de champs
                embed_my_jobs = discord.Embed(
                    title=f"Métiers de {author_name}",
                    color=discord.Color.green()
                )
                for job_name, level in user_jobs.items():
                    embed_my_jobs.add_field(
                        name=job_name,
                        value=f"Niveau {level}",
                        inline=True
                    )
                await ctx.send(embed=embed_my_jobs)
            return

        # (C) !job liste metier -> liste alphabétique des noms de métiers (sans les joueurs)
        if len(args) == 2 and arg1 == "liste" and args[1].lower() == "metier":
            all_jobs = set()
            for uid, user_data in self.jobs_data.items():
                for job_name in user_data.get("jobs", {}).keys():
                    all_jobs.add(job_name)

            if not all_jobs:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return

            sorted_jobs = sorted(all_jobs, key=lambda x: normalize_string(x))

            # On construit un embed (s'il y a trop de jobs, on les liste dans un seul champ)
            embed_job_list = discord.Embed(
                title="Liste de tous les métiers",
                color=discord.Color.purple()
            )
            description_text = "Voici la liste (triée alphabétiquement, sans accents) :\n"

            for jn in sorted_jobs:
                description_text += f"• {jn}\n"

            if len(description_text) > 4096:
                # Si la description dépasse la limite, on la scinde en morceaux
                # ou on peut simplement chunk. Ici on fera un chunk en plusieurs messages
                parts = []
                lines = description_text.split("\n")
                buffer = ""
                for line in lines:
                    if len(buffer) + len(line) + 1 > 4096:
                        parts.append(buffer)
                        buffer = line + "\n"
                    else:
                        buffer += line + "\n"
                parts.append(buffer)

                # On envoie un premier embed avec le titre, et s'il y a d'autres parties
                for i, part in enumerate(parts, start=1):
                    embed_part = discord.Embed(
                        title=f"Liste de tous les métiers (part {i}/{len(parts)})",
                        description=part,
                        color=discord.Color.purple()
                    )
                    await ctx.send(embed=embed_part)

            else:
                # Sinon on peut tout mettre dans un seul embed
                embed_job_list.description = description_text
                await ctx.send(embed=embed_job_list)

            return

        # (D) !job liste -> liste intégrale de tous les métiers et qui les possède
        if len(args) == 1 and arg1 == "liste":
            jobs_map = defaultdict(list)
            # Parcours de tous les utilisateurs
            for uid, user_data in self.jobs_data.items():
                user_display_name = user_data.get("name", f"ID {uid}")
                for job_name, level in user_data.get("jobs", {}).items():
                    jobs_map[job_name].append((user_display_name, level))

            if not jobs_map:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return

            # Tri alphabétique (en ignorant les accents) des noms de métiers
            sorted_job_names = sorted(jobs_map.keys(), key=lambda x: normalize_string(x))

            # On enverra potentiellement plusieurs embeds, car chaque embed ne peut avoir que 25 champs
            # On chunk le sorted_job_names en groupes de 25
            embed_count = 0
            for chunk in chunk_list(sorted_job_names, 25):
                embed_count += 1
                embed_global = discord.Embed(
                    title=f"Liste complète des métiers (part {embed_count})",
                    color=discord.Color.blurple()
                )
                for job_name in chunk:
                    listing = ""
                    for (player_name, lvl) in jobs_map[job_name]:
                        listing += f"- **{player_name}** : {lvl}\n"

                    embed_global.add_field(name=job_name, value=listing, inline=False)

                await ctx.send(embed=embed_global)
            return

        # (E) !job <job_name> <niveau> -> mise à jour du job de l'auteur
        if len(args) == 2:
            try:
                level_int = int(args[1])
                # Premier arg = nom du job (on enregistre tel quel)
                job_name = args[0]
                # Mise à jour de la structure
                if author_id not in self.jobs_data:
                    self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
                else:
                    # Mise à jour du pseudo si besoin
                    self.jobs_data[author_id]["name"] = author_name

                self.jobs_data[author_id]["jobs"][job_name] = level_int
                save_data(self.jobs_data)

                embed_update = discord.Embed(
                    title="Mise à jour du job",
                    description=(
                        f"Le métier **{job_name}** est maintenant défini au niveau **{level_int}** "
                        f"pour **{author_name}**."
                    ),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed_update)
                return

            except ValueError:
                # Si le second argument n'est pas un entier, on traite plus loin
                pass

        # (F) Single argument => soit un pseudo, soit un job (plus flexible)
        if len(args) == 1:
            pseudo_or_job = args[0]

            # (1) On teste d'abord si c'est un pseudo exact (insensible à la casse).
            found_user_id = None
            found_user_name = None

            for uid, user_data in self.jobs_data.items():
                stored_name = user_data.get("name", "")
                if stored_name.lower() == pseudo_or_job.lower():
                    found_user_id = uid
                    found_user_name = stored_name
                    break

            if found_user_id:
                # On a trouvé un utilisateur portant ce nom (exact, sans accent)
                user_jobs = get_user_jobs(found_user_id)
                if not user_jobs:
                    await ctx.send(f"{found_user_name} n'a aucun job enregistré.")
                else:
                    embed_user_jobs = discord.Embed(
                        title=f"Métiers de {found_user_name}",
                        color=discord.Color.gold()
                    )
                    for job_name, lvl in user_jobs.items():
                        embed_user_jobs.add_field(
                            name=job_name,
                            value=f"Niveau {lvl}",
                            inline=True
                        )
                    await ctx.send(embed=embed_user_jobs)
                return
            else:
                # (2) Sinon on suppose que c'est un nom de métier => Recherche souple
                job_name_input_norm = normalize_string(pseudo_or_job)

                # Rassemble tous les (job_name -> [liste (user, lvl)]) dans un dict
                all_jobs_map = defaultdict(list)
                for uid, data in self.jobs_data.items():
                    display_name = data.get("name", f"ID {uid}")
                    for jn, lvl in data.get("jobs", {}).items():
                        all_jobs_map[jn].append((display_name, lvl))

                # Recherche de toutes les correspondances (partielles) en ignorant accents/casse
                matching_jobs = []
                for jn in all_jobs_map.keys():
                    if job_name_input_norm in normalize_string(jn):
                        matching_jobs.append(jn)

                if not matching_jobs:
                    await ctx.send(
                        f"Aucun utilisateur ne porte le pseudo **{pseudo_or_job}**, "
                        f"et aucun job similaire à **{pseudo_or_job}** n'a été trouvé."
                    )
                    return

                # On doit potentiellement gérer plus de 25 métiers en correspondance
                # donc on va chunkifier `matching_jobs`.
                sorted_matches = sorted(matching_jobs, key=lambda x: normalize_string(x))
                chunk_index = 0
                for chunk_of_jobs in chunk_list(sorted_matches, 25):
                    chunk_index += 1
                    result_embed = discord.Embed(
                        title=f"Résultats de la recherche de métier (part {chunk_index})",
                        description=(
                            f"Pour votre terme **{pseudo_or_job}**, voici les métiers correspondants "
                            f"(recherche insensible à la casse et aux accents) :"
                        ),
                        color=discord.Color.blue()
                    )
                    # Pour chaque job faisant partie de ce chunk
                    for job_match in chunk_of_jobs:
                        listing = ""
                        for (player, lvl) in all_jobs_map[job_match]:
                            listing += f"- **{player}** : {lvl}\n"

                        # On ajoute un champ par métier trouvé
                        result_embed.add_field(
                            name=job_match,
                            value=listing,
                            inline=False
                        )

                    await ctx.send(embed=result_embed)
                return

        # (G) Sinon : usage incorrect
        usage_msg = (
            "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
            "• `!job me` : Afficher vos métiers\n"
            "• `!job liste` : Afficher la liste de tous les métiers\n"
            "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
            "• `!job <pseudo>` : Afficher les métiers d'un joueur\n"
            "• `!job <job_name>` : Afficher ceux qui ont un métier correspondant (recherche souple)\n"
            "• `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job\n"
        )
        error_embed = discord.Embed(
            title="Erreur de syntaxe",
            description=usage_msg,
            color=discord.Color.red()
        )
        await ctx.send(embed=error_embed)

def setup(bot: commands.Bot):
    """
    Fonction nécessaire pour charger l'extension via load_extension().
    Pour les versions < 2.0, on ne met pas de 'async def setup'.
    """
    bot.add_cog(JobCog(bot))
