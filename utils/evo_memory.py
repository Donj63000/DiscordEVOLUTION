"""Je garde les références utiles et prépare les suivis sans interprétation payante."""
from __future__ import annotations

import json
import logging
import re
import unicodedata

from utils.evo_safety import clean

log = logging.getLogger(__name__)
STAT_UNITS = frozenset({
    "force", "fo", "intelligence", "ine", "chance", "cha", "agilite", "age", "sagesse", "sa",
    "vitalite", "vi", "vie", "pp", "prospection", "pa", "pm", "po", "portee", "dommage",
    "dommages", "do", "soins", "resistance", "resistances", "puissance", "initiative", "pods",
    "critique", "critiques", "cc", "kamas", "runes",
})


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


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", normalized(text)).strip()


def has_stat_unit_nearby(text, start, end):
    """Je distingue les bornes d'une statistique des niveaux d'équipement."""
    before = _words(text[:start]).split()
    after = _words(text[end:]).split()
    while after and after[0] in {"en", "de", "d", "point", "points", "pts"}:
        after.pop(0)
    return bool((before and before[-1] in STAT_UNITS) or (after and after[0] in STAT_UNITS))


def _drop_pp(text):
    """Je distingue les deux PP avant d'enlever leurs clauses du suivi courant."""
    values = {}
    rest = text
    patterns = (
        ("pp_groupe", r"\bgroupe\s*(?:(?:de|a|avec)\s*)?(\d{1,6})\s*(?:de\s*)?pp\b(?:\s*(?:de|du)\s+groupe\b)?"),
        ("pp_groupe", r"\b(\d{1,6})\s*(?:de\s*)?pp\s*(?:(?:de|du|en|au)\s+)?groupe\b"),
        ("pp", r"(?<!\d)(\d{1,5})\s*(?:de\s*)?pp\b(?:\s*(?:personnelles?|perso)\b)?"),
    )
    for key, pattern in patterns:
        matches = list(re.finditer(pattern, rest))
        if len(matches) > 1 or matches and key in values:
            return None, text
        if matches:
            amount = int(matches[0][1])
            if amount > (100000 if key == "pp_groupe" else 10000):
                return None, text
            values[key] = amount
            rest = re.sub(pattern, " ", rest)
    return values, rest


def _drop_monsters(text, known):
    """Je ne transforme que les noms exacts et les deux abréviations explicites CM/DC."""
    names = {_words(name): name for name in known}
    names.update({"cm": "Chêne Mou", "dc": "Dragon Cochon"})
    names.setdefault("chene mou", "Chêne Mou")
    names.setdefault("dragon cochon", "Dragon Cochon")
    clause = re.search(r"\b(?:sur|chez|contre)\s+", text)
    if clause is None:
        return [], text
    for key, name in list(names.items()):
        names.setdefault(re.sub(r"^(?:le|la|les|l)\s+", "", key), name)
    pattern = re.compile(
        r"(?:(?:le|la|les|l|un|une)\s+)?("
        + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        + r")(?![a-z0-9])",
    )
    rest = text[clause.end():].strip()
    targets = []
    while True:
        match = pattern.match(rest)
        if match is None:
            return (None, text) if not targets else (targets, text[:clause.start()] + " " + rest)
        name = names[match[1]]
        if name not in targets:
            targets.append(name)
        rest = rest[match.end():].strip()
        conjunction = re.match(r"(?:et|ou)\s+", rest)
        if conjunction is None or not pattern.match(rest[conjunction.end():]):
            return targets, text[:clause.start()] + " " + rest
        rest = rest[conjunction.end():]


def _family_suffixes(candidates):
    """Je calcule les choix de couleur depuis le préfixe commun exact d'une famille vérifiée."""
    words = [_words(candidate.get("nom", "")).split() for candidate in candidates]
    prefix = 0
    if len(words) < 2:
        return {}
    for column in zip(*words):
        if len(set(column)) != 1:
            break
        prefix += 1
    if prefix < 2:
        return {}
    return {candidate["reference"]: " ".join(tokens[prefix:])
            for candidate, tokens in zip(candidates, words) if 1 <= len(tokens[prefix:]) <= 2}


