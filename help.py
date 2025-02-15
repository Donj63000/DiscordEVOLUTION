#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands

def chunk_text(text: str, max_size: int = 1024):
    lines = text.split('\n')
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_size:
            yield current_chunk
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n" + line
            else:
                current_chunk = line
    if current_chunk:
        yield current_chunk

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="aide", aliases=["help"])
    async def aide_command(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Liste des Commandes du Bot Evolution By Coca©",
            description=(
                "Bienvenue sur le bot de la guilde **Evolution** !\n\n"
                "Voici un récapitulatif de toutes les commandes disponibles, "
                "classées par catégories. Pour toute question ou besoin d’aide, "
                "n’hésitez pas à contacter un membre du Staff."
            ),
            color=discord.Color.blue()
        )

        embed.add_field(
            name=":bookmark_tabs: Mini-Guides & Commandes Racines",
            value=(
                "__**!ia**__\n"
                "> Guide sur l’IA (ex.: `!bot`, `!analyse`).\n\n"
                "__**!membre**__\n"
                "> Récap global des sous-commandes (ex.: `principal`, `addmule`).\n\n"
                "__**!job**__\n"
                "> Guide des sous-commandes liées aux métiers (ex.: `!job me`, `!job liste`).\n\n"
                "__**!rune**__\n"
                "> Outil de calcul (probabilités runes). Fonctionnalité partielle.\n\n"
                "__**!regles**__\n"
                "> Résumé simplifié du règlement d'Evolution.\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":sparkles: Commandes Générales",
            value=(
                "__**!ping**__\n"
                "> Vérifie que le bot répond (latence « Pong! »).\n\n"
                "__**!scan <URL>**__ *(Defender)*\n"
                "> Analyse manuellement un lien (Safe Browsing/VirusTotal), puis supprime la commande.\n\n"
                "__**!rune jet <valeur_jet> <stat>**__ *(Calcul Runes)*\n"
                "> Calcule les probabilités d'obtenir des runes (ex.: `!rune jet 30 force`).\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":busts_in_silhouette: Commandes Membres",
            value=(
                "__**!membre principal <NomPerso>**__\n"
                "> Définit ou met à jour votre personnage principal.\n\n"
                "__**!membre addmule <NomMule>**__\n"
                "> Ajoute une mule à votre fiche.\n\n"
                "__**!membre delmule <NomMule>**__\n"
                "> Retire une mule.\n\n"
                "__**!membre moi**__\n"
                "> Affiche votre fiche (principal + mules).\n\n"
                "__**!membre liste**__\n"
                "> Liste tous les joueurs, leurs persos et leurs mules.\n\n"
                "__**!membre <pseudo_ou_mention>**__\n"
                "> Affiche la fiche d'un joueur précis.\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":hammer_and_pick: Commandes Job",
            value=(
                "__**!job me**__\n"
                "> Affiche vos métiers et niveaux.\n\n"
                "__**!job liste**__\n"
                "> Liste complète des métiers et qui les possède.\n\n"
                "__**!job liste metier**__\n"
                "> Affiche simplement la liste des noms de métiers recensés.\n\n"
                "__**!job <pseudo>**__\n"
                "> Donne les métiers d'un joueur.\n\n"
                "__**!job <job_name>**__\n"
                "> Indique qui possède ce métier (ex.: `!job Paysan`).\n\n"
                "__**!job <job_name> <niveau>**__\n"
                "> Ajoute ou modifie l’un de vos métiers. Ex.: `!job Boulanger 100`.\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":tickets: Commande Ticket",
            value=(
                "__**!ticket**__\n"
                "> Lance en MP une procédure pour contacter le Staff (problème, aide, suggestion...).\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":robot: Commandes IA",
            value=(
                "__**!bot <message>**__\n"
                "> Fait appel à l’IA (gemini-1.5-pro) avec le contexte des derniers messages.\n\n"
                "__**!analyse**__\n"
                "> Analyse/résume les 100 derniers messages du salon.\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":bar_chart: Commandes Sondage",
            value=(
                "__**!sondage <Titre> ; <Choix1> ; ... ; temps=JJ:HH:MM>**__\n"
                "> Crée un sondage dans #annonces (mention @everyone). Se ferme au bout du délai (jours:heures:minutes) "
                "ou manuellement.\n\n"
                "__**!close_sondage <message_id>**__\n"
                "> Clôture manuellement le sondage (affiche résultats et édite l'embed d'origine en [Clôturé]).\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":calendar_spiral: Commandes Activités",
            value=(
                "__**!activite creer <Titre> <JJ/MM/AAAA HH:MM> [desc]**__\n"
                "> Crée une activité (donjon/sortie) + rôle éphémère + annonce dans #organisation.\n\n"
                "__**!activite liste**__\n"
                "> Montre les activités à venir (limite 8 participants). Inscriptions par réactions.\n\n"
                "__**!activite info <id>**__\n"
                "> Montre les détails d’une activité (date, organisateur, participants...).\n\n"
                "__**!activite join <id>**__ / __**!activite leave <id>**__\n"
                "> S’inscrire / Se désinscrire d’une activité.\n\n"
                "__**!activite annuler <id>**__ / __**!activite modifier <id> ...**__\n"
                "> Annule ou modifie (date/description) une activité (réservé au créateur ou admin).\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":shield: Commandes Staff (Rôle requis)",
            value=(
                "__**!staff**__\n"
                "> Liste des membres Staff enregistrés/mentionnés.\n\n"
                "__**!annonce <texte>**__\n"
                "> Publie une annonce stylée dans #annonces (mention @everyone).\n\n"
                "__**!event <texte>**__\n"
                "> Organise un événement, publié dans #organisation (mention Membre validé).\n\n"
                "__**!recrutement <pseudo>**__\n"
                "> Ajoute un nouveau joueur dans la base.\n\n"
                "__**!membre del <pseudo>**__\n"
                "> Supprime un joueur (et ses mules) de la base.\n"
            ),
            inline=False
        )

        embed.set_footer(
            text=(
                "Pour réafficher cette liste à tout moment, utilisez !aide ou !help.\n"
                "Besoin d’aide ? Contactez un membre du Staff !"
            )
        )

        await ctx.send(embed=embed)

    @commands.command(name="regles")
    async def regles_command(self, ctx: commands.Context):
        summary_text = (
            "------------------------------------------------------------------------------------------------------\n"
            "Résumé Simplifié des Règles d'Evolution  ✨\n"
            "------------------------------------------------------------------------------------------------------\n"
            "🌟 **Vision et Objectifs**\n\n"
            "🎮 Une guilde axée sur le respect, l'entraide et le plaisir de jouer ensemble.\n"
            "🤝 Cohésion, flexibilité et ambiance communautaire dynamique.\n"
            "🏆 Soutenir une participation équilibrée, sans pression excessive.\n"
            "📊 Contributions des Membres\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**Gestion de l'XP**\n\n"
            "⚔️ Niveau < 150 : 1% minimum.\n"
            "🛡️ Niveau > 150 : 5% minimum.\n"
            "🎉 Plus de 1 000 000 XP contribué : liberté de gérer votre exp guilde [Ceci est un essai]\n"
            "🛠️ Dérogations possibles pour progression ou contraintes personnelles.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**Gestion des Percepteurs**\n\n"
            "🏰 500 000 XP minimum pour poser un percepteur.\n"
            "🕒 Pose limitée à 1 percepteur par membre, durée max : 8h.\n"
            "🛡️ Défense collective obligatoire en cas d'attaque.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**Reconnaissance**\n\n"
            "🎖️ Contributions valorisées par des rôles ou distinctions sur Discord.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**🎉 Activités et Événements**\n\n"
            "⚔️ Tournois PvP, défis communautaires et événements réguliers ouverts à tous.\n"
            "💡 Les membres peuvent proposer leurs idées d’événements sur Discord.\n"
            "🌟 Valorisation des initiatives des participants avec des distinctions spéciales.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**💬 Communication**\n\n"
            "📱 Discord obligatoire pour suivre les annonces et participer aux discussions.\n"
            "🤐 Restez respectueux et constructifs, comportements toxiques interdits.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**🌀 Multi-Guilde**\n\n"
            "🛡️ Priorité à Evolution, engagement primordial.\n"
            "📣 Informez les meneurs si vous avez des personnages dans d'autres guildes.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**🤝 Respect et Sanctions**\n\n"
            "🫂 Respect, entraide et esprit d’équipe attendus de tous.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**❗ Infractions classées :**\n\n"
            "⚠️ Mineures : avertissements ou restrictions de droits.\n"
            "🚫 Modérées : suspensions temporaires.\n"
            "⛔ Graves : exclusions temporaires ou définitives.\n"
            "👮  Le staff : représente la guilde et a le pouvoir et les droits pour agir quand il le faut\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**🛠️ Recrutement et Rôles**\n\n"
            "🔑 Staff : Bras Droits, gauches et Trésoriers gèrent les recrutements, activités, percepteurs et peuvent faire appliquer les règles.\n"
            "⭐ Rejoindre le staff : ancienneté, implication et alignement sur les valeurs requises.\n"
            "👑 Meneurs (Coca-Cola et Thalata) supervisent les décisions finales.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**🏛️ Structure de la Guilde**\n\n"
            "👑 Meneurs : Coca-Cola & Thalata – décisions stratégiques.\n"
            "🛡️ Staff : Bras Droits & Trésoriers – gestion quotidienne de la guilde.\n"
            "🫂 Membres : Acteurs essentiels de la communauté.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**📜 Droits et Devoirs des Membres**\n\n"
            "✅ Droits : Liberté d’expression, transparence, possibilité de contester les décisions.\n"
            "📌 Devoirs : Respect des règles, communication et participation minimale.\n"
            "------------------------------------------------------------------------------------------------------\n"
            "**✨ Conclusion**\n\n"
            "Evolution c’est une nouvelle ère centrée sur la collaboration, l’innovation et le respect des styles de jeu de chacun. 🌟\n"
            "Ensemble, faisons grandir la guilde dans une ambiance conviviale et unie ! 🤩\n"
            "------------------------------------------------------------------------------------------------------\n"
            "Si vous cherchez le détail d'une règle, c'est au-dessus ⬆️\n"
            "------------------------------------------------------------------------------------------------------\n"
        )

        embed = discord.Embed(
            title="Résumé Simplifié du Règlement d'Evolution",
            description="**Voici un résumé (en plusieurs parties) :**",
            color=discord.Color.gold()
        )
        embed.set_footer(text="Pour plus de détails, consultez le règlement complet ou demandez au Staff.")

        chunks = list(chunk_text(summary_text, max_size=1024))

        for i, chunk in enumerate(chunks, start=1):
            embed.add_field(
                name=f"Règlement (Partie {i})",
                value=chunk,
                inline=False
            )

        await ctx.send(embed=embed)

def setup(bot: commands.Bot):
    bot.add_cog(HelpCog(bot))
