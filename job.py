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

def normalize_string(s):
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

        if not self.jobs_data:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    try:
                        self.jobs_data = json.load(f)
                    except:
                        self.jobs_data = {}

        self.initialized = True

    def save_data_local(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self.jobs_data, f, indent=4, ensure_ascii=False)

    async def dump_data_to_console(self, ctx):
        console_channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if console_channel:
            data_str = json.dumps(self.jobs_data, indent=4, ensure_ascii=False)

            if len(data_str) < 1900:
                await console_channel.send(f"===BOTJOBS===\n```json\n{data_str}\n```")
            else:
                await console_channel.send(
                    "===BOTJOBS=== (fichier)",
                    file=discord.File(fp=self._as_temp_file(data_str), filename="jobs_data.json")
                )

    def _as_temp_file(self, data_str):
        with open("temp_jobs_data.json", "w", encoding="utf-8") as tmp:
            tmp.write(data_str)
        return "temp_jobs_data.json"

    def get_user_jobs(self, user_id: str):
        if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
            return self.jobs_data[user_id]["jobs"]
        return {}

    @commands.command(name="job")
    async def job_command(self, ctx, *args):
        if not self.initialized:
            await self.initialize_data()

        author = ctx.author
        author_id = str(author.id)
        author_name = author.display_name

        if len(args) == 0:
            usage_msg = (
                "**Utilisation de la commande !job :**\n"
                "- `!job me` : Afficher vos métiers.\n"
                "- `!job liste` : Afficher la liste complète des métiers.\n"
                "- `!job liste metier` : Afficher la liste de tous les noms de métiers.\n"
                "- `!job <pseudo>` : Afficher les métiers d'un joueur.\n"
                "- `!job <job_name>` : Afficher tous les joueurs qui ont ce job.\n"
                "- `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job.\n"
            )
            embed_help = discord.Embed(title="Aide commande !job", description=usage_msg, color=discord.Color.blue())
            await ctx.send(embed=embed_help)
            return

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

        if len(args) == 2:
            try:
                level_int = int(args[1])
                job_name = args[0]
                if author_id not in self.jobs_data:
                    self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
                else:
                    self.jobs_data[author_id]["name"] = author_name

                self.jobs_data[author_id]["jobs"][job_name] = level_int
                self.save_data_local()

                embed_update = discord.Embed(
                    title="Mise à jour du job",
                    description=f"Le métier **{job_name}** est maintenant défini au niveau **{level_int}** pour **{author_name}**.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed_update)

                await self.dump_data_to_console(ctx)
                return
            except ValueError:
                pass

        if len(args) == 1:
            pseudo_or_job = args[0].lower()
            found_user_id = None
            found_user_name = None

            for uid, user_data in self.jobs_data.items():
                if user_data.get("name", "").lower() == pseudo_or_job:
                    found_user_id = uid
                    found_user_name = user_data["name"]
                    break

            if found_user_id:
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
                
                job_map = defaultdict(list)
                for uid, data in self.jobs_data.items():
                    display_name = data.get("name", f"ID {uid}")
                    for jn, lv in data.get("jobs", {}).items():
                        job_map[jn].append((display_name, lv))

                matching_jobs = []
                for jn in job_map.keys():
                    if normalize_string(pseudo_or_job) in normalize_string(jn):
                        matching_jobs.append(jn)

                if not matching_jobs:
                    await ctx.send(
                        f"Aucun joueur nommé **{pseudo_or_job}** et aucun job similaire."
                    )
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

        usage_msg = (
            "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
            "• `!job me` : Afficher vos métiers\n"
            "• `!job liste` : Afficher la liste de tous les métiers\n"
            "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
            "• `!job <pseudo>` : Afficher les métiers d'un joueur\n"
            "• `!job <job_name>` : Afficher ceux qui ont un métier correspondant\n"
            "• `!job <job_name> <niveau>` : Ajouter / mettre à jour votre job\n"
        )
        error_embed = discord.Embed(title="Erreur de syntaxe", description=usage_msg, color=discord.Color.red())
        await ctx.send(embed=error_embed)

    @commands.command(name="clear")
    @commands.has_role(STAFF_ROLE_NAME)
    async def clear_console_command(self, ctx, channel_name=None):
        """
        Commande !clear console
        Seuls les membres ayant le rôle 'Staff' peuvent effectuer cette opération.
        Supprime tous les messages du canal 'console' (ou un certain nombre, selon la logique).
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
