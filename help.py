#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands

def chunk_text(text: str, max_size: int = 3000):
    """
    Génère des segments de 'text' de taille maximale 'max_size'.
    Cette fonction évite de dépasser la limite d'embed Discord (4096 chars par description).
    """
    start = 0
    while start < len(text):
        yield text[start:start + max_size]
        start += max_size

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="aide", aliases=["help"])
    async def aide_command(self, ctx: commands.Context):
        """Affiche la liste complète des commandes du bot."""
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
                "> Ouvre une session privée avec Gemini 2.5 Pro.\n\n"
                "__**!iahelp**__\n"
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
                "__**!iaend**__\n"
                "> Termine votre session IA en cours.\n\n"
                "__**!analyse**__\n"
                "> Analyse/résume les 100 derniers messages du salon.\n"
            ),
            inline=False
        )

        embed.add_field(
            name=":bar_chart: Commandes Sondage",
            value=(
                "__**!sondage <Titre> ; <Choix1> ; ... ; temps=JJ:HH:MM>**__\n"
                "> Crée un sondage dans #📣 annonces 📣 (mention @everyone). Se ferme au bout du délai (jours:heures:minutes) "
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
                "> Crée une activité (donjon/sortie) + rôle éphémère + annonce dans #🌈 organisation 🌈.\n\n"
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
                "> Publie une annonce stylée dans #📣 annonces 📣 (mention @everyone).\n\n"
                "__**!event**__\n"
                "> Lance une discussion privée pour planifier un événement.\n"
                "> Après validation, un événement Discord programmé est créé et annoncé dans #🌈 organisation 🌈 (mention Membre validé).\n\n"
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
        """
        Affiche le règlement complet dans un ou plusieurs embeds, 
        sans duplication.
        """
        summary_text = (
            "✨ **Mise à jour du Règlement de la Guilde Evolution – Édition du 19/02/2025** ✨\n\n"
            "Bienvenue au sein de la guilde **Evolution** ! Ce règlement a pour but de garantir une ambiance "
            "conviviale, motivante et respectueuse, tout en favorisant l’implication de chacun. En rejoignant "
            "Evolution, vous acceptez de respecter ces règles, établies pour le bien de tous et la progression "
            "harmonieuse de la guilde. Nous comptons sur votre participation active, votre entraide et votre "
            "bonne humeur pour faire de cette guilde un endroit où il fait bon jouer ensemble.\n\n"

            "__**1. Respect et Convivialité 🤝**__\n"
            "**Respect mutuel** : Chaque membre se doit de respecter les autres, que ce soit en jeu ou sur Discord. "
            "Aucune insulte, propos discriminatoire (raciste, sexiste, etc.) ou comportement toxique ne sera toléré.\n"
            "**Politesse & bienveillance** : Le langage utilisé doit rester courtois. Le Staff et les membres veillent "
            "à maintenir une atmosphère positive où tout le monde se sent à l’aise.\n"
            "**Gestion des conflits** : En cas de désaccord ou de malaise, privilégiez le dialogue. Si nécessaire, "
            "sollicitez l’aide du Staff, qui est là pour vous écouter et résoudre les problèmes dans l’équité.\n\n"

            "__**2. Percepteurs 🏰**__\n"
            "**Droit de pose** : À partir de **500 000 XP** de contribution à la guilde, vous obtenez le droit de "
            "poser un percepteur.\n"
            "**Durée de pose assouplie** :\n"
            "- Tant que moins de la moitié des percepteurs disponibles sont utilisés, il n’y a pas de limite stricte "
            "de temps.\n"
            "- Au-delà, essayez de ne pas dépasser **8 à 12 heures** de pose pour un même percepteur.\n"
            "**Courtoisie et communication** :\n"
            "- Si un percepteur reste longtemps sur une zone très recherchée, vérifiez que d’autres membres n’en ont "
            "pas besoin.\n"
            "- Si plusieurs joueurs veulent poser un percepteur sur la même zone, organisez-vous pour partager "
            "l’accès équitablement.\n"
            "**Esprit d’équipe** :\n"
            "- En cas d’attaque, tous les membres disponibles sont encouragés à **défendre** le percepteur.\n"
            "- Réciproquement, si vous posez un percepteur, soyez prêt à défendre ceux des autres.\n\n"

            "__**3. Recrutement des Nouveaux Membres 🔑**__\n"
            "**Invitations réservées** : Seuls les membres du Staff et les vétérans peuvent inviter directement en jeu.\n"
            "**Proposition de candidats** :\n"
            "- Si vous connaissez quelqu’un d’intéressé ou si vous jugez qu’un joueur correspond à nos valeurs, "
            "parlez-en au Staff.\n"
            "- Les nouveaux arrivants devront passer par Discord ou contacter un membre du Staff pour en savoir plus.\n"
            "**Processus cohérent** :\n"
            "- Cet encadrement prévient les recrutements impulsifs qui pourraient dégrader l’ambiance.\n"
            "- Faites confiance au Staff pour maintenir une guilde de qualité sur le long terme.\n\n"

            "__**4. Organisation Interne et Rôles du Staff 🛡️**__\n"
            "**Fusion des rôles** : Les anciens Trésoriers, Bras Droit et Bras Gauche forment désormais une seule "
            "catégorie : **le Staff**.\n"
            "**Rôle du Staff** :\n"
            "- Gérer le recrutement, répondre aux questions, organiser les événements.\n"
            "- Veiller au respect du règlement et à la bonne entente générale.\n"
            "- Prendre des initiatives pour dynamiser la guilde, en accord avec le Meneur.\n"
            "**Meneur (Chef de Guilde)** :\n"
            "- Il demeure le garant ultime des décisions.\n"
            "- Il s’appuie sur l’ensemble du Staff pour mener la guilde.\n"
            "**Distinction sur Discord** :\n"
            "- Les membres du Staff sont identifiables par un rôle ou une couleur spécifique.\n"
            "- N’hésitez pas à les contacter pour toute demande, remarque ou suggestion.\n\n"

            "__**5. Sanctions et Discipline ⚠️**__\n"
            "**Avertissements** :\n"
            "- Les écarts mineurs (incompréhension d’une règle, propos maladroits, etc.) feront d’abord l’objet d’un "
            "rappel à l’ordre ou d’un avertissement.\n"
            "- L’erreur étant humaine, la priorité reste la compréhension et la correction du comportement.\n"
            "**Décisions collégiales** :\n"
            "- Il n’y a **pas d’échelle de sanctions prédéfinie** : chaque cas est évalué **au cas par cas** par le "
            "Staff.\n"
            "- Les sanctions importantes (exclusion, rétrogradation majeure, bannissement Discord) sont discutées "
            "collectivement.\n"
            "- **Aucune punition arbitraire ou isolée** ne sera appliquée par un seul membre du Staff, sauf nécessité "
            "absolue (ex. urgence). Dans ce cas, la décision devra être validée par l’ensemble du Staff par la suite.\n"
            "**Transparence** :\n"
            "- La personne concernée est toujours informée des raisons de la sanction.\n"
            "- Si besoin, le Staff peut expliquer brièvement la situation au reste de la guilde, sans détails privés.\n\n"

            "__**6. Participation, Entraide et Vie de Guilde 🌍**__\n"
            "**Discord Obligatoire** :\n"
            "- **L’utilisation de Discord est indispensable** pour rester informé, suivre les annonces et participer "
            "à la vie de la guilde.\n"
            "- C’est l’outil central de coordination (annonces, événements, discussions, etc.).\n"
            "**Participation active** :\n"
            "- Connectez-vous régulièrement, échangez sur les canaux, proposez ou rejoignez des sorties.\n"
            "- Un simple “bonjour” contribue déjà à l’ambiance conviviale.\n"
            "**Entraide** :\n"
            "- Aidez les membres en difficulté, offrez vos conseils ou accompagnez-les.\n"
            "- Si vous avez besoin d’aide, n’hésitez pas à le signaler.\n"
            "**Événements et animations** :\n"
            "- Le Staff organisera régulièrement des activités (donjons, drop, etc.).\n"
            "- Proposez vos propres idées : toutes les initiatives sont les bienvenues !\n"
            "**Outil Discord “EvolutionBOT”** :\n"
            "- Inscriptions aux événements, notifications, classement d’XP, etc.\n"
            "- Développé par **Coca-Cola**, ouvert aux suggestions d’amélioration.\n\n"

            "__**7. Contribution d’XP à la Guilde 📊**__\n"
            "**Liberté du taux d’XP** :\n"
            "- Dès votre arrivée, vous pouvez choisir de **1 % à 99 %** d’XP guilde.\n"
            "- L’ancienne règle du palier 1 000 000 d’XP est supprimée.\n"
            "**1 % d’XP minimum** :\n"
            "- Cette légère contribution garantit une évolution collective sans trop impacter votre progression.\n"
            "- Elle profite à tous (déblocage de percepteurs, meilleure réputation, etc.).\n"
            "**0 % : dérogation exceptionnelle** :\n"
            "- Par défaut, 0 % n’est pas autorisé.\n"
            "- En cas de circonstances particulières (rush 200, IRL, etc.), faites une demande via `!ticket`.\n"
            "- Le Staff évaluera la situation.\n\n"

            "__**8. Multi-Guilde 🔄**__\n"
            "**Pour les membres** :\n"
            "- Avoir un personnage dans une autre guilde est **toléré**, mais **mal vu** si cela nuit à votre "
            "engagement envers Evolution.\n"
            "- En cas de conflit d’intérêts, le Staff pourra en discuter avec vous pour trouver une solution.\n"
            "**Pour les membres du Staff** :\n"
            "- Nous exigeons une **fidélité à Evolution**.\n"
            "- Les membres du Staff ne doivent pas être actifs dans des guildes concurrentes.\n\n"

            "__**9. Conclusion 🎉**__\n"
            "Cette mise à jour du règlement a été conçue pour favoriser une bonne ambiance et l’implication de tous "
            "les membres. Nous souhaitons que chaque joueur d’**Evolution** se sente chez lui, progressant à la fois "
            "individuellement et collectivement.\n\n"
            "En adhérant à ces règles, vous contribuez à faire d’Evolution une guilde exemplaire où règnent le "
            "respect, la convivialité et la coopération. **Le Staff** est à votre écoute pour toute question ou "
            "suggestion. N’hésitez pas à communiquer ouvertement : c’est ensemble que nous continuerons d’améliorer "
            "la guilde.\n\n"
            "**Merci à tous pour votre lecture et votre engagement.**\n"
            "Bon jeu à tous au sein d’Evolution, et amusez-vous bien !\n\n"
            "*Règlement en vigueur à compter du 19/02/2025.*\n"
        )

        # 1) Vérifier si tout tient dans un seul embed (limite 4096 pour la description)
        if len(summary_text) <= 4096:
            embed = discord.Embed(
                title="Résumé Simplifié du Règlement d'Evolution",
                description=summary_text,
                color=discord.Color.gold()
            )
            embed.set_footer(text="Pour plus de détails, consultez le règlement complet ou demandez au Staff.")
            await ctx.send(embed=embed)

        else:
            # 2) Si le texte est trop long, on le découpe en plusieurs parties
            chunks = list(chunk_text(summary_text, 3000))
            for index, chunk in enumerate(chunks, start=1):
                # Nettoie les espaces inutiles
                chunk_clean = chunk.strip()
                if not chunk_clean:
                    continue  # ignore les éventuels blocs vides

                if index == 1:
                    # Premier embed
                    embed = discord.Embed(
                        title="Résumé Simplifié du Règlement d'Evolution",
                        description=chunk_clean,
                        color=discord.Color.gold()
                    )
                    embed.set_footer(text="Pour plus de détails, consultez le règlement complet ou demandez au Staff.")
                else:
                    # Embeds suivants
                    embed = discord.Embed(
                        title=f"Règlement (suite) [Part {index}]",
                        description=chunk_clean,
                        color=discord.Color.gold()
                    )

                await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    """Ajoute la classe HelpCog au bot (une seule fois)."""
    if bot.get_cog("HelpCog") is None:
        await bot.add_cog(HelpCog(bot))
    else:
        print("[HelpCog] Déjà chargé, on ignore setup().")
