"""Je résous les images publiques avec les dossiers vérifiés des catalogues Xixou."""

from __future__ import annotations

import re


XIXOU_UPLOADS = "https://xixou.io/wp-content/uploads/"
MOON_ICONS = "https://wiki.moon-bot.io/icons/"

EQUIPMENT_IMAGE_FOLDERS = {
    "amulettes": "xixou-amulettes",
    "anneaux": "xixou-anneaux",
    "arbaletes": "xixou-arbaletes",
    "arcs": "xixou-arcs",
    "armes-magiques": "xixou-armes-magiques",
    "baguettes": "xixou-baguettes",
    "batons": "xixou-batons",
    "bottes": "xixou-bottes",
    "boucliers": "xixou-boucliers",
    "capes": "xixou-capes",
    "ceintures": "xixou-ceintures",
    "chapeaux": "xixou-chapeaux",
    "dagues": "xixou-dagues",
    "dofus": "xixou-dofus",
    "epees": "xixou-epees",
    "familiers": "xixou-familiers",
    "faux": "xixou-faux",
    "filet-de-capture": "xixou-filet-de-capture",
    "haches": "xixou-haches",
    "marteaux": "xixou-marteaux",
    "outils": "xixou-outils",
    "pelles": "xixou-pelles",
    "pierre-d-ame": "xixou-pierre-d-ame",
    "pioches": "xixou-pioches",
    "sacs": "xixou-sacados",
}

RESOURCE_IMAGE_FOLDERS = {
    "aile": "xixou-ressources/images_aile",
    "alliage": "xixou-ressources/images_alliage",
    "benediction": "xixou-ressources/images_benediction",
    "biere": "xixou-ressources/images_biere",
    "bois": "xixou-ressources/images_bois",
    "boisson": "xixou-ressources/images_boisson",
    "bourgeon": "xixou-ressources/images_bourgeon",
    "cadeaux": "xixou-ressources/images_cadeaux",
    "carapace": "xixou-ressources/images_carapace",
    "carte-ttg": "xixou-ressources/tcg-images/cartes",
    "cereale": "xixou-ressources/images_cereale",
    "certificat-chenil": "xixou-ressources/images_certificat_chenils",
    "certificat-monture": "xixou-ressources/images_certificat_montures",
    "clefs": "xixou-ressources/images_clefs",
    "coquille": "xixou-ressources/images_coquille",
    "cuir": "xixou-ressources/images_cuir",
    "document": "xixou-ressources/images_document",
    "ecailles-dragon-133": "xixou-ressources/images_ecailles",
    "ecorce": "xixou-ressources/images_ecorce",
    "etoffe": "xixou-ressources/images_etoffe",
    "fantome-de-familier": "xixou-ressources/images_fantome-de-familier",
    "farine": "xixou-ressources/images_farine",
    "fee-artifice": "xixou-ressources/images_fee-artifice",
    "fleur": "xixou-ressources/images_fleur",
    "fragment-ame-de-shushu": "xixou-ressources/images_fragment-ame-de-shushu",
    "friandise": "xixou-ressources/images_friandise",
    "fruit": "xixou-ressources/images_fruit",
    "gelee": "xixou-ressources/images_gelee",
    "graine": "xixou-ressources/images_graine",
    "huile": "xixou-ressources/images_huile",
    "laine": "xixou-ressources/images_laine",
    "legume": "xixou-ressources/images_legume",
    "maitrise": "xixou-ressources/images_maitrises",
    "malediction": "xixou-ressources/images_malediction",
    "materiel-d-alchimie": "xixou-ressources/images_materiel-d-alchimie",
    "metaria": "xixou-ressources/images_metaria",
    "minerai": "xixou-ressources/images_minerai",
    "nourriture-boost": "xixou-ressources/images_nourriture-boost",
    "objet-de-dons": "xixou-ressources/images_objet-de-dons",
    "objet-de-mission": "xixou-ressources/images_objet-de-mission",
    "objet-de-mutation": "xixou-ressources/images_objet-de-mutation",
    "objet-elevage": "xixou-ressources/images_objet-elevage",
    "objet-utilisable": "xixou-ressources/images_objet-utilisable",
    "objet-vivant": "xixou-ressources/images_objet-vivant",
    "oeil": "xixou-ressources/images_oeil",
    "oeuf-de-familier": "xixou-ressources/images_oeuf-de-familier",
    "oeuf": "xixou-ressources/images_oeuf",
    "oreille": "xixou-ressources/images_oreille",
    "os": "xixou-ressources/images_os",
    "pain": "xixou-ressources/images_pain",
    "paquet-de-cartes": "xixou-ressources/tcg-images/paquets",
    "paquets": "xixou-ressources/tcg-images/paquets",
    "parchemin-caracteristique": "xixou-ressources/images_parchemin_caracteristiques",
    "parchemin-experience": "xixou-ressources/images_parchemin_experiences",
    "parchemin-recherche": "xixou-ressources/images_parchemin-recherche",
    "parchemin-sort": "xixou-ressources/images_parchemin_sorts",
    "parchemin-titre": "xixou-ressources/images_parchemin-titre",
    "patte": "xixou-ressources/images_patte",
    "peau": "xixou-ressources/images_peau",
    "peluche": "xixou-ressources/images_peluche",
    "personnage-suiveur": "xixou-ressources/images_personnage-suiveur",
    "pierre-ame-pleine": "xixou-ressources/images_pierre-ame-pleine",
    "pierre-brute": "xixou-ressources/images_pierre-brute",
    "pierre-magique": "xixou-ressources/images_pierre-magique",
    "pierre-precieuse": "xixou-ressources/images_pierre-precieuse",
    "planche": "xixou-ressources/images_planche",
    "plante": "xixou-ressources/images_plante",
    "plume": "xixou-ressources/images_plume",
    "poil": "xixou-ressources/images_poil",
    "poisson-comestible": "xixou-ressources/images_poisson-comestible",
    "poisson-vide": "xixou-ressources/images_poisson-vide",
    "poisson": "xixou-ressources/images_poisson",
    "potion-de-forgemagie": "xixou-ressources/images_potion-de-forgemagie",
    "potion-familier": "xixou-ressources/images_potion-familier",
    "potion-forgemagie": "xixou-ressources/images_potion-de-forgemagie",
    "potion-oubli-maitrise": "xixou-ressources/images_potion-oubli-maitrise",
    "potion-oubli-metier": "xixou-ressources/images_potion-oubli-metier",
    "potion-oubli-percepteur": "xixou-ressources/images_potion-oubli-percepteur",
    "potion-oubli-sort": "xixou-ressources/images_potion-oubli-sort",
    "potion": "xixou-ressources/images_potion",
    "poudre": "xixou-ressources/images_poudre",
    "prisme": "xixou-ressources/images_prisme",
    "queue": "xixou-ressources/images_queue",
    "racine": "xixou-ressources/images_racine",
    "ressource": "xixou-ressources/images_ressource",
    "roleplay-buff": "xixou-ressources/images_roleplay-buff",
    "rune-de-forgemagie": "xixou-ressources/images_rune-de-forgemagie",
    "sac-de-ressources": "xixou-ressources/images_sac-de-ressources",
    "teinture": "xixou-ressources/images_teinture",
    "tonique": "xixou-ressources/images_tonique",
    "viande-comestible": "xixou-ressources/images_viande-comestible",
    "viande-conservee": "xixou-ressources/images_viande-conservee",
    "viande": "xixou-ressources/images_viande",
}

