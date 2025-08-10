#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import re
import unicodedata
import tempfile
import discord
from discord.ext import commands
from collections import defaultdict

CONSOLE_CHANNEL_NAME = "console"
DATA_FILE = os.path.join(os.path.dirname(__file__), "jobs_data.json")
STAFF_ROLE_NAME = "Staff"
JOB_MIN_LEVEL = 1
JOB_MAX_LEVEL = 100

def normalize_string(s: str) -> str:
    s = s.replace("’", "'")
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def chunk_list(lst, chunk_size=25):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]

JOB_CATEGORIES = {
    "Récolte": [
        "Alchimiste",
        "Paysan",
        "Bûcheron",
        "Mineur",
        "Chasseur",
        "Pêcheur",
    ],
    "Artisanat": [
        "Alchimiste",
        "Boulanger",
        "Boucher",
        "Poissonnier",
        "Bijoutier",
        "Cordonnier",
        "Tailleur",
        "Sculpteur d’armes",
        "Forgeur d’armes",
        "Forgeur de boucliers",
        "Bricoleur",
    ],
    "Spécialisations": [
        "Forgemagie",
        "Sculptemagie",
    ],
}

CANONICAL_JOBS_ORDERED = []
for cat in ["Récolte", "Artisanat", "Spécialisations"]:
    for j in JOB_CATEGORIES[cat]:
        if j not in CANONICAL_JOBS_ORDERED:
            CANONICAL_JOBS_ORDERED.append(j)

ALIASES = {
    "alchimiste": "Alchimiste",
    "alchi": "Alchimiste",
    "paysan": "Paysan",
    "bucheron": "Bûcheron",
    "bûcheron": "Bûcheron",
    "mineur": "Mineur",
    "chasseur": "Chasseur",
    "pecheur": "Pêcheur",
    "pêcheur": "Pêcheur",
    "boulanger": "Boulanger",
    "boucher": "Boucher",
    "poissonnier": "Poissonnier",
    "bijoutier": "Bijoutier",
    "joa": "Bijoutier",
    "joaillier": "Bijoutier",
    "cordonnier": "Cordonnier",
    "tailleur": "Tailleur",
    "sculpteur": "Sculpteur d’armes",
    "sculpteur armes": "Sculpteur d’armes",
    "sculpteur d armes": "Sculpteur d’armes",
    "sculpteur d'armes": "Sculpteur d’armes",
    "forgeur": "Forgeur d’armes",
    "forgeron": "Forgeur d’armes",
    "forgeur armes": "Forgeur d’armes",
    "forgeur d armes": "Forgeur d’armes",
    "forgeur d'armes": "Forgeur d’armes",
    "forgeur bouclier": "Forgeur de boucliers",
    "forgeur de bouclier": "Forgeur de boucliers",
    "forgeur de boucliers": "Forgeur de boucliers",
    "forgebouclier": "Forgeur de boucliers",
    "bricoleur": "Bricoleur",
    "forgemagie": "Forgemagie",
    "forgemage": "Forgemagie",
    "sculptemagie": "Sculptemagie",
    "sculptemage": "Sculptemagie",
}

