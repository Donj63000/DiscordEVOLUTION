"""Je décris les champs du menu Discord et leur lien avec les commandes existantes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

import discord
from discord import app_commands


@dataclass(frozen=True)
class Option:
    name: str
    description: str
    annotation: object = str
    default: object = ...
    autocomplete: str | None = None
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Route:
    path: tuple[str, ...]
    target: str
    description: str
    options: tuple[Option, ...] = ()
    fixed: tuple[str, ...] = ()
    mode: str = "tokens"


DESCRIPTIONS = {
    "aide": "Découvrir les commandes et les guides d’Evolution BOT.",
    "regles": "Lire le règlement de la guilde Evolution.",
    "ping": "Vérifier que le bot répond.",
    "ticket": "Contacter le Staff : ouvrir un échange privé guidé.",
    "staff": "Afficher les membres du Staff.",
    "avis": "Donner ton avis sur la guilde en message privé.",
    "calendrier": "Consulter le calendrier interactif des activités.",
    "event": "Staff : préparer un événement Discord avec le guide en message privé.",
    "veteran": "Staff : consulter les candidats Vétéran et les promouvoir par bouton.",
    "recrutement": "Staff : enregistrer un nouveau joueur dans la guilde.",
    "scan": "Vérifier la sécurité d’un lien avec Defender.",
    "defenderstatus": "Administration : consulter l’état des services de sécurité.",
    "warnings": "Staff : consulter les avertissements d’un membre.",
    "resetwarnings": "Staff : effacer les avertissements d’un membre.",
    "musique": "Lancer une musique ou une playlist dans ton salon vocal.",
    "ia": "Ouvrir une conversation privée avec l’assistant IA.",
    "iahelp": "Consulter le guide de l’assistant IA.",
    "iaend": "Terminer ta conversation privée avec l’assistant IA.",
    "bot": "Poser une question à l’IA avec le contexte du salon.",
    "analyse": "Demander à l’IA un résumé des derniers messages du salon.",
    "pl": "Demander à l’IA de préparer une annonce de recherche de groupe.",
    "iastaff": "Staff : demander de l’aide ou une action à l’assistant du serveur.",
    "organisation": "Préparer une sortie de guilde avec le formulaire guidé.",
    "organisation-model": "Staff : consulter ou changer le modèle IA des sorties.",
    "organisation-sync": "Staff : recharger les événements enregistrés dans la console.",
    "annonce": "Staff : préparer une annonce avec le formulaire IA.",
    "annonce-model": "Staff : consulter ou changer le modèle IA des annonces.",
    "annonce-config": "Staff : consulter la configuration des annonces.",
    "annonce-list": "Staff : consulter les annonces programmées.",
    "annonce-cancel": "Staff : annuler une annonce programmée.",
    "close_sondage": "Clôturer un sondage que tu as créé, ou en tant que Staff.",
    "perco": "Consulter ou mettre à jour le statut des percepteurs.",
    "membre": "Consulter une fiche de membre.",
    "membre principal": "Enregistrer ou modifier ton personnage principal.",
    "membre addmule": "Ajouter une mule à ta fiche de membre.",
    "membre delmule": "Retirer une mule de ta fiche de membre.",
    "membre del": "Staff : supprimer la fiche d’un joueur et ses mules.",
    "membre moi": "Consulter ton personnage principal et tes mules.",
    "membre liste": "Consulter tous les joueurs et leurs personnages.",
    "profil": "Consulter les caractéristiques et le score d’un joueur.",
    "profil set": "Créer ou mettre à jour ton profil avec un guide en message privé.",
    "profil import": "Importer tes statistiques depuis un message du salon.",
    "profil delete": "Supprimer ton profil, ou celui d’un autre joueur en tant que Staff.",
    "profil search": "Rechercher un profil par nom de personnage.",
    "profil score": "Comprendre le calcul du score d’un profil.",
    "ladder": "Consulter le classement des profils de la guilde.",
    "stats": "Consulter le tableau de bord des statistiques du serveur.",
    "stats all": "Consulter toutes les statistiques du serveur.",
    "stats messages": "Consulter les statistiques des messages par salon.",
    "stats users": "Consulter les statistiques d’activité des membres.",
    "stats edits": "Consulter les statistiques des messages modifiés.",
    "stats deletions": "Consulter les statistiques des messages supprimés.",
    "stats reactions": "Consulter les statistiques des réactions.",
    "stats voice": "Consulter les statistiques des salons vocaux.",
    "stats presence": "Consulter les statistiques de présence.",
    "stats ladder": "Consulter le classement des profils de la guilde.",
    "stats reset": "Staff : remettre à zéro les statistiques du serveur.",
    "stats on": "Staff : activer la collecte des statistiques.",
    "stats off": "Staff : désactiver la collecte des statistiques.",
    "accueil": "Consulter les commandes de suivi de l’accueil des membres.",
    "accueil statut": "Staff : consulter l’avancement de l’accueil d’un membre.",
    "accueil relance": "Staff : relancer le parcours d’accueil d’un membre.",
    "accueil reset": "Staff : réinitialiser le suivi d’accueil d’un membre.",
}

GROUP_DESCRIPTIONS = {
    "job": "Consulter, ajouter ou supprimer tes métiers.",
    "activite": "Organiser les activités et gérer tes inscriptions.",
    "membre": "Gérer ton personnage principal et tes mules.",
    "profil": "Créer et consulter les profils de personnages.",
    "stats": "Consulter les statistiques du serveur.",
    "accueil": "Staff : suivre l’accueil des membres.",
    "rune": "Calculer les probabilités d’obtention de runes.",
    "clear": "Staff : nettoyer les messages temporaires de la console.",
}

COMMAND_NAMES = {
    "membre addmule": "ajouter-mule",
    "membre delmule": "retirer-mule",
    "membre del": "supprimer",
    "profil set": "modifier",
    "profil import": "importer",
    "profil delete": "supprimer",
    "profil search": "rechercher",
    "stats all": "tout",
    "stats users": "membres",
    "stats edits": "modifications",
    "stats deletions": "suppressions",
    "stats voice": "vocal",
    "stats ladder": "classement",
    "stats reset": "reinitialiser",
    "stats on": "activer",
    "stats off": "desactiver",
}

PARAMETERS = {
    "member": ("membre", "Sélectionne le membre concerné."),
    "pseudo": ("personnage", "Nom du joueur ou mention du membre."),
    "nom_perso": ("personnage", "Nom de ton personnage principal."),
    "nom_mule": ("personnage", "Nom de la mule concernée."),
    "maybe_name": ("joueur", "Nom du personnage ou mention ; vide pour ton profil."),
    "player_name": ("personnage", "Nom du personnage ; vide pour ton propre profil."),
    "q": ("recherche", "Tout ou partie du nom du personnage."),
    "arg": ("options", "Exemples : 15, me, all, class iop, page 2, ou une mention."),
    "model": ("modele", "Nom du modèle IA ; vide pour consulter le modèle actuel."),
    "message": ("message", "Ta demande à l’assistant Staff."),
    "user_message": ("message", "Ta question ou les informations à transmettre à l’IA."),
    "query": ("recherche", "Titre, lien YouTube ou lien de playlist."),
    "url": ("lien", "Adresse du site à vérifier, par exemple https://exemple.fr."),
    "message_id": ("message", "Identifiant du message du sondage à clôturer."),
    "announce_id": ("identifiant", "Identifiant de l’annonce programmée à annuler."),
    "etat": ("etat", "Statut des percepteurs ; vide pour consulter."),
    "quick": ("texte", "Texte ou consignes pour préparer l’annonce."),
}

JOB_NAME = Option("metier", "Choisis un métier ou saisis son nom.", autocomplete="jobs")
LEVEL = Option("niveau", "Niveau du métier, de 1 à 100.", app_commands.Range[int, 1, 100])
ACTIVITY_ID = Option("identifiant", "Identifiant de l’activité.", autocomplete="activities")
DATE = Option("date", "Date et heure au format JJ/MM/AAAA HH:MM.")
DESCRIPTION = Option("description", "Informations utiles pour les participants.", default="")


def custom_routes() -> tuple[Route, ...]:
    confirmation = os.getenv("CLEAR_CONSOLE_CONFIRMATION", "CONFIRMER")
    return (
        Route(("objet",), "objet", "Consulter les caractéristiques d'un objet Dofus Rétro.", (
            Option("nom", "Nom de l'objet : choisis une suggestion.", autocomplete="wiki_items"),
        ), mode="rest"),
        Route(("recette",), "recette", "Calculer les ressources nécessaires pour fabriquer un objet.", (
            Option("objet", "Objet à fabriquer : choisis une suggestion.", autocomplete="wiki_items"),
            Option("quantite", "Nombre d'exemplaires, de 1 à 10 000.", app_commands.Range[int, 1, 10000], 1),
        ), mode="wiki_recipe"),
        Route(("equipement",), "equipement", "Chercher des équipements par type, tranche de niveaux et nom.", (
            Option("type", "Type d'équipement : Chapeau, Anneau, Botte…", autocomplete="wiki_types"),
            Option("niveau", "Niveau maximum des équipements, de 1 à 200.", app_commands.Range[int, 1, 200], 200),
            Option("niveau_min", "Niveau minimum des équipements, de 1 à 200.", app_commands.Range[int, 1, 200], 1),
            Option("nom", "Tout ou partie du nom de l'équipement, facultatif.", default=""),
        ), mode="wiki_equipment"),
        Route(("monstre",), "monstre", "Consulter les statistiques et résistances d'un monstre Rétro.", (
            Option("nom", "Nom du monstre : choisis une suggestion et son niveau.", autocomplete="wiki_monsters"),
        ), mode="rest"),
        Route(("job", "aide"), "job", "Consulter le guide des métiers."),
        Route(("job", "ajouter"), "job", "Ajouter ou mettre à jour un métier.", (JOB_NAME, LEVEL), ("add",)),
        Route(("job", "supprimer"), "job", "Retirer un métier de ta fiche.", (JOB_NAME,), ("del",)),
        Route(("job", "mes-metiers"), "job", "Consulter tes métiers et leurs niveaux.", fixed=("me",)),
        Route(("job", "liste"), "job", "Consulter tous les métiers et les artisans.", fixed=("liste",)),
        Route(("job", "metiers"), "job", "Consulter les noms de métiers disponibles.", fixed=("liste", "metier")),
        Route(("job", "joueur"), "job", "Consulter les métiers d’un membre.", (Option("membre", "Membre à consulter.", discord.Member),)),
        Route(("job", "rechercher"), "job", "Trouver les artisans qui exercent un métier.", (JOB_NAME,)),
        Route(("job", "nettoyer"), "job", "Staff : retirer les joueurs absents du serveur.", fixed=("prune",)),
        Route(("activite", "aide"), "activite", "Consulter le guide des activités.", fixed=("guide",)),
        Route(("activite", "liste"), "activite", "Consulter les prochaines activités.", fixed=("liste",)),
        Route(("activite", "creer"), "activite", "Créer une activité et ouvrir les inscriptions.", (Option("titre", "Nom de l’activité."), DATE, DESCRIPTION), ("creer",), "activity"),
        Route(("activite", "modifier"), "activite", "Modifier la date et la description d’une activité.", (ACTIVITY_ID, DATE, DESCRIPTION), ("modifier",), "activity"),
        Route(("activite", "info"), "activite", "Consulter les détails d’une activité.", (ACTIVITY_ID,), ("info",), "rest"),
        Route(("activite", "rejoindre"), "activite", "T’inscrire à une activité.", (ACTIVITY_ID,), ("join",), "rest"),
        Route(("activite", "quitter"), "activite", "Te désinscrire d’une activité.", (ACTIVITY_ID,), ("leave",), "rest"),
        Route(("activite", "annuler"), "activite", "Annuler une activité que tu organises.", (ACTIVITY_ID,), ("annuler",), "rest"),
        Route(("rune", "aide"), "rune", "Consulter le guide de calcul des runes."),
        Route(("rune", "calculer"), "rune", "Calculer les probabilités de runes pour un jet.", (Option("jet", "Valeur du jet à briser.", app_commands.Range[int, 1, 10000]), Option("statistique", "Exemple : force, intelligence, vitalité ou sagesse.")), ("jet",), "rest"),
        Route(("sondage",), "sondage", "Publier un sondage avec plusieurs choix.", (
            Option("titre", "Question du sondage."),
            Option("choix", "Entre 2 et 26 réponses séparées par |."),
            Option("duree", "Durée JJ:HH:MM ; vide pour clôturer manuellement.", default=""),
        ), mode="poll"),
        Route(("clear", "aide"), "clear", "Staff : consulter les précautions du nettoyage de console."),
        Route(("clear", "console"), "clear", "Staff : nettoyer la console en préservant les données du bot.", (Option("confirmation", "Confirmer le nettoyage des messages temporaires.", choices=(confirmation,)),), ("console",)),
        Route(("profil", "importer"), "profil import", DESCRIPTIONS["profil import"], (Option("message", "Lien ou identifiant du message contenant %stats%, dans ce salon."),), mode="message_reference"),
    )


def quote_token(value: object) -> str:
    text = str(getattr(value, "mention", value))
    return '"' + text.replace('"', '\\"') + '"'


def format_arguments(route: Route, values: dict[str, object]) -> str:
    if route.mode == "wiki_recipe":
        return f"{values.get('quantite', 1)} {values['objet']}"
    if route.mode == "wiki_equipment":
        return (
            f"{quote_token(values['type'])} {values.get('niveau', 200)} "
            f"{values.get('niveau_min', 1)} {values.get('nom', '')}"
        ).rstrip()
    if route.mode == "message_reference":
        return ""
    parts = [str(getattr(values[o.name], "mention", values[o.name])) for o in route.options if values.get(o.name) is not None]
    if route.mode == "activity":
        try:
            datetime.strptime(str(values["date"]), "%d/%m/%Y %H:%M")
        except ValueError as exc:
            raise ValueError("La date doit être au format JJ/MM/AAAA HH:MM, par exemple 25/09/2026 21:00.") from exc
    if route.mode == "poll":
        title = str(values["titre"]).strip()
        choices = [choice.strip() for choice in str(values["choix"]).split("|")]
        if not title or ";" in title or not 2 <= len(choices) <= 26:
            raise ValueError("Indique un titre sans point-virgule et entre 2 et 26 choix séparés par |.")
        if any(not choice or ";" in choice or choice.lower().startswith("temps=") for choice in choices):
            raise ValueError("Chaque choix doit être non vide, sans point-virgule ni préfixe temps=.")
        duration = str(values.get("duree") or "").strip()
        if duration:
            components = duration.split(":")
            if len(components) != 3 or not all(c.isdigit() for c in components):
                raise ValueError("La durée doit être au format JJ:HH:MM, par exemple 00:02:00.")
            days, hours, minutes = map(int, components)
            if hours > 23 or minutes > 59 or not (days or hours or minutes):
                raise ValueError("Indique une durée positive, avec 0 à 23 heures et 0 à 59 minutes.")
        return " ; ".join([title, *choices, *([f"temps={duration}"] if duration else [])])
    if route.mode in {"rest", "activity"}:
        return " ".join([*route.fixed, *parts]).strip()
    return " ".join(quote_token(part) for part in [*route.fixed, *parts])
