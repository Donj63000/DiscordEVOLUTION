#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import asyncio
import discord
from discord.ext import commands
from typing import Dict, List, Optional, Tuple

from utils.channel_resolver import resolve_text_channel

DATA_ROOT = os.path.dirname(__file__)
DATA_FILE = os.path.join(DATA_ROOT, "players_data.json")
LEGACY_DATA_FILE = os.path.join(DATA_ROOT, "data", "players_data.json")
DATA_FILE_NAME = "players_data.json"
CONSOLE_CHANNEL_NAME = os.getenv("CHANNEL_CONSOLE", "console")
CONSOLE_CHANNEL_ID = os.getenv("CHANNEL_CONSOLE_ID")
PLAYERS_MARKER = "===PLAYERSDATA==="
RECRUITMENT_CHANNEL_FALLBACK = os.getenv("RECRUTEMENT_CHANNEL_NAME") or "📋 Recrutement 📋"
NOT_REGISTERED_MESSAGE = "Vous n'êtes pas encore enregistré. Faites `!membre principal <NomPerso>` d'abord."

def _load_json_candidate(path: str) -> Optional[Dict[str, dict]]:
    if not os.path.exists(path):
        print(f"[DEBUG] Fichier JSON introuvable : {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[DEBUG] Erreur lors de la lecture du JSON {path} : {e}")
        return None
    if not isinstance(data, dict):
        print(f"[DEBUG] Format inattendu pour {path}.")
        return None
    print(f"[DEBUG] {len(data)} enregistrements chargés depuis {path}")
    return data


def charger_donnees() -> Dict[str, dict]:
    print(f"[DEBUG] Chemin absolu du fichier JSON : {DATA_FILE}")
    for candidate in (DATA_FILE, LEGACY_DATA_FILE):
        data = _load_json_candidate(candidate)
        if data is None:
            continue
        if candidate != DATA_FILE and not os.path.exists(DATA_FILE):
            sauvegarder_donnees(data)
            print(f"[DEBUG] Données migrées depuis {candidate}")
        return data
    print("[DEBUG] Aucun fichier JSON valide trouvé. On retourne un dict vide.")
    return {}

def sauvegarder_donnees(data: Dict[str, dict]):
    print(f"[DEBUG] Sauvegarde de {len(data)} enregistrements dans {DATA_FILE}")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def chunk_list(lst, chunk_size=25):
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]


