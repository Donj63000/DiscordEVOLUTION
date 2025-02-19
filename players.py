#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import discord
from discord.ext import commands
from typing import Dict, List
from collections import defaultdict

DATA_FILE = os.path.join(os.path.dirname(__file__), "players_data.json")
CONSOLE_CHANNEL_NAME = "console"  # Nom du salon où l'on stocke le JSON
PLAYERS_MARKER = "===PLAYERSDATA==="  # Marqueur permettant d'identifier le message JSON

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
    """
    Un Cog gérant l'enregistrement des personnages principaux et mules (alts),
    avec stockage JSON dans le salon #console (marqueur ===PLAYERSDATA===)
    et en local (players_data.json) en fallback.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.persos_data = {}     # on initialise à vide
        self.initialized = False  # pour différer le chargement
        print("[DEBUG] PlayersCog initialisé (avant lecture console).")

    async def cog_load(self):
        """
        Méthode spéciale appelée automatiquement en discord.py 2.x
        une fois que le cog est chargé par le bot.
        On tente de recharger les données depuis #console,
        sinon on se rabat sur le fichier local.
        """
        await self.initialize_data()

    async def initialize_data(self):
        console_channel = discord.utils.get(self.bot.get_all_channels(), name=CONSOLE_CHANNEL_NAME)
        found_in_console = False

        if console_channel:
            # On parcourt les 1000 derniers messages pour trouver le dernier dump
            async for msg in console_channel.history(limit=1000):
                if msg.author == self.bot.user and PLAYERS_MARKER in msg.content:
                    try:
                        # On cherche le bloc JSON entre ```json\n et \n```
                        start_idx = msg.content.index("```json\n") + len("```json\n")
                        end_idx = msg.content.rindex("\n```")
                        raw_json = msg.content[start_idx:end_idx]
                        data_temp = json.loads(raw_json)
                        self.persos_data = data_temp
                        print("[DEBUG] Données récupérées depuis le salon #console.")
                        found_in_console = True
                        break
                    except Exception as e:
                        print(f"[DEBUG] Erreur lors du parsing JSON depuis console: {e}")
                        pass

        if not found_in_console:
            # Fallback : on essaye de charger le fichier local
            self.persos_data = charger_donnees()
            if self.persos_data:
                print("[DEBUG] Données chargées depuis le fichier local (fallback).")
            else:
                print("[DEBUG] Aucune donnée trouvée ni en console ni en local.")

        self.initialized = True
        print(f"[DEBUG] initialize_data terminé. {len(self.persos_data)} enregistrements.")

    async def dump_data_to_console(self, ctx: commands.Context = None):
        """
        Envoie la version courante de self.persos_data
        dans le salon #console sous forme de message.
        Si data > 1900 caractères, on l'envoie en fichier.
        """
        # On refait un dump local aussi
        sauvegarder_donnees(self.persos_data)

        # Si pas de contexte ou pas de guilde, on ne peut pas poster
        if not ctx or not ctx.guild:
            return

        console_channel = discord.utils.get(ctx.guild.text_channels, name=CONSOLE_CHANNEL_NAME)
        if not console_channel:
            print("[DEBUG] Salon #console introuvable, impossible de publier le JSON.")
            return

        data_str = json.dumps(self.persos_data, indent=4, ensure_ascii=False)

        if len(data_str) < 1900:
            # On insère le marqueur ===PLAYERSDATA=== pour repérer ce message
            message_content = f"{PLAYERS_MARKER}\n```json\n{data_str}\n```"
            await console_channel.send(message_content)
        else:
            # Envoi en fichier si trop long
            temp_file_path = self._as_temp_file(data_str)
            await console_channel.send(
                f"{PLAYERS_MARKER} (fichier)",
                file=discord.File(fp=temp_file_path, filename="players_data.json")
            )

    def _as_temp_file(self, data_str: str) -> str:
        """
        Écrit data_str dans un fichier temporaire et retourne son chemin.
        """
        temp_filename = "temp_players_data.json"
        with open(temp_filename, "w", encoding="utf-8") as tmp:
            tmp.write(data_str)
        return temp_filename

    # -----------------------------------------------------------
    # Reprise des commandes existantes, en veillant à appeler
    # dump_data_to_console(ctx) après chaque modification
    # -----------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Quand un membre quitte le serveur, on le retire de la base si présent.
        Puis on notifie #𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭.
        """
        member_id = str(member.id)
        if member_id in self.persos_data:
            stored_name = self.persos_data[member_id].get("discord_name", member.display_name)
            del self.persos_data[member_id]
            sauvegarder_donnees(self.persos_data)
            # Pas de 'ctx' ici, donc on ne dump pas dans #console
            # (ou alors on trouverait un moyen d'avoir un ctx/guild).
            # On notifie le salon recrutement
            recruitment_channel = discord.utils.get(member.guild.text_channels, name="𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭")
            if recruitment_channel:
                await recruitment_channel.send(
                    f"Le membre **{stored_name}** a quitté le serveur. Sa fiche a été supprimée."
                )

    @commands.has_role("Staff")
    @commands.command(name="recrutement")
    async def recrutement_command(self, ctx: commands.Context, *, pseudo: str = None):
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
        await ctx.send(
            f"Le joueur **{pseudo}** a été créé dans la base (ID: {new_id})."
        )

    @commands.group(name="membre", invoke_without_command=True)
    async def membre_group(self, ctx: commands.Context, *, arg: str = None):
        if not arg:
            usage_msg = (
                "**Commandes disponibles :**\n"
                "`!membre principal <NomPerso>` : Définit ou met à jour votre perso principal.\n"
                "`!membre addmule <NomMule>` : Ajoute une mule.\n"
                "`!membre delmule <NomMule>` : Supprime une mule.\n"
                "`!membre moi` : Affiche votre perso principal + vos mules.\n"
                "`!membre liste` : Affiche la liste de tous les joueurs et leurs persos.\n"
                "`!membre <pseudo_ou_mention>` : Affiche la fiche d'un joueur.\n\n"
                "**Commandes Staff :**\n"
                "`!recrutement <Pseudo>` : Ajoute un nouveau joueur dans la base.\n"
                "`!membre del <pseudo>` : Supprime un joueur.\n"
            )
            await ctx.send(usage_msg)
            return

        # Si mention
        if len(ctx.message.mentions) == 1:
            mention = ctx.message.mentions[0]
            user_id = str(mention.id)
            await self._afficher_membre_joueur_embed(ctx, user_id, mention.display_name)
        else:
            found_user_id = None
            found_user_name = None
            for uid, data in self.persos_data.items():
                stored_name = data.get("discord_name", "")
                if stored_name.lower() == arg.lower():
                    found_user_id = uid
                    found_user_name = stored_name
                    break

            if found_user_id:
                await self._afficher_membre_joueur_embed(ctx, found_user_id, found_user_name)
            else:
                await ctx.send(f"Aucun joueur ne correspond au pseudo **{arg}** dans la base.")

    @membre_group.command(name="del")
    @commands.has_role("Staff")
    async def membre_del_member(self, ctx: commands.Context, *, pseudo: str = None):
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
        if not nom_perso:
            await ctx.send("Usage : !membre principal <NomPerso>")
            return

        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)

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

    def _verifier_et_fusionner_id(self, vrai_id: str, discord_name: str):
        # Si on a déjà un enregistrement sous ce vrai_id, on ne fait rien
        if vrai_id in self.persos_data:
            return

        # Sinon, on cherche s'il existe un enregistrement 'fictif'
        id_fictif = None
        for uid, data in self.persos_data.items():
            stored_name = data.get("discord_name", "").lower()
            if stored_name == discord_name.lower():
                id_fictif = uid
                break

        if id_fictif:
            self.persos_data[vrai_id] = self.persos_data[id_fictif]
            del self.persos_data[id_fictif]
            sauvegarder_donnees(self.persos_data)

    def auto_register_member(self, discord_id: int, discord_display_name: str, dofus_pseudo: str):
        author_id = str(discord_id)
        self._verifier_et_fusionner_id(author_id, discord_display_name)
        if author_id not in self.persos_data:
            self.persos_data[author_id] = {
                "discord_name": discord_display_name,
                "main": dofus_pseudo,
                "mules": []
            }
        else:
            self.persos_data[author_id]["discord_name"] = discord_display_name
            self.persos_data[author_id]["main"] = dofus_pseudo

        sauvegarder_donnees(self.persos_data)
        print(f"[DEBUG] auto_register_member : {discord_display_name} ({author_id}) --> main={dofus_pseudo}")

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayersCog(bot))
