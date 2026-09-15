"""Je garde les références utiles et prépare les suivis sans interprétation payante."""
from __future__ import annotations

import json
import re
import unicodedata

from utils.evo_safety import clean


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c)).replace("’", "'").strip()


def wants_depth(question):
    """Seul un impératif direct de l'auteur déclenche le spécialiste."""
    return bool(re.match(r"^(?:evo[, :]+)?approfondis(?:\s|[.!?:]|$)", normalized(question)))


def small_talk(question):
    return normalized(question).rstrip(" !?.") in {
        "salut", "coucou", "hello", "bonjour", "yo", "merci", "merci evo",
        "confidentialite", "vie privee",
    }


def arguments(item):
    value = item.get("parametres", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def update_brief(previous, question, evidence, answer):
    """Les références viennent des outils et l'ordre vient de la réponse publiée."""
    brief = dict(previous)
    text = normalized(question)
    preferences = dict(brief.get("preferences", {}))
    for name, pattern in {
        "classe": r"\b(cra|iop|sacrieur|eniripsa|enutrof|osamodas|sadida|sram|ecaflip|feca|xelor|pandawa)\b",
        "element": r"\b(terre|feu|eau|air|multi)\b",
        "usage": r"\b(pvm|pvp)\b",
        "niveau": r"\b(?:lvl|niveau|nv)\s*(\d{1,3})\b",
        "pa": r"\b(\d{1,2})\s*pa\b",
        "pm": r"\b(\d{1,2})\s*pm\b",
    }.items():
        match = re.search(pattern, text)
        if match:
            preferences[name] = match.group(1)
    character = re.search(
        r"\b(?:je suis|je joue|mon|ma)\s+(?:un |une )?"
        r"(?:cra|iop|sacrieur|eniripsa|enutrof|osamodas|sadida|sram|ecaflip|feca|xelor|pandawa)"
        r"\s+(?:(?:terre|feu|eau|air|multi)\s+)?(\d{1,3})\b", text,
    )
    if character and 1 <= int(character[1]) <= 200:
        preferences["niveau"] = character[1]
    if preferences:
        brief["preferences"] = preferences
    rendered = normalized(answer)
    displayed = {}
    has_selection = False
    for item in evidence:
        name, params = item.get("outil"), arguments(item)
        result = item.get("resultat", {})
        if not isinstance(result, dict) or "erreur" in result:
            continue
        if name in {"sources_drop", "recette", "chercher_equipements", "monstre", "comparer_objets"}:
            brief["sujet"] = name
            brief["derniere_requete"] = {"outil": name, "arguments": params}
        if name in {"sources_drop", "recette"} and result.get("reference"):
            stored = dict(params)
            stored["objet"] = result["reference"]
            brief[name] = stored
        rows = result.get("resultats") or result.get("objets")
        if isinstance(rows, list):
            has_selection = True
            for row in rows:
                if not isinstance(row, dict):
                    continue
                label = row.get("objet") or row.get("nom")
                reference = row.get("reference")
                if isinstance(label, str) and isinstance(reference, str):
                    position = rendered.find(normalized(label))
                    if position >= 0:
                        displayed[reference] = (position, {"objet": clean(label, 100), "reference": reference})
    if has_selection:
        brief["selection"] = [row for _, row in sorted(displayed.values(), key=lambda x: x[0])[:5]]
    return brief


def followup_tools(brief, question):
    """Je reconnais uniquement les suivis bornés dont les paramètres sont déjà connus."""
    text = normalized(question).rstrip(" ?!.")
    source = brief.get("sources_drop")
    pp = re.fullmatch(r"(?:et\s+)?(?:avec\s+|j'ai\s+)?(\d{1,5})\s*pp(?:\s+(?:personnelle|personnelles))?", text)
    if pp and brief.get("sujet") == "sources_drop" and source and int(pp[1]) <= 10000:
        return [("sources_drop", {**source, "pp": int(pp[1])})]
    recipe = brief.get("recette")
    quantity = re.fullmatch(r"(?:et\s+)?(?:j'en veux|pour)\s+(\d{1,3})(?:\s+exemplaires?)?", text)
    if quantity and brief.get("sujet") == "recette" and recipe and 1 <= int(quantity[1]) <= 100:
        return [("recette", {**recipe, "quantite": int(quantity[1]), "page": 1})]
    previous = brief.get("derniere_requete", {})
    if text in {"suite", "page suivante", "la suite", "liste complete", "la liste complete"}:
        if previous.get("outil") in {"recette", "monstre"}:
            params = dict(previous.get("arguments", {}))
            params["page"] = params.get("page", 1) + 1
            return [(previous["outil"], params)]
    selection = brief.get("selection", [])
    ordinal = re.fullmatch(r"(?:le|la)\s+(premier|premiere|deuxieme|second|seconde|troisieme|quatrieme|cinquieme)", text)
    if ordinal:
        index = {"premier": 0, "premiere": 0, "deuxieme": 1, "second": 1,
                 "seconde": 1, "troisieme": 2, "quatrieme": 3, "cinquieme": 4}[ordinal[1]]
        if len(selection) > index:
            return [("fiche_objet", {"objet": selection[index]["reference"]})]
    if re.fullmatch(r"compare(?:-moi)? (?:les deux premiers|les 2 premiers|les deux premieres|les 2 premieres)", text) and len(selection) >= 2:
        return [("comparer_objets", {"objets": [row["reference"] for row in selection[:2]]})]
    if text in {"compare-les", "compare les"} and 2 <= len(selection) <= 3:
        return [("comparer_objets", {"objets": [row["reference"] for row in selection]})]
    return []
