"""Je décris les champs du menu Discord et leur lien avec les commandes existantes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo
import os

import discord
from discord import app_commands

from utils.slash_errors import SlashInputError


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
    "calendrier": "Ouvrir le calendrier du mois en cours, sans date à saisir.",
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
    "organisation": "Staff : préparer une sortie de guilde avec le formulaire guidé.",
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
DATE = Option("date", "Date et heure de Paris au format JJ/MM/AAAA HH:MM.")
DESCRIPTION = Option("description", "Informations utiles pour les participants.", default="")
MODIFIED_DESCRIPTION = Option(
    "description", "Nouvelle description ; laisser vide pour conserver celle de l'activité.", default=None,
)


def custom_routes() -> tuple[Route, ...]:
    confirmation = os.getenv("CLEAR_CONSOLE_CONFIRMATION", "CONFIRMER").strip()
    if not 1 <= len(confirmation) <= 100 or confirmation.endswith("\\"):
        raise ValueError("CLEAR_CONSOLE_CONFIRMATION doit compter de 1 à 100 caractères, sans antislash final.")
    return (
        Route(
            ("calendrier",), "calendrier",
            "Afficher directement le calendrier des activités, sans rien remplir.",
            mode="calendar",
        ),
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
        Route(("activite", "modifier"), "activite", "Modifier la date et la description d’une activité.", (ACTIVITY_ID, DATE, MODIFIED_DESCRIPTION), ("modifier",), "activity"),
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
        Route(("close_sondage",), "close_sondage", DESCRIPTIONS["close_sondage"], (
            Option("message", "Identifiant du message : choisis un sondage actif.", autocomplete="polls"),
        ), mode="rest"),
        Route(("clear", "aide"), "clear", "Staff : consulter les précautions du nettoyage de console."),
        Route(("clear", "console"), "clear", "Staff : nettoyer la console en préservant les données du bot.", (Option("confirmation", "Confirmer le nettoyage des messages temporaires.", choices=(confirmation,)),), ("console",)),
        Route(("profil", "importer"), "profil import", DESCRIPTIONS["profil import"], (Option("message", "Lien ou identifiant du message contenant %stats%, dans ce salon."),), mode="message_reference"),
    )


def text_limit(route: Route, option: Option) -> int:
    """Borne les champs en amont, avant toute création de rôle ou écriture."""
    if option.name == "titre":
        return 85 if route.target == "activite" else 180
    if option.name == "description":
        return 900
    if option.name == "date":
        return 16
    if option.name == "duree":
        return 10
    if option.name == "message" and route.target == "close_sondage":
        return 20
    if option.name in {"metier", "personnage", "joueur", "identifiant", "statistique", "modele"}:
        return 100
    if option.name == "choix":
        return 2700
    return 2000


def validate_values(route: Route, supplied: dict[str, object]) -> dict[str, object]:
    """Ne confond pas une option omise avec une valeur vide explicitement fournie."""
    values = {}
    for option in route.options:
        value = supplied.get(option.name, option.default)
        if value is ...:
            raise SlashInputError(f"Le champ « {option.name} » est obligatoire.")
        if isinstance(value, str):
            value = value.strip()
            if option.default is ... and not value:
                raise SlashInputError(f"Le champ « {option.name} » ne peut pas être vide.")
            if any(ord(char) < 32 and char not in "\n\t" for char in value):
                raise SlashInputError(f"Le champ « {option.name} » contient un caractère de contrôle.")
            try:
                length = len(value.encode("utf-16-le")) // 2
            except UnicodeEncodeError as exc:
                raise SlashInputError("Le texte contient un caractère Unicode invalide.") from exc
            if length > text_limit(route, option):
                raise SlashInputError(
                    f"Le champ « {option.name} » est trop long (maximum {text_limit(route, option)} caractères)."
                )
            if option.choices and value not in option.choices:
                raise SlashInputError(f"Choisis une valeur proposée pour « {option.name} ».")
        values[option.name] = value
    return values


def quote_token(value: object) -> str:
    text = str(getattr(value, "mention", value))
    # Le parseur historique ne sait pas représenter un antislash juste avant
    # le guillemet terminal. Refuser est préférable à modifier silencieusement le nom.
    if text.endswith("\\"):
        raise SlashInputError("Cette valeur ne peut pas se terminer par un antislash.")
    return '"' + text.replace('"', '\\"') + '"'


def activity_datetime(value: object) -> datetime:
    try:
        parsed = datetime.strptime(str(value), "%d/%m/%Y %H:%M")
    except ValueError as exc:
        raise SlashInputError("La date doit être au format JJ/MM/AAAA HH:MM, heure de Paris.") from exc
    paris = ZoneInfo("Europe/Paris")
    local = parsed.replace(tzinfo=paris)
    # Au passage à l'heure d'été, certaines heures locales n'existent pas.
    if local.astimezone(timezone.utc).astimezone(paris).replace(tzinfo=None) != parsed:
        raise SlashInputError("Cette heure n'existe pas à Paris lors du changement d'heure.")
    if local <= datetime.now(paris):
        raise SlashInputError("Choisis une date et une heure à venir, à l'heure de Paris.")
    return parsed


def format_arguments(route: Route, values: dict[str, object]) -> str:
    if route.mode == "calendar":
        return ""
    if route.mode == "wiki_recipe":
        return f"{values.get('quantite', 1)} {values['objet']}"
    if route.mode == "wiki_equipment":
        if int(values.get("niveau_min", 1)) > int(values.get("niveau", 200)):
            raise SlashInputError("Le niveau minimum ne peut pas dépasser le niveau maximum.")
        return (
            f"{quote_token(values['type'])} {values.get('niveau', 200)} "
            f"{values.get('niveau_min', 1)} {values.get('nom', '')}"
        ).rstrip()
    if route.mode == "message_reference":
        return ""
    parts = [str(getattr(values[o.name], "mention", values[o.name]))
             for o in route.options if values.get(o.name) is not None]
    if route.target == "activite" and "identifiant" in values:
        identifier = str(values["identifiant"])
        if not re.fullmatch(r"[0-9]{1,20}", identifier) or int(identifier) == 0:
            raise SlashInputError("Choisis une activité proposée ou indique son identifiant numérique.")
    if route.mode == "activity":
        activity_datetime(values["date"])
        if "\n" in str(values.get("titre", "")):
            raise SlashInputError("Le titre de l'activité doit tenir sur une seule ligne.")
    if route.mode == "poll":
        title = str(values["titre"]).strip()
        choices = [choice.strip() for choice in str(values["choix"]).split("|")]
        if not title or ";" in title or title.casefold().startswith("temps="):
            raise SlashInputError("Le titre ne doit contenir ni point-virgule ni préfixe temps=.")
        if not 2 <= len(choices) <= 26:
            raise SlashInputError("Indique entre 2 et 26 choix séparés par |.")
        if any(
            not choice or ";" in choice or choice.casefold().startswith("temps=")
            or len(choice.encode("utf-16-le")) // 2 > 100 for choice in choices
        ):
            raise SlashInputError("Chaque choix doit compter de 1 à 100 caractères, sans ; ni préfixe temps=.")
        if len({choice.casefold() for choice in choices}) != len(choices):
            raise SlashInputError("Les choix du sondage doivent être différents.")
        duration = str(values.get("duree") or "").strip()
        if duration:
            if not re.fullmatch(r"[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2}", duration):
                raise SlashInputError("La durée doit être au format JJ:HH:MM, par exemple 00:02:00.")
            days, hours, minutes = map(int, duration.split(":"))
            total = days * 1440 + hours * 60 + minutes
            if hours > 23 or minutes > 59 or not 0 < total <= 30 * 1440:
                raise SlashInputError("Indique une durée positive de 30 jours maximum (heures 0–23, minutes 0–59).")
        return " ; ".join([title, *choices, *([f"temps={duration}"] if duration else [])])
    if route.target == "close_sondage":
        text = str(values["message"])
        if not re.fullmatch(r"[0-9]{1,20}", text) or not 0 < int(text) < 2**64:
            raise SlashInputError("Choisis un sondage ou indique son identifiant Discord exact.")
    if route.mode in {"rest", "activity"}:
        return " ".join([*route.fixed, *parts]).strip()
    return " ".join(quote_token(part) for part in [*route.fixed, *parts])
