#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import discord
from discord.ext import commands
from typing import Dict, List

# Nom exact du fichier JSON contenant les données des membres
DATA_FILE = os.path.join(os.path.dirname(__file__), "players_data.json")


def charger_donnees() -> Dict[str, dict]:
    """
    Charge le contenu JSON depuis DATA_FILE sous forme de dict.
    Retourne {} si le fichier n'existe pas ou s'il y a une erreur de lecture.
    """
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
    """
    Sauvegarde 'data' dans le fichier JSON (players_data.json) avec indentation.
    """
    print(f"[DEBUG] Sauvegarde de {len(data)} enregistrements dans {DATA_FILE}")
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def chunk_list(lst, chunk_size=25):
    """
    Génère des sous-listes de taille maximale 'chunk_size'.
    Utile pour ne pas dépasser 25 fields par Embed.
    """
    for i in range(0, len(lst), chunk_size):
        yield lst[i : i + chunk_size]


class PlayersCog(commands.Cog):
    """
    Un Cog gérant l'enregistrement des personnages principaux et mules (alts).

    Commandes principales :
      - !membre principal <NomPerso>
      - !membre addmule <NomMule>
      - !membre delmule <NomMule>
      - !membre moi
      - !membre liste
      - !membre <pseudo_ou_mention> (affiche les infos d'un joueur)

    Nouvelles commandes Staff :
      - !recrutement <pseudo> : ajoute un nouveau joueur dans la base
      - !membre del <pseudo> : supprime un joueur et ses mules
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.persos_data = charger_donnees()
        print(f"[DEBUG] PlayersCog initialisé avec {len(self.persos_data)} enregistrements.")

    # ------------------------------------------------------------------------
    # LISTENER : Si un membre quitte le serveur, on supprime sa fiche si elle existe.
    # ------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """
        Événement déclenché quand un membre quitte le serveur.
        Si le membre est enregistré dans 'persos_data', on le retire de la base,
        puis on notifie le canal 𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭.
        """
        member_id = str(member.id)
        if member_id in self.persos_data:
            # Récupérer le nom enregistré (si présent)
            stored_name = self.persos_data[member_id].get("discord_name", member.display_name)

            # Supprimer l'entrée
            del self.persos_data[member_id]
            sauvegarder_donnees(self.persos_data)

            # Notification dans 𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭
            recruitment_channel = discord.utils.get(member.guild.text_channels, name="𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭")
            if recruitment_channel:
                await recruitment_channel.send(
                    f"Le membre **{stored_name}** a quitté le serveur. Sa fiche a été supprimée de la base."
                )
            else:
                print("Canal 𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭 introuvable. Impossible d'envoyer la notification.")

    # ========================
    # Commande Staff : !recrutement <pseudo>
    # ========================
    @commands.has_role("Staff")
    @commands.command(name="recrutement")
    async def recrutement_command(self, ctx: commands.Context, *, pseudo: str = None):
        """
        Ajoute un nouveau joueur dans la base (players_data.json),
        même s'il n'est pas (encore) sur le serveur.
        Réservé aux membres du rôle Staff.
        """
        if not pseudo:
            await ctx.send("Usage : !recrutement <PseudoNouveau>")
            return

        # Vérifier si ce pseudo existe déjà dans la base
        for uid, data in self.persos_data.items():
            existing_name = data.get("discord_name", "")
            if existing_name.lower() == pseudo.lower():
                await ctx.send(f"Le joueur **{pseudo}** existe déjà dans la base de données.")
                return

        # Générer un ID fictif, par exemple "recrue_<pseudo>"
        new_id = f"recrue_{pseudo.lower()}"

        # En cas de conflit improbable, on boucle
        suffix = 1
        while new_id in self.persos_data:
            new_id = f"recrue_{pseudo.lower()}_{suffix}"
            suffix += 1

        # Ajouter dans la base
        self.persos_data[new_id] = {
            "discord_name": pseudo,
            "main": "",
            "mules": []
        }
        sauvegarder_donnees(self.persos_data)

        await ctx.send(
            f"Le joueur **{pseudo}** a été créé dans la base de données (ID: {new_id}).\n"
            "Il pourra être associé ultérieurement à un utilisateur réel ou modifié via !membre principal."
        )

    # ========================
    # Groupe !membre
    # ========================
    @commands.group(name="membre", invoke_without_command=True)
    async def membre_group(self, ctx: commands.Context, *, arg: str = None):
        """
        Groupe de commandes !membre.
        Sans argument, affiche l'aide.
        Sinon, on interprète <arg> comme un pseudo ou une mention pour afficher la fiche.
        """
        if not arg:
            usage_msg = (
                "**Commandes disponibles :**\n"
                "`!membre principal <NomPerso>` : Définit ou met à jour votre perso principal.\n"
                "`!membre addmule <NomMule>` : Ajoute une mule.\n"
                "`!membre delmule <NomMule>` : Supprime une mule.\n"
                "`!membre moi` : Affiche votre perso principal + vos mules.\n"
                "`!membre liste` : Affiche la liste de tous les joueurs et leurs persos.\n"
                "`!membre <pseudo_ou_mention>` : Affiche les infos d'un joueur (perso + mules).\n\n"
                "**Commandes Staff :**\n"
                "`!recrutement <Pseudo>` : Ajoute un nouveau joueur dans la base.\n"
                "`!membre del <pseudo>` : Supprime un joueur (et ses mules) de la base.\n"
            )
            await ctx.send(usage_msg)
            return

        # Sinon, on interprète l'argument comme un pseudo ou une mention
        if len(ctx.message.mentions) == 1:
            mention = ctx.message.mentions[0]
            user_id = str(mention.id)
            await self._afficher_membre_joueur_embed(ctx, user_id, mention.display_name)
        else:
            # Recherche par pseudo dans la base
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
                await ctx.send(f"Aucun joueur ne correspond au pseudo **{arg}** dans la base de données.")

    # ========================
    # Sous-commande Staff : !membre del <pseudo>
    # ========================
    @membre_group.command(name="del")
    @commands.has_role("Staff")
    async def membre_del_member(self, ctx: commands.Context, *, pseudo: str = None):
        """
        Supprime un joueur (et ses mules) de la base de données.
        Réservé aux membres ayant le rôle "Staff".
        """
        if not pseudo:
            await ctx.send("Usage : !membre del <pseudo>")
            return

        # Vérifier mention
        mention = None
        if len(ctx.message.mentions) == 1:
            mention = ctx.message.mentions[0]

        # Chercher l'ID dans la base
        target_id = None
        target_name = None

        if mention is not None:
            # On supprime via l'ID de la personne mentionnée
            mention_id = str(mention.id)
            if mention_id in self.persos_data:
                target_id = mention_id
                target_name = self.persos_data[mention_id].get("discord_name", "")
        else:
            # On cherche par pseudo
            for uid, data in self.persos_data.items():
                stored_name = data.get("discord_name", "")
                if stored_name.lower() == pseudo.lower():
                    target_id = uid
                    target_name = stored_name
                    break

        if not target_id:
            await ctx.send(f"Impossible de trouver **{pseudo}** dans la base de données.")
            return

        # Suppression
        del self.persos_data[target_id]
        sauvegarder_donnees(self.persos_data)

        await ctx.send(
            f"Le joueur **{target_name or pseudo}** (ID: {target_id}) a été supprimé avec ses mules de la base de données."
        )

    # ========================
    # Sous-commandes Membres
    # ========================
    @membre_group.command(name="principal")
    async def membre_principal(self, ctx: commands.Context, *, nom_perso: str = None):
        """
        !membre principal <NomPerso> : Définit ou met à jour votre perso principal.
        """
        if not nom_perso:
            await ctx.send("Veuillez préciser le nom de votre personnage principal. Ex: !membre principal MonPerso")
            return

        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name

        # Tente d'aligner un enregistrement fictif sur l'ID réel
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

        sauvegarder_donnees(self.persos_data)
        await ctx.send(f"Votre personnage principal est maintenant **{nom_perso}**.")

    @membre_group.command(name="addmule")
    async def membre_addmule(self, ctx: commands.Context, *, nom_mule: str = None):
        """
        !membre addmule <NomMule> : Ajoute une mule.
        """
        if not nom_mule:
            await ctx.send("Veuillez préciser le nom de la mule. Ex: !membre addmule MuleDuFeu")
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
        sauvegarder_donnees(self.persos_data)

        await ctx.send(f"La mule **{nom_mule}** a été ajoutée pour **{author_name}**.")

    @membre_group.command(name="delmule")
    async def membre_delmule(self, ctx: commands.Context, *, nom_mule: str = None):
        """
        !membre delmule <NomMule> : Supprime une mule de votre liste.
        """
        if not nom_mule:
            await ctx.send("Veuillez préciser la mule à supprimer. Ex: !membre delmule MuleDuFeu")
            return

        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)

        if author_id not in self.persos_data:
            await ctx.send("Vous n'êtes pas encore enregistré (pas de perso principal).")
            return

        mules_list = self.persos_data[author_id].get("mules", [])
        if nom_mule not in mules_list:
            await ctx.send(f"La mule **{nom_mule}** n'est pas dans votre liste.")
            return

        mules_list.remove(nom_mule)
        self.persos_data[author_id]["mules"] = mules_list
        sauvegarder_donnees(self.persos_data)

        await ctx.send(f"La mule **{nom_mule}** a été retirée de votre liste.")

    @membre_group.command(name="moi")
    async def membre_moi(self, ctx: commands.Context):
        """
        !membre moi : Affiche votre fiche (perso principal + mules) en embed.
        """
        author_id = str(ctx.author.id)
        author_name = ctx.author.display_name
        self._verifier_et_fusionner_id(author_id, author_name)

        if author_id not in self.persos_data:
            await ctx.send("Vous n'êtes pas encore enregistré. Faites !membre principal <NomPerso> d'abord.")
            return

        data = self.persos_data[author_id]
        await self._envoyer_fiche_embed(ctx, user_id=author_id, user_name=author_name, data=data)

    @membre_group.command(name="liste")
    async def membre_liste(self, ctx: commands.Context):
        """
        !membre liste : Affiche la liste de tous les joueurs enregistrés sous forme d'embeds.
        """
        if not self.persos_data:
            await ctx.send("Aucun joueur n'est enregistré pour le moment.")
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

    # ------------------------------------------------------------------------
    # Méthodes internes
    # ------------------------------------------------------------------------

    async def _afficher_membre_joueur_embed(self, ctx: commands.Context, user_id: str, user_name: str):
        """
        Affiche la fiche d'un joueur donné (user_id déjà présent dans self.persos_data)
        sous forme d'embed.
        """
        if user_id not in self.persos_data:
            await ctx.send(f"{user_name} n'a pas encore enregistré de personnage.")
            return

        data = self.persos_data[user_id]
        await self._envoyer_fiche_embed(ctx, user_id, user_name, data)

    async def _envoyer_fiche_embed(self, ctx: commands.Context, user_id: str, user_name: str, data: dict):
        """
        Envoie un embed contenant la fiche (perso principal + mules) pour un joueur.
        """
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
        """
        Vérifie si 'discord_name' existe déjà sous un ID fictif et fusionne l'entrée sur 'vrai_id'.
        """
        if vrai_id in self.persos_data:
            return

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

    # ------------------------------------------------------------------------
    # NOUVELLE MÉTHODE PUBLIQUE pour un enregistrement automatique
    # ------------------------------------------------------------------------
    def auto_register_member(self, discord_id: int, discord_display_name: str, dofus_pseudo: str):
        """
        Enregistre automatiquement un nouveau joueur dans la base :
        - Fusionne si un enregistrement fictif existait déjà pour 'discord_display_name'.
        - Définit le 'main' au pseudo Dofus indiqué.
        """
        author_id = str(discord_id)
        self._verifier_et_fusionner_id(author_id, discord_display_name)

        # Crée l'entrée si elle n'existe pas
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


def setup(bot: commands.Bot):
    """
    Pour charger ce Cog : bot.load_extension('players')
    """
    bot.add_cog(PlayersCog(bot))