_FAMILY_FOLDERS = {"equipements": EQUIPMENT_IMAGE_FOLDERS, "ressources": RESOURCE_IMAGE_FOLDERS}
_SOURCE_FOLDERS = frozenset(EQUIPMENT_IMAGE_FOLDERS.values()) | frozenset(
    RESOURCE_IMAGE_FOLDERS.values()
)
_ALLOWED_FOLDERS = _SOURCE_FOLDERS | frozenset(f"xixou-og/{folder}" for folder in _SOURCE_FOLDERS)
_SOURCE_FILENAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\.(svg|png)")
_PNG_FILENAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\.png")
_MOON_FILENAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\.png")


def xixou_image_candidates(family: str, category: str, image: str) -> tuple[str, ...]:
    """Je conserve un PNG existant et je remplace un SVG par sa copie PNG publique."""
    if not all(isinstance(value, str) for value in (family, category, image)):
        return ()
    folders = _FAMILY_FOLDERS.get(family, {})
    folder = folders.get(category)
    match = _SOURCE_FILENAME.fullmatch(image)
    if folder is None or match is None:
        return ()
    png_name = image.rsplit(".", 1)[0] + ".png"
    og_url = f"{XIXOU_UPLOADS}xixou-og/{folder}/{png_name}"
    if match.group(1) == "png":
        return (f"{XIXOU_UPLOADS}{folder}/{png_name}", og_url)
    return (og_url,)


def is_allowed_image_url(url: str) -> bool:
    """Je limite les téléchargements aux PNG des dossiers publics explicitement connus."""
    if not isinstance(url, str):
        return False
    if url.startswith(MOON_ICONS):
        return _MOON_FILENAME.fullmatch(url[len(MOON_ICONS):]) is not None
    if not url.startswith(XIXOU_UPLOADS):
        return False
    folder, separator, name = url[len(XIXOU_UPLOADS):].rpartition("/")
    return bool(separator and folder in _ALLOWED_FOLDERS and _PNG_FILENAME.fullmatch(name))
