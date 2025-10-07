#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
import discord
from discord.ext import commands
from typing import Dict, List, Optional, Tuple

DATA_FILE = os.path.join(os.path.dirname(__file__), "players_data.json")
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
CONSOLE_CHANNEL_ID = os.getenv("CHANNEL_CONSOLE_ID")
PLAYERS_MARKER = "===PLAYERSDATA==="

def charger_donnees() -> Dict[str, dict]:
    print(f"[DEBUG] Chemin absolu du fichier JSON : {DATA_FILE}")
    if not os.path.exists(DATA_FILE):
        print("[DEBUG] Le fichier JSON n'existe pas. On retourne un dict vide.")
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            print(f"[DEBUG] {len(data)} enregistrements chargés depuis {DATA_FILE}")
            return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[DEBUG] Erreur lors de la lecture du JSON : {e}")
        return {}

def sauvegarder_donnees(data: Dict[str, dict]):
    print(f"[DEBUG] Sauvegarde de {len(data)} enregistrements dans {DATA_FILE}")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def chunk_list(lst, chunk_size=25):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]

class PlayersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.persos_data = {}
        self.initialized = False
        self.console_channel: Optional[discord.TextChannel] = None
        self.console_message_id: Optional[int] = None
        self._init_task: Optional[asyncio.Task] = None
        self._init_lock = asyncio.Lock()
        print(f"[DEBUG] PlayersCog initialisé (avant lecture {CONSOLE_CHANNEL_NAME}).")

    async def cog_load(self):
        if self._init_task is None or self._init_task.done():
            self._init_task = asyncio.create_task(self.initialize_data())

    async def initialize_data(self):
        if self.initialized:
            return
        async with self._init_lock:
            if self.initialized:
                return
            try:
                await self.bot.wait_until_ready()
                self.console_channel = await self._resolve_console_channel()
                found_in_console = False
                if self.console_channel:
                    found_in_console = await self._load_data_from_console(self.console_channel)
                else:
                    print(f"[DEBUG] Salon #{CONSOLE_CHANNEL_NAME} introuvable au démarrage.")
                if not found_in_console:
                    self.persos_data = charger_donnees()
                    if self.persos_data:
                        print("[DEBUG] Données chargées depuis le fichier local (fallback).")
                    else:
                        print(f"[DEBUG] Aucune donnée trouvée ni en {CONSOLE_CHANNEL_NAME} ni en local.")
            except Exception as exc:
                print(f"[DEBUG] Erreur lors de l'initialisation des données joueurs : {exc}")
            finally:
                self.initialized = True
                print(f"[DEBUG] initialize_data terminé. {len(self.persos_data)} enregistrements.")

    async def dump_data_to_console(self, ctx: commands.Context = None):
        sauvegarder_donnees(self.persos_data)
        guild = ctx.guild if ctx and ctx.guild else None
        console_channel = await self._resolve_console_channel(guild)
        if not console_channel:
            print(f"[DEBUG] Salon #{CONSOLE_CHANNEL_NAME} introuvable, impossible de publier le JSON.")
            return
        self.console_channel = console_channel
        data_str = json.dumps(self.persos_data, indent=4, ensure_ascii=False)
        if len(data_str) < 1900:
            message_content = f"{PLAYERS_MARKER}\n```json\n{data_str}\n```"
            existing_message = await self._get_console_snapshot(console_channel)
            if existing_message:
                await existing_message.edit(content=message_content)
                self.console_message_id = existing_message.id
            else:
                sent_message = await console_channel.send(message_content)
                self.console_message_id = sent_message.id
        else:
            temp_file_path = self._as_temp_file(data_str)
            existing_message = await self._get_console_snapshot(console_channel)
            if existing_message:
                try:
                    await existing_message.delete()
                except discord.HTTPException as exc:
                    print(f"[DEBUG] Impossible de supprimer l'ancien snapshot console : {exc}")
            sent_message = await console_channel.send(
                f"{PLAYERS_MARKER} (fichier)",
                file=discord.File(fp=temp_file_path, filename="players_data.json")
            )
            self.console_message_id = sent_message.id

    def _as_temp_file(self, data_str: str) -> str:
        temp_filename = "temp_players_data.json"
        with open(temp_filename, "w", encoding="utf-8") as tmp:
            tmp.write(data_str)
        return temp_filename

    async def _resolve_console_channel(self, guild: Optional[discord.Guild] = None) -> Optional[discord.TextChannel]:
        channel: Optional[discord.abc.GuildChannel] = None
        if CONSOLE_CHANNEL_ID:
            try:
                channel_id = int(CONSOLE_CHANNEL_ID)
            except ValueError:
                print(f"[DEBUG] CHANNEL_CONSOLE_ID invalide : {CONSOLE_CHANNEL_ID}")
            else:
                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        channel = None
                if isinstance(channel, discord.TextChannel):
                    return channel
        target_guilds: List[discord.Guild] = []
        if guild:
            target_guilds.append(guild)
        target_guilds.extend(g for g in self.bot.guilds if g not in target_guilds)
        for g in target_guilds:
            channel = discord.utils.get(g.text_channels, name=CONSOLE_CHANNEL_NAME)
            if channel:
                return channel
        return None

    async def _load_data_from_console(self, console_channel: discord.TextChannel) -> bool:
        async for msg in console_channel.history(limit=1000):
            if msg.author != self.bot.user:
                continue
            if PLAYERS_MARKER in msg.content:
                data = self._extract_json_from_message(msg.content)
                if data is not None:
                    self.persos_data = data
                    self.console_message_id = msg.id
                    print(f"[DEBUG] Données récupérées depuis le salon #{CONSOLE_CHANNEL_NAME}.")
                    return True
            if msg.attachments:
                for attachment in msg.attachments:
                    if not attachment.filename.lower().endswith(".json"):
                        continue
                    try:
                        raw = await attachment.read()
                        data = json.loads(raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        print(f"[DEBUG] Erreur lors du parsing JSON joint depuis {CONSOLE_CHANNEL_NAME}: {exc}")
                        continue
                    self.persos_data = data
                    self.console_message_id = msg.id
                    print(f"[DEBUG] Données récupérées depuis le fichier joint dans #{CONSOLE_CHANNEL_NAME}.")
                    return True
        return False

    def _extract_json_from_message(self, content: str) -> Optional[Dict[str, dict]]:
        if "```json" not in content:
            return None
        try:
            start = content.index("```json") + len("```json")
            if content[start] == "\n":
                start += 1
            end = content.rindex("```")
            raw_json = content[start:end].strip()
            if not raw_json:
                return None
            return json.loads(raw_json)
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"[DEBUG] Erreur lors du parsing JSON depuis {CONSOLE_CHANNEL_NAME}: {exc}")
            return None

    async def _get_console_snapshot(self, console_channel: discord.TextChannel) -> Optional[discord.Message]:
        if self.console_message_id:
            try:
                message = await console_channel.fetch_message(self.console_message_id)
                if message.author == self.bot.user and PLAYERS_MARKER in message.content:
                    return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self.console_message_id = None
        async for msg in console_channel.history(limit=50):
            if msg.author == self.bot.user and PLAYERS_MARKER in msg.content:
                self.console_message_id = msg.id
                return msg
        return None

    async def _ensure_initialized(self):
        if self.initialized:
            return
        task = self._init_task
        if task:
            try:
                await task
            except Exception as exc:
                print(f"[DEBUG] Erreur lors de l'attente de l'initialisation : {exc}")
        if not self.initialized:
            await self.initialize_data()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._ensure_initialized()
        member_id = str(member.id)
        if member_id in self.persos_data:
            stored_name = self.persos_data[member_id].get("discord_name", member.display_name)
            del self.persos_data[member_id]
            await self.dump_data_to_console()
            recruitment_channel = discord.utils.get(member.guild.text_channels, name="📥 Recrutement 📥")
            if recruitment_channel:
                await recruitment_channel.send(
                    f"Le membre **{stored_name}** a quitté le serveur. Sa fiche a été supprimée."
                )

    @commands.has_role("Staff")
    @commands.command(name="recrutement")
    async def recrutement_command(self, ctx: commands.Context, *, pseudo: str = None):
        await self._ensure_initialized()
        if not pseudo:
            await ctx.send("Usage : !recrutement <PseudoNouveau>")
            return
        for uid, data in self.persos_data.items():
            existing_name = data.get("discord_name", "")
            if existing_name.lower() == pseudo.lower():
                await ctx.send(f"Le joueur **{pseudo}** existe déjà dans la base.")
                return
        new_id = f"recrue_{pseudo.lower()}"
        suffix = 1
        while new_id in self.persos_data:
            new_id = f"recrue_{pseudo.lower()}_{suffix}"
            suffix += 1
        self.persos_data[new_id] = {
            "discord_name": pseudo,
            "main": "",
            "mules": []
        }
        await self.dump_data_to_console(ctx)
        await ctx.send(f"Le joueur **{pseudo}** a été créé dans la base (ID: {new_id}).")

    @commands.group(name="membre", invoke_without_command=True)
    async def membre_group(self, ctx: commands.Context, *, arg: str = None):
        await self._ensure_initialized()
        if not arg:
            embed = discord.Embed(
                title="Commandes membres",
                description="Gérez les fiches de vos personnages Evolution.",
                color=discord.Color.blurple()
            )
            embed.add_field(
                name="Pour tous",
                value=(
                    "`!membre principal <NomPerso>` : Définit ou met à jour votre perso principal.\n"
                    "`!membre addmule <NomMule>` : Ajoute une mule.\n"
                    "`!membre delmule <NomMule>` : Supprime une mule.\n"
                    "`!membre moi` : Affiche votre fiche complète.\n"
                    "`!membre liste` : Affiche tous les joueurs enregistrés.\n"
                    "`!membre <pseudo/mention>` : Affiche la fiche d'un joueur."
                ),
                inline=False
            )
            embed.add_field(
                name="Staff",
                value=(
                    "`!recrutement <Pseudo>` : Ajoute un nouveau joueur dans la base.\n"
                    "`!membre del <pseudo>` : Supprime un joueur enregistré."
                ),
                inline=False
            )
            total = len(self.persos_data)
            if total:
                embed.set_footer(text=f"{total} joueur(s) enregistrés.")
            await ctx.send(embed=embed)
            return
        if len(ctx.message.mentions) == 1:
            mention = ctx.message.mentions[0]
            user_id = str(mention.id)
            await self._afficher_membre_joueur_embed(ctx, user_id, mention.display_name)
        else:
            match = self._find_member_by_name(arg)
            if match:
                if len(match) == 1:
                    found_user_id, found_user_name = match[0]
                    await self._afficher_membre_joueur_embed(ctx, found_user_id, found_user_name)
                else:
                    suggestions = ", ".join(name for _, name in match[:5])
                    if len(match) > 5:
                        suggestions += ", ..."
                    await ctx.send(
                        f"Plusieurs joueurs correspondent à **{arg}** : {suggestions}. "
                        "Précisez le pseudo complet ou utilisez une mention."
                    )
            else:
                await ctx.send(
                    f"Aucun joueur ne correspond au pseudo **{arg}** dans la base. "
                    "Essayez le nom complet, une mention ou consultez `!membre liste`."
                )

    @membre_group.command(name="del")
    @commands.has_role("Staff")
    async def membre_del_member(self, ctx: commands.Context, *, pseudo: str = None):
        await self._ensure_initialized()
        if not pseudo:
            await ctx.send("Usage : !membre del <pseudo>")
            return
        mention = None
        if len(ctx.message.mentions) == 1:
            mention = ctx.message.mentions[0]
        target_id = None
        target_name = None
        if mention is not None:
            mention_id = str(mention.id)
            if mention_id in self.persos_data:
                target_id = mention_id
                target_name = self.persos_data[mention_id].get("discord_name", "")
        else:
            for uid, data in self.persos_data.items():
                stored_name = data.get("discord_name", "")
                if stored_name.lower() == pseudo.lower():
                    target_id = uid
                    target_name = stored_name
                    break
        if not target_id:
            await ctx.send(f"Impossible de trouver **{pseudo}** dans la base.")
            return
        del self.persos_data[target_id]
        await self.dump_data_to_console(ctx)
        await ctx.send(f"Le joueur **{target_name or pseudo}** (ID: {target_id}) a été supprimé de la base.")

    @membre_group.command(name="principal")
    async def membre_principal(self, ctx: commands.Context, *, nom_perso: str = None):
        await self._ensure_initialized()
        if not nom_perso:
            await ctx.send("Usage : !membre principal <NomPerso>")
            return
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name, nom_perso)
        if author_id not in self.persos_data:
            self.persos_data[author_id] = {
                "discord_name": author_name,
                "main": nom_perso,
                "mules": []
            }
        else:
            self.persos_data[author_id]["discord_name"] = author_name
            self.persos_data[author_id]["main"] = nom_perso
        await self.dump_data_to_console(ctx)
        await ctx.send(f"Votre personnage principal est maintenant **{nom_perso}**.")

    @membre_group.command(name="addmule")
    async def membre_addmule(self, ctx: commands.Context, *, nom_mule: str = None):
        await self._ensure_initialized()
        if not nom_mule:
            await ctx.send("Usage : !membre addmule <NomMule>")
            return
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)
        if author_id not in self.persos_data:
            self.persos_data[author_id] = {
                "discord_name": author_name,
                "main": "",
                "mules": []
            }
        mules_list = self.persos_data[author_id].get("mules", [])
        if nom_mule in mules_list:
            await ctx.send(f"La mule **{nom_mule}** est déjà enregistrée.")
            return
        mules_list.append(nom_mule)
        self.persos_data[author_id]["mules"] = mules_list
        await self.dump_data_to_console(ctx)
        await ctx.send(f"La mule **{nom_mule}** a été ajoutée pour **{author_name}**.")

    @membre_group.command(name="delmule")
    async def membre_delmule(self, ctx: commands.Context, *, nom_mule: str = None):
        await self._ensure_initialized()
        if not nom_mule:
            await ctx.send("Usage : !membre delmule <NomMule>")
            return
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)
        if author_id not in self.persos_data:
            await ctx.send("Vous n'êtes pas encore enregistré.")
            return
        mules_list = self.persos_data[author_id].get("mules", [])
        if nom_mule not in mules_list:
            await ctx.send(f"La mule **{nom_mule}** n'est pas dans votre liste.")
            return
        mules_list.remove(nom_mule)
        self.persos_data[author_id]["mules"] = mules_list
        await self.dump_data_to_console(ctx)
        await ctx.send(f"La mule **{nom_mule}** a été retirée de votre liste.")

    @membre_group.command(name="moi")
    async def membre_moi(self, ctx: commands.Context):
        await self._ensure_initialized()
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)
        if author_id not in self.persos_data:
            await ctx.send("Vous n'êtes pas encore enregistré. Faites `!membre principal <NomPerso>` d'abord.")
            return
        data = self.persos_data[author_id]
        await self._envoyer_fiche_embed(ctx, author_id, author_name, data)

    @membre_group.command(name="liste")
    async def membre_liste(self, ctx: commands.Context):
        await self._ensure_initialized()
        if not self.persos_data:
            await ctx.send("Aucun joueur enregistré.")
            return
        all_keys = list(self.persos_data.keys())
        all_keys.sort(key=lambda k: self.persos_data[k].get("discord_name", "").lower())
        embed_count = 0
        for chunk in chunk_list(all_keys, 25):
            embed_count += 1
            embed = discord.Embed(
                title=f"Liste des joueurs (partie {embed_count})",
                color=discord.Color.green()
            )
            for uid in chunk:
                info = self.persos_data[uid]
                discord_name = info.get("discord_name", f"ID {uid}")
                main_name = info.get("main", "")
                mules_list = info.get("mules", [])
                perso_principal = main_name if main_name else "(non défini)"
                if mules_list:
                    mules_str = "\n".join(f"- {m}" for m in mules_list)
                else:
                    mules_str = "(Aucune)"
                field_value = (
                    f"**Perso principal** : {perso_principal}\n"
                    f"**Mules** :\n{mules_str}"
                )
                embed.add_field(
                    name=f"{discord_name} (ID {uid})",
                    value=field_value,
                    inline=False
                )
            await ctx.send(embed=embed)

        await ctx.send(
            f"{len(self.persos_data)} joueur(s) au total. Utilisez `!membre <pseudo>` pour une fiche détaillée."
        )

    async def _afficher_membre_joueur_embed(self, ctx: commands.Context, user_id: str, user_name: str):
        if user_id not in self.persos_data:
            await ctx.send(f"{user_name} n'a pas encore enregistré de personnage.")
            return
        data = self.persos_data[user_id]
        await self._envoyer_fiche_embed(ctx, user_id, user_name, data)

    async def _envoyer_fiche_embed(self, ctx: commands.Context, user_id: str, user_name: str, data: dict):
        main_name = data.get("main", "")
        mules_list = data.get("mules", [])
        embed = discord.Embed(
            title=f"Fiche de {user_name}",
            description=f"ID Discord : {user_id}",
            color=discord.Color.blue()
        )
        if main_name:
            embed.add_field(name="Personnage principal", value=main_name, inline=False)
        else:
            embed.add_field(name="Personnage principal", value="(non défini)", inline=False)
        if mules_list:
            mules_str = "\n".join(f"- {m}" for m in mules_list)
            embed.add_field(name="Mules", value=mules_str, inline=False)
        else:
            embed.add_field(name="Mules", value="(Aucune)", inline=False)
        await ctx.send(embed=embed)

    def _find_member_by_name(self, search: str) -> List[Tuple[str, str]]:
        search_lower = search.lower()
        results: List[Tuple[str, str]] = []
        exact_match: Optional[Tuple[str, str]] = None
        for uid, data in self.persos_data.items():
            stored_name = data.get("discord_name", "")
            main_name = data.get("main", "")
            if stored_name.lower() == search_lower or main_name.lower() == search_lower:
                exact_match = (uid, stored_name or main_name or uid)
                break
            if search_lower in stored_name.lower():
                results.append((uid, stored_name))
            elif main_name and search_lower in main_name.lower():
                display = stored_name if stored_name else f"(principal {main_name})"
                results.append((uid, display))
        if exact_match:
            return [exact_match]
        return results

    def _verifier_et_fusionner_id(self, vrai_id: str, *aliases: str):
        if vrai_id in self.persos_data:
            return
        alias_pairs: list[tuple[str, str]] = []
        for alias in aliases:
            if not alias:
                continue
            lowered = alias.lower()
            slug = ''.join(ch for ch in lowered if ch.isalnum())
            alias_pairs.append((lowered, slug))
        if not alias_pairs:
            return
        for uid, data in list(self.persos_data.items()):
            candidates = (
                data.get("discord_name", ""),
                data.get("main", ""),
            )
            for candidate in candidates:
                if not candidate:
                    continue
                cand_lower = candidate.lower()
                cand_slug = ''.join(ch for ch in cand_lower if ch.isalnum())
                for alias_lower, alias_slug in alias_pairs:
                    if (
                        cand_lower == alias_lower
                        or cand_lower in alias_lower
                        or alias_lower in cand_lower
                        or (cand_slug and cand_slug == alias_slug)
                    ):
                        if uid == vrai_id:
                            return
                        self.persos_data[vrai_id] = data
                        del self.persos_data[uid]
                        sauvegarder_donnees(self.persos_data)
                        return

    async def auto_register_member(self, discord_id: int, discord_display_name: str, dofus_pseudo: str):
        await self._ensure_initialized()
        author_id = str(discord_id)
        self._verifier_et_fusionner_id(author_id, discord_display_name, dofus_pseudo)
        if author_id not in self.persos_data:
            self.persos_data[author_id] = {
                "discord_name": discord_display_name,
                "main": dofus_pseudo,
                "mules": []
            }
        else:
            self.persos_data[author_id]["discord_name"] = discord_display_name
            self.persos_data[author_id]["main"] = dofus_pseudo
        await self.dump_data_to_console()
        print(f"[DEBUG] auto_register_member : {discord_display_name} ({author_id}) --> main={dofus_pseudo}")

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayersCog(bot))