def _drop_followup(brief, question):
    """Je relie uniquement une confirmation exacte ou des contraintes au sujet drop actif."""
    if brief.get("sujet") != "sources_drop":
        return None
    text = _words(question)
    if any(mark in question for mark in ('"', "«", "»", ";", "\n")):
        return None
    if re.search(r"\b(?:non|pas|autre|plutot|change|nouveau|nouvelle)\b", text):
        return None
    context = brief.get("drop_context", {})
    pending = brief.get("choix_en_attente", {})
    if pending.get("outil") == "sources_drop":
        candidates = pending.get("candidats", [])
        params = dict(pending.get("arguments", {}))
    else:
        candidates = [context] if context.get("reference") else []
        params = dict(brief.get("sources_drop", {}))
    suffixes = _family_suffixes(candidates) if pending.get("famille") else {}
    identity = None
    for candidate in candidates:
        name = candidate.get("nom") or candidate.get("objet")
        if not isinstance(name, str) or not name.strip() or not isinstance(candidate.get("reference"), str):
            continue
        choices = [_words(name)]
        if candidate["reference"] in suffixes:
            choices.append(suffixes[candidate["reference"]])
        pattern = r"(?<![a-z0-9])(?:" + "|".join(re.escape(choice) for choice in choices) + r")(?![a-z0-9])"
        if re.search(pattern, text):
            if identity is not None:
                return None
            identity = candidate
            text = re.sub(pattern, " ", text)
    if pending.get("outil") == "sources_drop" and identity is None:
        return None
    if identity is not None:
        params["objet"] = identity["reference"]
    if not isinstance(params.get("objet"), str) or not re.fullmatch(r"item:\d+", params["objet"]):
        return None
    values, rest = _drop_pp(text)
    if values is None:
        return None
    targets, rest = _drop_monsters(rest, context.get("monstres", []))
    if targets is None:
        return None
    permitted = {"oui", "ok", "avec", "et", "j", "ai", "le", "la", "les", "l", "c", "est",
                 "bien", "je", "choisis", "confirme", "pour", "du", "drop", "taux", "donc",
                 "stp", "evo", "en", "mon", "ma", "combien", "de", "chance", "chances",
                 "un", "une", "probabilite", "probabilites"}
    if set(rest.split()) - permitted or not (identity or values or targets):
        return None
    params = {key: params.get(key) for key in ("objet", "pp", "pp_groupe")}
    params.update(values)
    return params, targets, identity


def _remember_drop(brief, previous, question, evidence):
    """Je conserve une identité vérifiée et les contraintes, séparées des arguments d'outil."""
    prepared = _drop_followup(previous, question)
    if prepared:
        params, targets, identity = prepared
        brief["sources_drop"] = params
        context = dict(previous.get("drop_context", {}))
        if identity:
            if context.get("reference") != identity["reference"]:
                context = {}
            context.update(objet=identity.get("nom") or identity.get("objet"),
                           reference=identity["reference"])
        if targets:
            context["monstres_demandes"] = targets
        brief["drop_context"] = context
    for item in evidence:
        if item.get("outil") != "sources_drop":
            continue
        result, params = item.get("resultat", {}), arguments(item)
        if not isinstance(result, dict) or "erreur" in result:
            continue
        brief["sujet"] = "sources_drop"
        candidates = result.get("a_preciser") or result.get("variantes")
        if isinstance(candidates, list):
            safe = [{"nom": clean(row.get("nom") or row.get("objet"), 120), "reference": row["reference"]}
                    for row in candidates[:6] if isinstance(row, dict)
                    and isinstance(row.get("nom") or row.get("objet"), str)
                    and isinstance(row.get("reference"), str)
                    and re.fullmatch(r"item:\d+", row["reference"])]
            values, _ = _drop_pp(_words(question))
            pending = {key: params.get(key) for key in ("objet", "pp", "pp_groupe")}
            if prepared:
                pending.update(prepared[0])
            pending.update(values or {})
            brief["choix_en_attente"] = {
                "outil": "sources_drop", "arguments": pending, "candidats": safe,
            }
            if result.get("famille"):
                brief["choix_en_attente"]["famille"] = clean(result["famille"], 120)
            brief.pop("sources_drop", None)
            log.debug("evo memory exact_drop_choice_pending candidates=%s", len(safe))
            continue
        if not re.fullmatch(r"item:\d+", str(result.get("reference", ""))):
            continue
        old = brief.get("drop_context", {})
        same = old.get("reference") == result["reference"]
        stored = dict(brief.get("sources_drop", {})) if same else {}
        for argument, output in (("pp", "pp_personnelle"), ("pp_groupe", "pp_groupe")):
            value = params.get(argument)
            value = result.get(output) if value is None else value
            if value is not None or argument not in stored:
                stored[argument] = value
        stored["objet"] = result["reference"]
        values, _ = _drop_pp(_words(question))
        stored.update(values or {})
        names = [clean(row["monstre"], 100) for row in result.get("sources_drop", [])[:12]
                 if isinstance(row, dict) and isinstance(row.get("monstre"), str)]
        _, remaining = _drop_pp(_words(question))
        targets, _ = _drop_monsters(remaining, names)
        brief["sources_drop"] = stored
        brief["drop_context"] = {
            "objet": clean(result.get("objet"), 120), "reference": result["reference"],
            "monstres": list(dict.fromkeys(names)),
            "monstres_demandes": targets or (old.get("monstres_demandes", []) if same else []),
        }
        brief.pop("choix_en_attente", None)
        log.debug("evo memory verified_drop_context reference=%s", result["reference"])