ALIAS_LOOKUP = {normalize_string(k): v for k, v in ALIASES.items()}
CANON_LOOKUP = {normalize_string(j): j for j in CANONICAL_JOBS_ORDERED}
SPECIALIZATION_SET = set(JOB_CATEGORIES["Spécialisations"])
SPECIALIZATION_ALLOWED_BASE = {"Forgeur d’armes", "Sculpteur d’armes", "Tailleur", "Cordonnier", "Bijoutier"}

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
                    if "fichier" in msg.content and msg.attachments:
                        att = discord.utils.find(lambda a: a.filename == "jobs_data.json", msg.attachments)
                        if att:
                            try:
                                data_bytes = await att.read()
                                self.jobs_data = json.loads(data_bytes.decode("utf-8"))
                                break
                            except:
                                pass
                    else:
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
        await self.migrate_legacy_keys()
        self.initialized = True

    def save_data_local(self):
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(self.jobs_data, f, indent=4, ensure_ascii=False)
        except:
            pass

    async def dump_data_to_console(self, ctx):
        console_channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not console_channel:
            return
        data_str = json.dumps(self.jobs_data, indent=4, ensure_ascii=False)
        if len(data_str) < 1900:
            await console_channel.send(f"===BOTJOBS===\n```json\n{data_str}\n```")
        else:
            temp_path = self._as_temp_file(data_str)
            await console_channel.send("===BOTJOBS=== (fichier)", file=discord.File(fp=temp_path, filename="jobs_data.json"))
            os.remove(temp_path)

    def _as_temp_file(self, data_str: str) -> str:
        tmp = tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8", suffix=".json")
        tmp.write(data_str)
        tmp.close()
        return tmp.name

    async def migrate_legacy_keys(self):
        updated = False
        to_remove = []
        for key, data in list(self.jobs_data.items()):
            if not key.isdigit():
                member = discord.utils.find(
                    lambda m: m.name.lower() == key.lower() or m.display_name.lower() == key.lower(),
                    self.bot.get_all_members(),
                )
                if member:
                    new_key = str(member.id)
                    if new_key not in self.jobs_data:
                        self.jobs_data[new_key] = {"name": member.display_name, "jobs": {}}
                    self.jobs_data[new_key]["jobs"].update(data.get("jobs", {}))
                    if not self.jobs_data[new_key].get("name"):
                        self.jobs_data[new_key]["name"] = member.display_name
                    to_remove.append(key)
                    updated = True
        for k in to_remove:
            del self.jobs_data[k]
        if updated:
            self.save_data_local()

    def get_user_jobs(self, user_id: str, user_name: str = None):
        if user_id in self.jobs_data and "jobs" in self.jobs_data[user_id]:
            return self.jobs_data[user_id]["jobs"]
        if user_name:
            for key, data in self.jobs_data.items():
                if not key.isdigit() and data.get("name", "").lower() == user_name.lower():
                    return data.get("jobs", {})
        return {}

    def resolve_job_name(self, input_name: str):
        n = normalize_string(input_name)
        if n in ALIAS_LOOKUP:
            return ALIAS_LOOKUP[n]
        if n in CANON_LOOKUP:
            return CANON_LOOKUP[n]
        for canon in CANONICAL_JOBS_ORDERED:
            if n == normalize_string(canon):
                return canon
        for uid, data in self.jobs_data.items():
            for jn in data.get("jobs", {}).keys():
                if n == normalize_string(jn):
                    return jn
        return None

    def suggest_similar_jobs(self, input_name: str, limit=6):
        n = normalize_string(input_name)
        pool = set(CANONICAL_JOBS_ORDERED)
        for uid, data in self.jobs_data.items():
            for jn in data.get("jobs", {}).keys():
                pool.add(jn)
        scored = []
        for j in pool:
            d = levenshtein(n, normalize_string(j))
            scored.append((d, j))
        scored.sort(key=lambda x: (x[0], normalize_string(x[1])))
        return [j for _, j in scored[:limit]]

    async def confirm_job_creation_flow(self, ctx, job_name: str, level: int, author_id: str, author_name: str):
        if level < JOB_MIN_LEVEL or level > JOB_MAX_LEVEL:
            await ctx.send(f"Le niveau doit être compris entre {JOB_MIN_LEVEL} et {JOB_MAX_LEVEL}.")
            return
        suggestions = self.suggest_similar_jobs(job_name)
        if suggestions:
            suggestion_text = "\n".join(f"- {s}" for s in suggestions)
            prompt = (
                f"Le métier **{job_name}** n'existe pas encore.\n"
                f"Suggestions proches :\n{suggestion_text}\n\n"
                f"Tapez **oui** pour créer ce nouveau métier, **non** ou **cancel** pour annuler."
            )
        else:
            prompt = f"Le métier **{job_name}** n'existe pas encore.\nTapez **oui** pour créer, **non** ou **cancel** pour annuler."
        await ctx.send(prompt)

        def check(m: discord.Message):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["oui", "non", "cancel"]

        try:
            reply = await self.bot.wait_for("message", timeout=30.0, check=check)
        except:
            await ctx.send("Temps écoulé, commande annulée.")
            return

        if reply.content.lower() in ["cancel", "non"]:
            await ctx.send("Commande terminée.")
            return

        if author_id not in self.jobs_data:
            self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
        self.jobs_data[author_id]["name"] = author_name
        self.jobs_data[author_id]["jobs"][job_name] = level
        self.save_data_local()
        embed_ok = discord.Embed(title="Nouveau métier créé", description=f"Le métier **{job_name}** a été créé et défini au niveau {level} pour {author_name}.", color=discord.Color.green())
        await ctx.send(embed=embed_ok)
        await self.dump_data_to_console(ctx)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        user_id = str(member.id)
        removed = False
        if user_id in self.jobs_data:
            del self.jobs_data[user_id]
            removed = True
        else:
            for key in list(self.jobs_data.keys()):
                if not key.isdigit() and self.jobs_data[key].get("name", "").lower() == member.display_name.lower():
                    del self.jobs_data[key]
                    removed = True
                    break
        if removed:
            self.save_data_local()
            console_channel = discord.utils.get(member.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
            if console_channel:
                data_str = json.dumps(self.jobs_data, indent=4, ensure_ascii=False)
                if len(data_str) < 1900:
                    await console_channel.send(f"===BOTJOBS===\n```json\n{data_str}\n```")
                else:
                    temp_path = self._as_temp_file(data_str)
                    await console_channel.send("===BOTJOBS=== (fichier)", file=discord.File(fp=temp_path, filename="jobs_data.json"))
                    os.remove(temp_path)

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
                "- `!job liste metier` : Afficher la liste des noms de métiers connus.\n"
                "- `!job <pseudo|@mention>` : Afficher les métiers d'un joueur.\n"
                "- `!job <job_name>` : Rechercher un métier et voir qui l'a.\n"
                "- `!job <job_name> <niveau>` : Ajouter ou mettre à jour votre métier (nom multi-mots accepté).\n"
                "- `!job del <job_name>` : Supprimer l'un de vos métiers.\n"
                "- `!job add <job_name> <niveau>` : Alias d'ajout/mise à jour.\n"
            )
            await ctx.send(embed=discord.Embed(title="Aide commande !job", description=usage_msg, color=0x00ffff))
            return

        if len(args) == 1 and args[0].lower() == "me":
            user_jobs = self.get_user_jobs(author_id)
            if not user_jobs:
                await ctx.send(embed=discord.Embed(title="Vos métiers", description=f"{author_name}, vous n'avez aucun métier enregistré.", color=discord.Color.orange()))
            else:
                emb = discord.Embed(title=f"Métiers de {author_name}", color=discord.Color.green())
                for job_name, lvl in sorted(user_jobs.items(), key=lambda kv: normalize_string(kv[0])):
                    emb.add_field(name=job_name, value=f"Niveau {lvl}", inline=True)
                await ctx.send(embed=emb)
            return

        if len(args) == 2 and args[0].lower() == "liste" and args[1].lower() == "metier":
            known = list(CANONICAL_JOBS_ORDERED)
            extra = set()
            for uid, data in self.jobs_data.items():
                for jn in data.get("jobs", {}).keys():
                    if jn not in known:
                        extra.add(jn)
            sections = []
            for cat in ["Récolte", "Artisanat", "Spécialisations"]:
                if JOB_CATEGORIES[cat]:
                    sections.append((cat, JOB_CATEGORIES[cat]))
            if extra:
                sections.append(("Autres", sorted(extra, key=lambda x: normalize_string(x))))
            embeds = []
            for title, bucket in sections:
                text = "\n".join(f"• {jn}" for jn in bucket)
                if len(text) < 4096:
                    embeds.append(discord.Embed(title=f"{title}", description=text, color=discord.Color.purple()))
                else:
                    current = ""
                    parts = []
                    for line in text.split("\n"):
                        if len(current) + len(line) + 1 > 4096:
                            parts.append(current)
                            current = line + "\n"
                        else:
                            current += line + "\n"
                    parts.append(current)
                    for i, c in enumerate(parts, start=1):
                        embeds.append(discord.Embed(title=f"{title} (part {i})", description=c, color=discord.Color.purple()))
            for e in embeds:
                await ctx.send(embed=e)
            return

        if len(args) == 1 and args[0].lower() == "liste":
            jobs_map = defaultdict(list)
            for uid, data in self.jobs_data.items():
                disp_name = data.get("name", f"ID {uid}")
                for jn, lv in data.get("jobs", {}).items():
                    jobs_map[jn].append((disp_name, lv))

            ordered_names = list(CANONICAL_JOBS_ORDERED)
            for j in sorted([n for n in jobs_map.keys() if n not in CANONICAL_JOBS_ORDERED], key=lambda x: normalize_string(x)):
                ordered_names.append(j)

            embed_count = 0
            for chunk in chunk_list(ordered_names, 25):
                embed_count += 1
                e = discord.Embed(title=f"Liste complète des métiers (part {embed_count})", color=discord.Color.blurple())
                for jn in chunk:
                    listing = ""
                    if jn in jobs_map:
                        for (player_name, lv) in sorted(jobs_map[jn], key=lambda x: normalize_string(x[0])):
                            listing += f"- **{player_name}** : {lv}\n"
                    e.add_field(name=jn, value=listing or "—", inline=False)
                await ctx.send(embed=e)
            return

        if len(args) >= 3 and args[0].lower() == "add":
            *job_name_tokens, level_str = args[1:]
            job_input = " ".join(job_name_tokens)
            try:
                level_int = int(level_str)
            except ValueError:
                await ctx.send("Syntaxe invalide. Exemple : `!job add Grand Sculpteur 100`.")
                return
            if level_int < JOB_MIN_LEVEL or level_int > JOB_MAX_LEVEL:
                await ctx.send(f"Le niveau doit être compris entre {JOB_MIN_LEVEL} et {JOB_MAX_LEVEL}.")
                return
            canonical = self.resolve_job_name(job_input)
            if canonical is None:
                await self.confirm_job_creation_flow(ctx, job_input, level_int, author_id, author_name)
                return
            if author_id not in self.jobs_data:
                self.jobs_data[author_id] = {"name": author_name, "jobs": {}}
            self.jobs_data[author_id]["name"] = author_name
            self.jobs_data[author_id]["jobs"][canonical] = level_int
            self.save_data_local()
            desc = f"Le métier **{canonical}** (initialement demandé : `{job_input}`) a été défini au niveau **{level_int}** pour **{author_name}**."
            warn = ""
            if canonical in SPECIALIZATION_SET and not any(b in self.jobs_data[author_id]["jobs"] for b in SPECIALIZATION_ALLOWED_BASE):
                warn = "\n⚠️ Vous n'avez aucun métier de base associé à cette spécialisation."
            emb = discord.Embed(title="Mise à jour du métier", description=desc + warn, color=discord.Color.green())
            await ctx.send(embed=emb)
            await self.dump_data_to_console(ctx)
            return

        if len(args) >= 2 and args[0].lower() == "del":
            job_input = " ".join(args[1:])
            canonical = self.resolve_job_name(job_input)
            if canonical is None:
                await ctx.send(f"Le métier `{job_input}` n'existe pas.")
                return
            user_jobs = self.get_user_jobs(author_id, author_name)
            if canonical not in user_jobs:
                await ctx.send(f"Vous n'avez pas le métier {canonical}.")
                return
            del self.jobs_data[author_id]["jobs"][canonical]
            self.save_data_local()
            await ctx.send(f"Le métier {canonical} a été supprimé pour {author_name}.")
            await self.dump_data_to_console(ctx)
            return

        if len(args) >= 2 and args[0].lower() not in ["liste", "me", "add", "del"]:
            *job_name_tokens, level_str = args
            job_input = " ".join(job_name_tokens)
            try:
                level_int = int(level_str)
            except ValueError:
                pass
            else:
                if level_int < JOB_MIN_LEVEL or level_int > JOB_MAX_LEVEL:
                    await ctx.send(f"Le niveau doit être compris entre {JOB_MIN_LEVEL} et {JOB_MAX_LEVEL}.")
                    return
                canonical = self.resolve_job_name(job_input)
                if canonical is None:
                    await self.confirm_job_creation_flow(ctx, job_input, level_int, author_id, author_name)
                else:
                    author_jobs = self.jobs_data.get(author_id, {"name": author_name, "jobs": {}})
                    author_jobs["name"] = author_name
                    author_jobs["jobs"][canonical] = level_int
                    self.jobs_data[author_id] = author_jobs
                    self.save_data_local()
                    warn = ""
                    if canonical in SPECIALIZATION_SET and not any(b in author_jobs["jobs"] for b in SPECIALIZATION_ALLOWED_BASE):
                        warn = "\n⚠️ Vous n'avez aucun métier de base associé à cette spécialisation."
                    emb = discord.Embed(title="Mise à jour du métier", description=f"Le métier **{canonical}** (initialement demandé : `{job_input}`) est maintenant défini au niveau **{level_int}** pour **{author_name}**." + warn, color=discord.Color.green())
                    await ctx.send(embed=emb)
                    await self.dump_data_to_console(ctx)
                return

        if len(args) == 1:
            query = args[0]
            mention_id = None
            m = re.fullmatch(r"<@!?(\d+)>", query)
            if m:
                mention_id = m.group(1)
            if mention_id and mention_id in self.jobs_data:
                user_jobs = self.get_user_jobs(mention_id)
                disp = self.jobs_data[mention_id].get("name", f"ID {mention_id}")
                if not user_jobs:
                    await ctx.send(f"{disp} n'a aucun métier enregistré.")
                else:
                    e = discord.Embed(title=f"Métiers de {disp}", color=discord.Color.gold())
                    for jn, lv in sorted(user_jobs.items(), key=lambda kv: normalize_string(kv[0])):
                        e.add_field(name=jn, value=f"Niveau {lv}", inline=True)
                    await ctx.send(embed=e)
                return

            found_user_id = None
            found_user_name = None
            for uid, data in self.jobs_data.items():
                if data.get("name", "").lower() == query.lower():
                    found_user_id = uid
                    found_user_name = data["name"]
                    break
            if found_user_id:
                user_jobs = self.get_user_jobs(found_user_id)
                if not user_jobs:
                    await ctx.send(f"{found_user_name} n'a aucun métier enregistré.")
                else:
                    e = discord.Embed(title=f"Métiers de {found_user_name}", color=discord.Color.gold())
                    for jn, lv in sorted(user_jobs.items(), key=lambda kv: normalize_string(kv[0])):
                        e.add_field(name=jn, value=f"Niveau {lv}", inline=True)
                    await ctx.send(embed=e)
                return

            job_map = defaultdict(list)
            for uid, data in self.jobs_data.items():
                display_name = data.get("name", f"ID {uid}")
                for jn, lv in data.get("jobs", {}).items():
                    job_map[jn].append((display_name, lv))
            qn = normalize_string(query)
            matching_jobs = []
            for jn in set(list(job_map.keys()) + CANONICAL_JOBS_ORDERED):
                if qn in normalize_string(jn):
                    matching_jobs.append(jn)
            if not matching_jobs:
                suggestions = self.suggest_similar_jobs(query)
                if suggestions:
                    s = "\n".join(f"• {x}" for x in suggestions)
                    await ctx.send(f"Aucun joueur nommé **{query}**. Métiers proches :\n{s}")
                else:
                    await ctx.send(f"Aucun joueur nommé **{query}** et aucun métier similaire.")
                return
            sorted_matches = sorted(matching_jobs, key=lambda x: (x not in CANONICAL_JOBS_ORDERED, normalize_string(x)))
            chunk_idx = 0
            for chunk in chunk_list(sorted_matches, 25):
                chunk_idx += 1
                emb = discord.Embed(title=f"Résultats de la recherche de métier (part {chunk_idx})", description=f"Recherche : {query}", color=discord.Color.blue())
                for jn in chunk:
                    listing = ""
                    for (player, lv) in sorted(job_map.get(jn, []), key=lambda x: normalize_string(x[0])):
                        listing += f"- **{player}** : {lv}\n"
                    emb.add_field(name=jn, value=listing or "—", inline=False)
                await ctx.send(embed=emb)
            return

        usage_msg = (
            "**Utilisation incorrecte**. Référez-vous ci-dessous :\n\n"
            "• `!job me` : Afficher vos métiers\n"
            "• `!job liste` : Afficher la liste de tous les métiers\n"
            "• `!job liste metier` : Afficher la liste de tous les noms de métiers\n"
            "• `!job <pseudo|@mention>` : Afficher les métiers d'un joueur\n"
            "• `!job <job_name>` : Rechercher un métier et voir qui l'a\n"
            "• `!job <job_name> <niveau>` : Ajouter ou mettre à jour votre métier\n"
            "• `!job del <job_name>` : Retirer un métier\n"
            "• `!job add <job_name> <niveau>` : Alias de la commande d'ajout\n"
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
    if getattr(bot, "_job_cog_loaded", False):
        return
    await bot.add_cog(JobCog(bot))
    bot._job_cog_loaded = True
