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
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def normalize_string(s: str) -> str:
    nf = unicodedata.normalize('NFD', s.lower())
    return ''.join(c for c in nf if unicodedata.category(c) != 'Mn')

def chunk_list(lst, chunk_size=25):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

class JobCog(commands.Cog):
    """
    Cog pour gérer la commande !job.

    Usage rapide :
    - !job
    - !job me
    - !job liste
    - !job liste metier
    - !job <pseudo>
    - !job <job_name>
    - !job <job_name> <niveau>
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jobs_data = load_data()

    @commands.command(name="job")
    async def job_command(self, ctx, *args):
        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        def get_user_jobs(user_id: str):
            if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
                return self.jobs_data[user_id]["jobs"]
            return {}

        # A) Aide si aucun argument
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

        # B) !job me
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

        # C) !job liste metier
        if len(args) == 2 and arg1 == "liste" and args[1].lower() == "metier":
            all_jobs = set()
            for uid, user_data in self.jobs_data.items():
                for job_name in user_data.get("jobs", {}).keys():
                    all_jobs.add(job_name)

            if not all_jobs:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return

            sorted_jobs = sorted(all_jobs, key=lambda x: normalize_string(x))
            embed_job_list = discord.Embed(
                title="Liste de tous les métiers",
                color=discord.Color.purple()
            )
            description_text = "Voici la liste (triée alphabétiquement, sans accents) :\n"

            for jn in sorted_jobs:
                description_text += f"• {jn}\n"

            if len(description_text) > 4096:
                # Découpe si trop long
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

                for i, part in enumerate(parts, start=1):
                    embed_part = discord.Embed(
                        title=f"Liste de tous les métiers (part {i}/{len(parts)})",
                        description=part,
                        color=discord.Color.purple()
                    )
                    await ctx.send(embed=embed_part)
            else:
                embed_job_list.description = description_text
                await ctx.send(embed=embed_job_list)
            return

        # D) !job liste
        if len(args) == 1 and arg1 == "liste":
            jobs_map = defaultdict(list)
            for uid, user_data in self.jobs_data.items():
                user_display_name = user_data.get("name", f"ID {uid}")
                for job_name, level in user_data.get("jobs", {}).items():
                    jobs_map[job_name].append((user_display_name, level))

            if not jobs_map:
                await ctx.send("Aucun métier enregistré pour l'instant.")
                return

            sorted_job_names = sorted(jobs_map.keys(), key=lambda x: normalize_string(x))
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

        # E) !job <job_name> <niveau>
        if len(args) == 2:
            try:
                level_int = int(args[1])
                job_name = args[0]
                if author_id not in self.jobs_data:
                    self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
                else:
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
                pass

        # F) Single argument (pseudo ou job)
        if len(args) == 1:
            pseudo_or_job = args[0]
            found_user_id = None
            found_user_name = None

            for uid, user_data in self.jobs_data.items():
                stored_name = user_data.get("name", "")
                if stored_name.lower() == pseudo_or_job.lower():
                    found_user_id = uid
                    found_user_name = stored_name
                    break

            if found_user_id:
                user_jobs = get_user_jobs(found_user_id)
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
                # C'est sûrement un job => on cherche qui le possède
                job_name_input_norm = normalize_string(pseudo_or_job)
                all_jobs_map = defaultdict(list)
                for uid, data in self.jobs_data.items():
                    display_name = data.get("name", f"ID {uid}")
                    for jn, lvl in data.get("jobs", {}).items():
                        all_jobs_map[jn].append((display_name, lvl))

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
                    for job_match in chunk_of_jobs:
                        listing = ""
                        for (player, lvl) in all_jobs_map[job_match]:
                            listing += f"- **{player}** : {lvl}\n"
                        result_embed.add_field(name=job_match, value=listing, inline=False)
                    await ctx.send(embed=result_embed)
                return

        # G) Erreur de syntaxe
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

# Pour les versions 2.x de Discord.py/Py-Cord, on définit setup de manière asynchrone :
async def setup(bot: commands.Bot):
    await bot.add_cog(JobCog(bot))