def update_brief(previous, question, evidence, answer):
    """Les références viennent des outils et l'ordre vient de la réponse publiée."""
    brief = dict(previous)
    text = normalized(question)
    preferences = dict(brief.get("preferences", {}))
    equipment_range = re.search(
        r"\b(?:entre|niveaux?|lv|lvl)\s*(\d{1,3})\s*(?:et|a|à|-)\s*(\d{1,3})\b", text,
    )
    if equipment_range and has_stat_unit_nearby(text, equipment_range.start(), equipment_range.end()):
        equipment_range = None
    equipment_range = equipment_range if equipment_range and (
        re.search(r"\b(?:coiffes?|capes?|anneaux?|bottes?|equipements?|items?|stuff|chapeaux?|plage)\b", text)
        or previous.get("sujet") in {"chercher_equipements", "comparer_objets", "fiche_objet"}
    ) else None
    player_level = False
    for name, pattern in {
        "classe": r"\b(cra|iop|sacrieur|eniripsa|enutrof|osamodas|sadida|sram|ecaflip|feca|xelor|pandawa)\b",
        "element": r"\b(terre|feu|eau|air|multi)\b",
        "usage": r"\b(pvm|pvp)\b",
        "niveau": r"\b(?:lv|lvl|niveau|nv)\s*(\d{1,3})\b",
        "pa": r"\b(\d{1,2})\s*pa\b",
        "pm": r"\b(\d{1,2})\s*pm\b",
    }.items():
        match = re.search(pattern, text)
        if match and not (name == "niveau" and equipment_range):
            preferences[name] = match.group(1)
            player_level = player_level or name == "niveau"
    character = re.search(
        r"\b(?:je suis|je joue|mon|ma)\s+(?:un |une )?"
        r"(?:cra|iop|sacrieur|eniripsa|enutrof|osamodas|sadida|sram|ecaflip|feca|xelor|pandawa)"
        r"\s+(?:(?:terre|feu|eau|air|multi)\s+)?(\d{1,3})\b", text,
    )
    if character and 1 <= int(character[1]) <= 200:
        preferences["niveau"] = character[1]
        player_level = True
    if player_level:
        preferences.pop("niveau_min_equipement", None)
        preferences.pop("niveau_max_equipement", None)
    if equipment_range and 1 <= int(equipment_range[1]) <= int(equipment_range[2]) <= 200:
        preferences.update(niveau_min_equipement=int(equipment_range[1]),
                           niveau_max_equipement=int(equipment_range[2]))
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
        if name in {"sources_drop", "recette", "chercher_equipements", "monstre", "comparer_objets", "fiche_objet"}:
            brief["sujet"] = name
            brief["derniere_requete"] = {"outil": name, "arguments": params}
        if name == "recette" and result.get("reference"):
            stored = dict(params)
            stored["objet"] = result["reference"]
            brief[name] = stored
        rows = result.get("resultats") or result.get("objets") or result.get("variantes")
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
    if evidence and brief.get("sujet") != "sources_drop":
        brief.pop("choix_en_attente", None)
    _remember_drop(brief, previous, question, evidence)
    return brief


def followup_tools(brief, question):
    """Je reconnais uniquement les suivis bornés dont les paramètres sont déjà connus."""
    text = normalized(question).rstrip(" ?!.")
    drop = _drop_followup(brief, question)
    if drop:
        return [("sources_drop", drop[0])]
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
