"""Routage local des demandes de rédaction, sans appel IA ni accès aux données."""
from __future__ import annotations

import re
import unicodedata


def _words(value: str) -> str:
    value = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                    if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


_POLITE = (r"^(?:(?:bonjour|salut|evo|stp|svp|s il te plait) )*"
           r"(?:(?:peux tu|tu peux|pourrais tu|tu pourrais|je veux que tu) )?"
           r"(?:(?:me|m) )?")
_TRANSFORM = re.compile(_POLITE + r"(?:reformule|reformuler|reecris|reecrire|"
                        r"corrige|corriger|traduis|traduire|raccourcis|raccourcir|"
                        r"resume|resumer|synthetise|synthetiser)\b")
_TEXT = r"(?:message|texte|annonce|poeme|lettre|paragraphe|phrase|brouillon|email|mail|sms|blague|slogan|code|script)"
_DRAFT = re.compile(_POLITE + r"(?:redige|rediger|ecris|ecrire|cree|creer|compose|composer) "
                    r"(?:(?:moi|nous) )?(?:(?:un|une|le|la|mon|ma|notre|ce|cette) )?" + _TEXT + r"\b")
_EDIT = re.compile(_POLITE + r"(?:ajoute|ajouter|supprime|supprimer|retire|retirer|"
                   r"enleve|enlever|ameliore|ameliorer|rends|rendre)\b")
_LOOKUP = re.compile(
    r"\b(?:cherche|chercher|recherche|rechercher|verifie|verifier|consulte|consulter|"
    r"recupere|recuperer|internet|web|actualites?|bilan|calendrier|annuaire|"
    r"historique|logs?|salon|canal|actuel|actuellement|prix|tarifs?|horaires?|statistiques|"
    r"liste|nombre|bonus|jets?|taux|drops?|recettes?)\b|"
    r"\bartisans? disponibles?\b|"
    r"\b(?:derniers?|recents?|precedents?) messages?\b|"
    r"\b(?:regles?|reglement|histoire|membres?|metiers?) (?:de |du |de la |de notre )?(?:guilde|serveur)\b|"
    r"\b(?:nos |les )?(?:prochaines?|dernieres?) (?:sorties?|activites?|mises? a jour)\b|"
    r"\b(?:a partir|d apres|en utilisant) (?:des |les |nos )?(?:donnees|messages|activites|sorties)\b"
)
_ALSO_ACT = re.compile(
    r"\b(?:puis|ensuite|et) (?:publie|publier|envoie|envoyer|inscris|inscrire|"
    r"desinscris|desinscrire|(?:cree|creer|supprime|supprimer) (?:une? )?activite|"
    r"(?:ajoute|retire|supprime) mon metier)\b"
)


def is_writing_request(question: str) -> bool:
    """Je sépare un texte à transformer d'une lecture ou action réellement demandée.

    Le contenu après « : » ou un guillemet est du texte à traiter. Ses mots
    « guilde », « inscris » ou « feu » n'autorisent ni outil ni changement de profil.
    En cas de récupération de faits ou de demande mixte, la boucle habituelle
    conserve ses outils et ses contrôles d'autorisation.
    """
    if not isinstance(question, str) or question.lstrip().startswith(('"', "'", "«", ">", "`")):
        return False
    # Un vocatif n'est pas le début d'un texte fourni.
    request = re.sub(r"^\s*(?:salut\s+|bonjour\s+)?evo\s*[:,]\s*", "", question, flags=re.I)
    boundary = re.search(r'(?<!\d):(?!\d|//)|[\n«“"]', request)
    instruction = request[:boundary.start()] if boundary else request
    if boundary and boundary.group() in {'"', "«", "“"}:
        closing = {'"': '"', "«": "»", "“": "”"}[boundary.group()]
        end = request.find(closing, boundary.end())
        if end >= 0:
            # Une action demandée APRÈS la citation reste une instruction. Les
            # deux-points d'une heure ou d'une URL ne commencent pas un brouillon.
            instruction += " " + request[end + 1:]
    key = _words(instruction)
    if _LOOKUP.search(key) or _ALSO_ACT.search(key):
        return False
    transformation = _TRANSFORM.match(key)
    if transformation:
        target = key[transformation.end():].strip()
        return bool(not target or re.search(r"\b" + _TEXT + r"s?\b", target)
                    or re.match(r"^(?:ca|cela|ceci|le tout|la reponse|ta reponse|tes propos|"
                                r"les fautes|mes fautes|en (?:anglais|francais|espagnol|allemand)|"
                                r"plus (?:court|long|simple))\b", target)
                    or len(instruction) < len(request))
    if _DRAFT.match(key):
        return True
    return bool(_EDIT.match(key) and re.search(r"\b" + _TEXT + r"s?\b", key))


def is_writing_followup(question: str) -> bool:
    """Suivis textuels courts, uniquement après un tour de rédaction réussi."""
    key = re.sub(r" (?:stp|svp|s il te plait)$", "", _words(question))
    return bool(re.fullmatch(
        r"(?:(?:le|la) (?:premier|premiere|deuxieme|second|seconde|troisieme)|"
        r"(?:en |une |la )?version (?:courte|longue|simple)|"
        r"(?:fais |en |une version )?(?:plus|moins) (?:court|courte|long|longue|"
        r"formel|formelle|familier|familiere|chaleureux|chaleureuse|detaille|detaillee)|"
        r"(?:avec|sans) (?:des |les )?emojis?|"
        r"(?:ajoute|retire|supprime|enleve) (?:des |les )?emojis?|"
        r"(?:change|modifie|refais) (?:le |la |l )?(?:fin|debut|titre|introduction|conclusion))", key,
    ))


def is_general_question(question: str) -> bool:
    """Quelques sujets généraux explicites évitent le catalogue Dofus complet.

    Un nom inconnu seul reste ambigu : le repli habituel conserve alors les
    recherches de membre/objet. Cette liste réduit l'entrée, jamais les droits.
    """
    key = _words(question)
    return bool(re.search(
        r"\b(?:programmation|python|javascript|typescript|rust|sql|algorithmes?|"
        r"decorateurs?|recursivite|variables?|mathematiques|fractions?)\b|"
        r"\b(?:combien (?:fait|font)|calcule|calculer) \d", key,
    ))