def _parse_channel_id(raw_value: Optional[str]) -> Optional[int]:
    if not raw_value:
        return None
    digits = "".join(ch for ch in raw_value if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None

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
        async def ensure_pinned(message: discord.Message):
            try:
                if not message.pinned:
                    await message.pin()
            except Exception as exc:
                print(f"[DEBUG] Impossible d'épingler le snapshot console : {exc}")

        if len(data_str) < 1900:
            message_content = f"{PLAYERS_MARKER}\n```json\n{data_str}\n```"
            existing_message = await self._get_console_snapshot(console_channel)
            if existing_message:
                await existing_message.edit(content=message_content)
                self.console_message_id = existing_message.id
                await ensure_pinned(existing_message)
            else:
                sent_message = await console_channel.send(message_content)
                self.console_message_id = sent_message.id
                await ensure_pinned(sent_message)
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
                file=discord.File(fp=temp_file_path, filename=DATA_FILE_NAME)
            )
            self.console_message_id = sent_message.id
            await ensure_pinned(sent_message)

    def _as_temp_file(self, data_str: str) -> str:
        temp_filename = "temp_players_data.json"
        with open(temp_filename, "w", encoding="utf-8") as tmp:
            tmp.write(data_str)
        return temp_filename

    async def _resolve_console_channel(self, guild: Optional[discord.Guild] = None) -> Optional[discord.TextChannel]:
        channel: Optional[discord.abc.GuildChannel] = None
        channel_id = _parse_channel_id(CONSOLE_CHANNEL_ID)
        if channel_id:
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
            resolved = resolve_text_channel(
                g,
                id_env="CHANNEL_CONSOLE_ID",
                name_env="CHANNEL_CONSOLE",
                default_name=CONSOLE_CHANNEL_NAME,
            )
            if resolved:
                return resolved
        return None

    async def _load_data_from_console(self, console_channel: discord.TextChannel) -> bool:
        try:
            pinned_messages = await console_channel.pins()
        except Exception:
            pinned_messages = []
        candidates: List[discord.Message] = []
        seen_ids = set()
        for message in pinned_messages:
            if message.id not in seen_ids:
                candidates.append(message)
                seen_ids.add(message.id)
        async for message in console_channel.history(limit=1000, oldest_first=False):
            if message.id not in seen_ids:
                candidates.append(message)
                seen_ids.add(message.id)
        best_data: Optional[Dict[str, dict]] = None
        best_message: Optional[discord.Message] = None
        for message in candidates:
            if message.author != self.bot.user:
                continue
            content = message.content or ""
            has_marker = PLAYERS_MARKER in content
            has_file = any(att.filename == DATA_FILE_NAME for att in message.attachments)
            if not (has_marker or has_file):
                continue
            parsed: Optional[Dict[str, dict]] = None
            for attachment in message.attachments:
                if attachment.filename != DATA_FILE_NAME:
                    continue
                try:
                    raw = await attachment.read()
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    print(f"[DEBUG] Erreur lors du parsing JSON joint depuis {CONSOLE_CHANNEL_NAME}: {exc}")
                    continue
                if self._looks_like_players_data(data):
                    parsed = data
                    break
            if parsed is None and "```json" in content:
                data = self._extract_json_from_message(content)
                if data is not None and self._looks_like_players_data(data):
                    parsed = data
            if parsed is None:
                continue
            if not best_data or len(parsed) > len(best_data):
                best_data = parsed
                best_message = message
        if best_data and best_message:
            self.persos_data = best_data
            self.console_message_id = best_message.id
            print(
                f"[DEBUG] Données récupérées depuis #{CONSOLE_CHANNEL_NAME} (message {best_message.id}, {len(best_data)} entrées)."
            )
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

    def _looks_like_players_data(self, data: dict) -> bool:
        if not isinstance(data, dict) or not data:
            return False
        forbidden = {
            "messages",
            "edits",
            "deletions",
            "reactions_added",
            "reactions_removed",
            "voice",
            "presence",
            "logs",
        }
        if any(key in data for key in forbidden):
            return False
        for value in data.values():
            if isinstance(value, dict) and any(k in value for k in ("discord_name", "main", "mules")):
                return True
        return False

    def _is_console_snapshot(self, message: discord.Message) -> bool:
        if message.author != self.bot.user:
            return False
        content = message.content or ""
        if PLAYERS_MARKER in content:
            return True
        if any(att.filename == DATA_FILE_NAME for att in getattr(message, "attachments", []) or []):
            return True
        return False

    async def _get_console_snapshot(self, console_channel: discord.TextChannel) -> Optional[discord.Message]:
        if self.console_message_id:
            try:
                message = await console_channel.fetch_message(self.console_message_id)
                if self._is_console_snapshot(message):
                    return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                self.console_message_id = None
        try:
            pinned = await console_channel.pins()
        except Exception:
            pinned = []
        for msg in pinned:
            if self._is_console_snapshot(msg):
                self.console_message_id = msg.id
                return msg
        async for msg in console_channel.history(limit=200):
            if self._is_console_snapshot(msg):
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
            recruitment_channel = resolve_text_channel(
                member.guild,
                id_env="RECRUTEMENT_CHANNEL_ID",
                name_env="RECRUTEMENT_CHANNEL_NAME",
                default_name=RECRUITMENT_CHANNEL_FALLBACK,
            )
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
        existing = {mule.lower() for mule in mules_list if isinstance(mule, str)}
        if nom_mule.lower() in existing:
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
            await ctx.send(NOT_REGISTERED_MESSAGE)
            return
        mules_list = self.persos_data[author_id].get("mules", [])
        index_to_remove = None
        for idx, mule in enumerate(mules_list):
            if isinstance(mule, str) and mule.lower() == nom_mule.lower():
                index_to_remove = idx
                break
        if index_to_remove is None:
            await ctx.send(f"La mule **{nom_mule}** n'est pas dans votre liste.")
            return
        removed_mule = mules_list.pop(index_to_remove)
        self.persos_data[author_id]["mules"] = mules_list
        await self.dump_data_to_console(ctx)
        await ctx.send(f"La mule **{removed_mule}** a été retirée de votre liste.")

    @membre_group.command(name="moi")
    async def membre_moi(self, ctx: commands.Context):
        await self._ensure_initialized()
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)
        if author_id not in self.persos_data:
            await ctx.send(NOT_REGISTERED_MESSAGE)
            return
        data = self.persos_data[author_id]
        await self._envoyer_fiche_embed(ctx, author_id, author_name, data)

    @membre_group.command(name="liste")
    async def membre_liste(self, ctx: commands.Context):
        await self._ensure_initialized()
        filtered_ids = [uid for uid in self.persos_data if uid.isdigit()]
        if not filtered_ids:
            await ctx.send("Aucun joueur enregistré.")
            return
        filtered_ids.sort(key=lambda k: self.persos_data[k].get("discord_name", "").lower())
        embed_count = 0
        for chunk in chunk_list(filtered_ids, 25):
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
            f"{len(filtered_ids)} joueur(s) au total. Utilisez `!membre <pseudo>` pour une fiche détaillée."
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

    def _normalize_token(self, value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum())

    def _find_member_by_name(self, search: str) -> List[Tuple[str, str]]:
        query = (search or "").strip()
        if not query:
            return []
        if query.startswith("<@") and query.endswith(">"):
            payload = query[2:-1]
            if payload.startswith("!"):
                payload = payload[1:]
            if payload.isdigit():
                user_id = payload
                data = self.persos_data.get(user_id)
                if data:
                    label = data.get("discord_name") or data.get("main") or user_id
                    return [(user_id, label)]
        if query.isdigit() and query in self.persos_data:
            data = self.persos_data[query]
            label = data.get("discord_name") or data.get("main") or query
            return [(query, label)]
        search_lower = query.lower()
        search_slug = self._normalize_token(query)
        results: List[Tuple[str, str, int]] = []
        for uid, data in self.persos_data.items():
            stored_name = data.get("discord_name", "")
            main_name = data.get("main", "")
            candidates = [stored_name, main_name]
            for name in candidates:
                if not name:
                    continue
                norm = self._normalize_token(name)
                score = 0
                if name.lower() == search_lower or norm == search_slug:
                    return [(uid, stored_name or main_name or uid)]
                if norm and search_slug and norm.startswith(search_slug):
                    score = 1
                elif search_lower in name.lower():
                    score = 2
                elif search_slug and norm and search_slug in norm:
                    score = 3
                if score:
                    display = stored_name or main_name or uid
                    results.append((uid, display, score))
                    break
        results.sort(key=lambda item: item[2])
        return [(uid, label) for uid, label, _ in results]

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
