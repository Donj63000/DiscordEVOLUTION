"""Outils /evo en lecture seule, branchés sur les modules installés d'Evolution.

Aucune exécution de commande libre, aucun accès à l'environnement, aux tickets,
aux avertissements, aux MP ou aux messages supprimés. Pas de client web arbitraire.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import re

from utils.dofus_wiki import find_entries, search_key
from utils.drop_calculator import (
    DropRule, ProspectingSettings, format_percent, item_drop_rule,
    personal_rate, rate_bounds, source_count, threshold_met,
)
from utils.evo_config import EvoError
from utils.evo_equipment import EquipmentIndex, STAT_NAMES, equipment_search, exo_candidates
from utils.evo_safety import ToolContext, bounded_json, clean, compact, parse_arguments
from utils.exo_data import parse_effects
from utils.exo_engine import DISCLAIMER, STATS
from utils.xixou_api import monster_drop_sources, monster_record

log = logging.getLogger(__name__)


def text(*, nullable=False, maximum=120):
    return {"type": ["string", "null"] if nullable else "string", "maxLength": maximum}


def number(low, high, *, nullable=False):
    return {"type": ["integer", "null"] if nullable else "integer", "minimum": low, "maximum": high}


def choice(values):
    return {"type": "string", "enum": list(values)}


def array(item, minimum=0, maximum=3):
    return {"type": "array", "items": item, "minItems": minimum, "maxItems": maximum}


def tool(name, description, properties, domain="game"):
    return {
        "domain": domain,
        "schema": {"type": "function", "name": name, "description": description, "strict": True,
                   "parameters": {"type": "object", "properties": properties,
                                  "required": list(properties), "additionalProperties": False}},
    }


TOOLS = [
    tool("fiche_objet", "Fiche vérifiée : jets, niveau, conditions, panoplie. Nom ou référence item:ID.",
         {"objet": text()}),
    tool("sources_drop", "Monstres, zones et taux calculés en Python. PP null = taux de base.",
         {"objet": text(), "pp": number(0, 10000, nullable=True),
          "pp_groupe": number(0, 100000, nullable=True)}),
    tool("recette", "Ingrédients multipliés et, sur demande, sources de chaque ressource.",
         {"objet": text(), "quantite": number(1, 100), "avec_sources": {"type": "boolean"}}),
    tool("monstre", "Statistiques par niveau et drops explicitement présents dans les catalogues.",
         {"nom": text()}),
    tool("chercher_equipements", "5 équipements, triés par jets max dans l'ordre des priorités. Pas de prix.",
         {"type_objet": text(nullable=True), "niveau_min": number(1, 200), "niveau_max": number(1, 200),
          "priorites": array(choice(STAT_NAMES), 1, 3), "sans_malus": array(choice(STAT_NAMES), 0, 3),
          "nom_contient": text(maximum=60)}),
    tool("comparer_objets", "Compare les jets naturels et conditions de 2 ou 3 objets nommés.",
         {"objets": array(text(), 2, 3)}),
    tool("candidats_exo", "Candidats de remontage simple. Heuristique, PAS taux de réussite ni coût garanti.",
         {"bonus": choice(("pa", "pm", "po")), "type_objet": text(nullable=True),
          "niveau_min": number(1, 200), "niveau_max": number(1, 200)}),
    tool("ma_session_fm", "Lit uniquement l'atelier /exo du demandeur, en réponse privée. Ne pose aucune rune.", {}),
    tool("guide_fm", "Poids nominaux du moteur existant et limites du simulateur. Pas de taux Ankama certifié.",
         {"sujet": text()}),
    tool("guilde", "Présentation Discord et salons publics. Ne révèle aucune donnée Staff.", {}, "guild"),
    tool("connaissances_guilde", "Recherche les faits publics validés par le Staff : règles, histoire, habitudes.",
         {"question": text(maximum=180)}, "guild"),
    tool("membre", "Identifie un membre et son profil Dofus déclaré, jamais une biographie inventée.",
         {"nom": text()}, "guild"),
    tool("artisans", "Métiers déclarés des membres actuels. Aucun changement de profil.",
         {"metier": text(), "niveau_min": number(1, 100)}, "guild"),
    tool("activites", "Sorties publiées et accessibles à l'audience de ce salon, places et dates réelles.",
         {"recherche": text(), "jours": number(1, 60)}, "guild"),
    tool("conversation_salon", "Échantillon des 15 derniers messages du salon actuel, uniquement si autorisé.",
         {}, "guild"),
    tool("aide_bot", "Commandes disponibles pour les membres. N'exécute aucune commande.",
         {}, "both"),
    tool("demander_precision", "Une seule question courte si une information indispensable manque.",
         {"question": text(maximum=250)}, "both"),
]
BY_NAME = {spec["schema"]["name"]: spec for spec in TOOLS}


def schemas_for(question: str) -> list[dict]:
    """Filtre local peu coûteux. En cas de doute, les deux domaines sont fournis."""
    key = search_key(question)
    game = bool(re.search(r"\b(dofus|drop|drops|stuff|item|items|objet|equipement|force|terre|feu|eau|air|"
                          r"coiffe|cape|anneau|bottes|exo|fm|rune|runes|puits|pp|pa|pm|recette|craft|monstre|"
                          r"gelano|prospection|vita|lvl|niveau)\b", key))
    guild = bool(re.search(r"\b(guilde|membre|membres|qui|artisan|artisans|metier|metiers|profil|"
                           r"sortie|sorties|activite|activites|calendrier|salon|resume|reglement|evolution|"
                           r"inscrit|inscrits|organise|orga)\b", key))
    domains = {"game", "guild"} if game == guild else {"game"} if game else {"guild"}
    return [spec["schema"] for spec in TOOLS if spec["domain"] in domains | {"both"}]


def item_payload(detail, enrichment=None):
    effects = enrichment.effects if enrichment and enrichment.effects else detail.data.get("stats", [])
    effects = effects if isinstance(effects, (list, tuple)) else []
    bounds, unknown, _ = parse_effects(effects[:100])
    return {
        "objet": detail.entry.name, "reference": detail.entry.token,
        "type": detail.entry.category, "niveau": detail.entry.level,
        "jets_naturels": {STATS[k].name: list(v) for k, v in bounds.items()},
        "effets_non_interpretes": list(unknown),
        "details": dict(enrichment.details) if enrichment else {},
        "description": clean(enrichment.description if enrichment else detail.data.get("description", ""), 350),
        "source": detail.entry.url, "enrichissement": "Xixou" if enrichment else None,
        "dates_catalogues": list(enrichment.dates) if enrichment else [],
        "cache_ancien": bool(detail.stale or (enrichment and enrichment.stale)),
    }


def drop_payload(detail, enrichment, pp=None, pp_groupe=None):
    if pp is None and pp_groupe is not None:
        raise EvoError("Précise aussi ta PP personnelle ; la PP du groupe doit l'inclure.")
    settings = ProspectingSettings(pp, pp_groupe) if pp is not None else None
    rule = item_drop_rule(detail.entry.category, detail.entry.name)
    rows = []
    for source in enrichment.drops if enrichment else ():
        row = {
            "monstre": source.name, "taux_base": source.rate or None,
            "taux_par_niveau": list(source.level_rates), "seuil_pp": source.pp or None,
            "quota_partage": source.maximum or None, "zones": list(source.zones),
        }
        if settings is not None:
            bounds = rate_bounds(source)
            met = threshold_met(settings, source.pp)
            if rule is DropRule.QUEST or bounds is None:
                row["taux_personnel"] = None
                row["raison"] = "Objet de quête ou taux source non calculable."
            else:
                rates = [
                    Decimal(0) if met is False or source_count(source.maximum) == 0
                    else personal_rate(rate, settings, fixed=rule is DropRule.FIXED)
                    for rate in bounds
                ]
                row["taux_personnel"] = [format_percent(rate) for rate in rates]
                row["conditionnel"] = met is None
                row["seuil_atteint"] = met
        rows.append(row)
    return {
        "objet": detail.entry.name, "reference": detail.entry.token,
        "pp_personnelle": pp, "pp_groupe": pp_groupe, "regle": rule.value,
        "sources_drop": rows[:12], "nombre_sources": len(rows),
        "recoltes": [
            {"ressource": x.name, "metier": x.job, "niveau": x.level}
            for x in enrichment.harvests
        ] if enrichment else [],
        "source": detail.entry.url, "source_enrichissement": "Xixou" if enrichment else None,
        "dates_catalogues": list(enrichment.dates) if enrichment else [],
        "cache_ancien": bool(detail.stale or (enrichment and enrichment.stale)),
        "limites": (
            "Taux du jet individuel, pas chance finale de recevoir un quota partagé ni rendement horaire. "
            "Bonus de combat non inclus. Une source absente ne prouve pas l'impossibilité de drop. "
            + ("Enrichissement indisponible : vérifier XIXOU_API_KEY." if enrichment is None else "")
        ),
    }


class EvoTools:
    def __init__(self):
        self.equipment = EquipmentIndex()
        self.fetch_slots = asyncio.Semaphore(2)

    def wiki(self, ctx):
        wiki = ctx.bot.get_cog("DofusWikiCog")
        if wiki is None or getattr(wiki, "_closed", False):
            raise EvoError("L'encyclopédie du bot est indisponible. Réessaie avec /objet.")
        return wiki

    async def resolve(self, ctx, query, kind="item"):
        if not query.strip():
            raise EvoError("Il manque le nom de l'objet ou du monstre.")
        wiki = self.wiki(ctx)
        entries = await (wiki.client.items() if kind == "item" else wiki.client.monsters())
        found, fuzzy = find_entries(entries, query, 6)
        if not found:
            raise EvoError("Aucune correspondance dans le catalogue du bot.")
        if len(found) != 1 or fuzzy:
            return None, {"a_preciser": [
                {"nom": e.name, "niveau": e.level, "reference": e.token} for e in found
            ], "message": "Demander au joueur de choisir ; une suggestion approximative n'est pas une identité vérifiée."}
        return found[0], None

    async def detail(self, ctx, entry):
        wiki = self.wiki(ctx)
        async with self.fetch_slots:
            detail = await wiki.client.detail(entry)
            enrichment = None
            if entry.kind == "item" and wiki.enrichment_client and wiki.enrichment_client.enabled:
                try:
                    async with asyncio.timeout(8):
                        enrichment = await wiki.enrichment_client.enrich(detail)
                except Exception as exc:
                    # Pas de clé, réponse HTTP, URL ou texte de catalogue dans les logs.
                    log.debug("evo enrichment unavailable type=%s", type(exc).__name__)
            return detail, enrichment

    async def execute(self, name: str, raw: str, ctx: ToolContext, offered: set[str]) -> dict:
        ctx.check()
        if name not in offered or name not in BY_NAME:
            return {"erreur": "Outil non autorisé. Aucune action exécutée."}
        try:
            params = parse_arguments(raw, BY_NAME[name]["schema"]["parameters"])
            async with asyncio.timeout(24):
                value = await getattr(self, "do_" + name)(ctx, **params)
            self._sources(ctx, value)
            return json.loads(bounded_json(value, 5200))
        except EvoError as exc:
            return {"erreur": str(exc)}
        except ValueError:
            return {"erreur": "Données ou paramètres incohérents : aucun résultat inventé."}
        except TimeoutError:
            return {"erreur": "La source met trop de temps à répondre. Aucun résultat inventé."}
        except Exception as exc:
            log.warning("evo tool failed tool=%s type=%s", name, type(exc).__name__)
            return {"erreur": "Cette source est indisponible. Utilise la commande classique ou précise ta demande."}

    def _sources(self, ctx, value):
        if isinstance(value, dict):
            for item in value.values():
                self._sources(ctx, item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                self._sources(ctx, item)
        elif isinstance(value, str) and value.startswith("https://"):
            ctx.source(value)

    async def do_fiche_objet(self, ctx, objet):
        entry, ambiguity = await self.resolve(ctx, objet)
        return ambiguity if ambiguity else item_payload(*(await self.detail(ctx, entry)))

    async def do_sources_drop(self, ctx, objet, pp, pp_groupe):
        entry, ambiguity = await self.resolve(ctx, objet)
        if ambiguity:
            return ambiguity
        return drop_payload(*(await self.detail(ctx, entry)), pp, pp_groupe)

    async def do_recette(self, ctx, objet, quantite, avec_sources):
        entry, ambiguity = await self.resolve(ctx, objet)
        if ambiguity:
            return ambiguity
        detail, _ = await self.detail(ctx, entry)
        recipe = detail.data.get("recipe")
        if recipe is None or recipe == []:
            return {"objet": entry.name, "message": "Aucune recette renseignée.", "source": entry.url}
        if not isinstance(recipe, list) or len(recipe) > 100:
            raise EvoError("La recette reçue est incomplète.")
        totals = {}
        for row in recipe:
            if (not isinstance(row, dict) or type(row.get("qty")) is not int
                    or not 1 <= row["qty"] <= 1000000 or type(row.get("item_id")) is not int
                    or row["item_id"] <= 0 or not isinstance(row.get("name"), str)):
                raise EvoError("Un ingrédient ou une quantité de la recette est invalide.")
            identifier = row["item_id"]
            previous = totals.setdefault(identifier, {"nom": row["name"], "quantite": 0, "reference": f"item:{identifier}"})
            if previous["nom"] != row["name"]:
                raise EvoError("Deux ingrédients partagent un identifiant incohérent.")
            previous["quantite"] += row["qty"] * quantite
        ingredients = list(totals.values())
        if avec_sources:
            async def enrich(row):
                sources = await self.do_sources_drop(ctx, row["reference"], None, None)
                return {**row, "obtention": {
                    "drops": sources.get("sources_drop", [])[:3],
                    "recoltes": sources.get("recoltes", [])[:2],
                    "source": sources.get("source"),
                    "limites": sources.get("limites", sources.get("message", "")),
                }}
            ingredients = list(await asyncio.gather(*(enrich(row) for row in ingredients[:8])))
        return {
            "objet": entry.name, "exemplaires": quantite, "ingredients": ingredients,
            "nombre_ingredients": len(totals), "obtention_limitee_aux_8_premiers": bool(avec_sources and len(totals) > 8),
            "source": entry.url,
        }

    async def do_monstre(self, ctx, nom):
        entry, ambiguity = await self.resolve(ctx, nom, "monster")
        if ambiguity:
            return ambiguity
        detail, _ = await self.detail(ctx, entry)
        grades = []
        for row in detail.data.get("grades", [])[:8]:
            if not isinstance(row, dict):
                continue
            # Les zéros collectifs sont des sentinelles "non renseigné" dans ce wiki.
            missing = all(row.get(k) in (None, 0) for k in ("hp", "ap", "mp"))
            grades.append({
                "niveau": row.get("level"), "pv": None if missing else row.get("hp"),
                "pa": None if missing else row.get("ap"), "pm": None if missing else row.get("mp"),
                "resistances": row.get("resist"),
            })
        enrichment_client = self.wiki(ctx).enrichment_client
        catalog = None
        if enrichment_client and enrichment_client.enabled:
            try:
                async with asyncio.timeout(8):
                    catalog = await enrichment_client.catalog("monstres")
            except Exception as exc:
                log.debug("evo monster catalog unavailable type=%s", type(exc).__name__)
        match = monster_record(catalog, detail)
        drops = []
        if match:
            for resource, source in monster_drop_sources(match):
                drops.append({
                    "ressource": resource, "taux_base": source.rate or None,
                    "seuil_pp": source.pp or None, "quota_partage": source.maximum or None,
                    "taux_par_niveau": list(source.level_rates),
                })
            log.debug("evo monster inventory normalized drops=%s", len(drops))
        return {
            "monstre": entry.name, "grades": grades, "source": entry.url,
            "cache_ancien": detail.stale, "zones": match.get("zones", []) if match else [],
            "drops": drops[:10], "nombre_drops_renseignes": len(drops),
            "date_catalogue": catalog.get("genere_le") if catalog else None,
            "limites": "Sources disponibles, pas un rendement horaire ou un prix. "
                      "La fiche de ressource fournit le calcul PP détaillé. "
                      + ("Drops du catalogue Xixou ; liste limitée à 10."
                         if match else "Inventaire des drops indisponible ou identité non vérifiée."),
        }

    async def do_chercher_equipements(self, ctx, type_objet, niveau_min, niveau_max, priorites, sans_malus, nom_contient):
        rows, info = await self.equipment.get(self.wiki(ctx))
        return {**equipment_search(
            rows, category=type_objet, min_level=niveau_min, max_level=niveau_max,
            priorities=priorites, no_malus=sans_malus, query=nom_contient,
        ), "couverture": info}

    async def do_comparer_objets(self, ctx, objets):
        results = await asyncio.gather(*(self.do_fiche_objet(ctx, name) for name in objets))
        return {"objets": results, "limites": "Comparer les jets naturels ; le stuff complet et les prix sont inconnus."}

    async def do_candidats_exo(self, ctx, bonus, type_objet, niveau_min, niveau_max):
        rows, info = await self.equipment.get(self.wiki(ctx))
        return {**exo_candidates(
            rows, target=bonus, category=type_objet, min_level=niveau_min, max_level=niveau_max,
        ), "couverture": info}

    async def do_ma_session_fm(self, ctx):
        if not ctx.allow_private_fm:
            return {"confidentialite": "Ta session /exo est privée. Utilise /evo avec prive:True pour en parler sans la publier."}
        cog = ctx.bot.get_cog("ExoCog")
        view = getattr(cog, "views", {}).get((ctx.guild.id, ctx.member.id))
        if (view is None or view.owner_id != ctx.member.id or view.guild_id != ctx.guild.id
                or view.retired):
            raise EvoError("Tu n'as pas d'atelier /exo actif.")
        async with view.lock:
            if view.retired:
                raise EvoError("Cet atelier /exo vient d'être fermé.")
            session = view.session
            state = session.state
            return {
                "objet": session.item.name, "mode": session.mode, "revision": session.revision,
                "jets": {STATS[k].name: v for k, v in state.jets.items() if k in STATS},
                "puits": None if state.sink is None else str(state.sink),
                "derniere_rune": session.rune.name, "poids_rune": str(session.rune.weight),
                "journal_recent": compact(state.journal[-3:], max_string=150),
                "hypothese_probabilite_simulation": session.p,
                "avertissement": DISCLAIMER,
                "limites": "Puits inconnu reste inconnu. Le simulateur ne révèle pas les tirages du vrai jeu. Aucune rune jouée.",
            }

    async def do_guide_fm(self, ctx, sujet):
        key = search_key(sujet)
        selected = [
            stat for stat in STATS.values()
            if search_key(stat.name) in key or re.search(r"\b" + re.escape(stat.key) + r"\b", key)
        ][:6]
        if not selected:
            selected = [STATS[k] for k in ("pa", "pm", "po", "fo", "vi")]
        return {
            "poids_nominaux": {stat.name: str(stat.weight) for stat in selected},
            "source": "utils/exo_engine.py du bot, profil nominal Rétro",
            "principes": [
                "Un bonus déjà natif n'est pas un exo de ce même bonus.",
                "Facilité de remontage, coût en kamas et probabilité de passage sont trois questions distinctes.",
                "Le puits exact demande un état initial connu et un journal complet ; sinon il est inconnu.",
                "Les pertes du simulateur sont heuristiques, pas une preuve des mécanismes internes d'Ankama.",
                "Ne pas promettre qu'une rune va passer ni inventer les prix.",
            ],
            "avertissement": DISCLAIMER,
        }

    async def do_guilde(self, ctx):
        channels = [
            {"nom": c.name, "reference": f"<#{c.id}>"}
            for c in getattr(ctx.guild, "text_channels", ())
            if c.permissions_for(ctx.guild.default_role).view_channel and ctx.readable_here(c)
        ][:12]
        return {
            "nom": ctx.guild.name, "description": clean(getattr(ctx.guild, "description", ""), 500),
            "nombre_membres_discord": getattr(ctx.guild, "member_count", None),
            "salons_publics": channels,
            "note": "Le nombre Discord peut inclure les bots. Aucun historique privé consulté.",
        }

    async def do_connaissances_guilde(self, ctx, question):
        path = Path(ctx.config.knowledge_path)
        if not path.is_file() or path.stat().st_size > 64000:
            return {"faits": [], "note": "Pas de fiche de connaissances publiques renseignée par le Staff."}
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload.get("guild_id")) != str(ctx.guild.id):
            return {"faits": [], "note": "La fiche de connaissances n'est pas configurée pour cette guilde."}
        words = set(search_key(question).split())
        scored = []
        for fact in payload.get("facts", [])[:100]:
            if not isinstance(fact, dict) or not isinstance(fact.get("text"), str):
                continue
            haystack = search_key(str(fact.get("title", "")) + " " + fact["text"] + " " + str(fact.get("keywords", "")))
            score = len(words & set(haystack.split()))
            if score:
                scored.append((score, fact))
        scored.sort(key=lambda row: -row[0])
        return {
            "faits": [
                {"titre": clean(f.get("title"), 100), "texte": clean(f["text"], 650)}
                for _, f in scored[:4]
            ],
            "source": "Fiche publique validée par le Staff",
            "mise_a_jour": payload.get("updated_at"), "note": "Aucune autre information sur l'histoire de la guilde n'est inventée.",
        }

    def legacy_available(self, ctx):
        # Jobs/Players historiques ne sont pas cloisonnés par guild_id.
        return len(ctx.bot.guilds) == 1 and ctx.bot.guilds[0].id == ctx.guild.id

    def members_matching(self, ctx, query):
        key = search_key(query)
        if key in {"moi", "mon profil", "me", ""}:
            return [ctx.member]
        mention = re.fullmatch(r"<@!?(\d+)>|(\d{1,20})", query.strip())
        if mention:
            member = ctx.guild.get_member(int(mention.group(1) or mention.group(2)))
            return [member] if member else []
        players = ctx.bot.get_cog("PlayersCog") if self.legacy_available(ctx) else None
        characters = getattr(players, "persos_data", {}) if getattr(players, "initialized", False) else {}
        exact, approximate = [], []
        for member in list(getattr(ctx.guild, "members", ()))[:5000]:
            if member.bot:
                continue
            names = [member.display_name, member.name]
            if ctx.config.public_member_data or member.id == ctx.member.id:
                data = characters.get(str(member.id), {})
                names += [data.get("main", "")] + data.get("mules", [])[:8]
            keys = [search_key(name) for name in names if name]
            if key in keys:
                exact.append(member)
            elif any(key in name for name in keys):
                approximate.append(member)
        return (exact or approximate)[:6]

    async def do_membre(self, ctx, nom):
        members = self.members_matching(ctx, nom)
        if len(members) != 1:
            return {"a_preciser": [m.display_name for m in members], "message": "Précise le membre ou sa mention."}
        member = members[0]
        result = {"membre": member.display_name, "reference": f"<@{member.id}>"}
        if not ctx.config.public_member_data and member.id != ctx.member.id:
            result["note"] = "Lecture des profils publics désactivée dans Evo par le Staff."
            return result
        if self.legacy_available(ctx):
            players = ctx.bot.get_cog("PlayersCog")
            if getattr(players, "initialized", False):
                data = players.persos_data.get(str(member.id), {})
                result["personnage"] = data.get("main")
                result["mules_declarees"] = data.get("mules", [])[:8]
            jobs = ctx.bot.get_cog("JobCog")
            if getattr(jobs, "initialized", False):
                result["metiers_declares"] = jobs.jobs_data.get(str(member.id), {}).get("jobs", {})
        profiles = ctx.bot.get_cog("ProfilCog")
        if profiles is not None:
            profile = await profiles.store.get_by_owner(ctx.guild.id, member.id)
            if profile and profile.guild_id == ctx.guild.id and profile.owner_id == member.id:
                result["profil_declare"] = {
                    "personnage": profile.player_name, "classe": profile.classe, "niveau": profile.level,
                    "pa": profile.pa, "pm": profile.pm,
                    "stats": {k: v.total for k, v in profile.stats.items()},
                    "mise_a_jour": profile.updated_at,
                }
        result["source"] = "Profils et métiers déclarés dans les modules du bot ; pas des observations en jeu."
        return result

    async def do_artisans(self, ctx, metier, niveau_min):
        if not ctx.config.public_member_data or not self.legacy_available(ctx):
            raise EvoError("L'annuaire des métiers n'est pas autorisé pour Evo dans cette configuration.")
        jobs = ctx.bot.get_cog("JobCog")
        if not getattr(jobs, "initialized", False):
            raise EvoError("L'annuaire des métiers n'est pas encore chargé.")
        key = search_key(metier)
        if len(key) < 3:
            raise EvoError("Précise le métier recherché.")
        results = []
        for uid, row in jobs.jobs_data.items():
            if not str(uid).isdigit():
                continue
            member = ctx.guild.get_member(int(uid))
            if member is None or member.bot:
                continue
            for job, level in row.get("jobs", {}).items():
                if key in search_key(job) and type(level) is int and niveau_min <= level <= 100:
                    results.append({"membre": member.display_name, "metier": job, "niveau": level})
        results.sort(key=lambda row: (-row["niveau"], row["membre"]))
        return {"artisans": results[:10], "total": len(results), "source": "Métiers déclarés via /job."}

    async def do_activites(self, ctx, recherche, jours):
        cog = ctx.bot.get_cog("ActiviteCog")
        if cog is None or not getattr(cog, "initialized", False):
            raise EvoError("Le calendrier des activités n'est pas encore disponible.")
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=jours)
        rows = []
        for event in cog.events_for_guild(ctx.guild.id).values():
            if event.get("cancelled") or not event.get("message_id"):
                continue  # Ne révèle pas les brouillons.
            channel = ctx.guild.get_channel(int(event.get("channel_id") or 0))
            if not ctx.readable_here(channel):
                continue
            try:
                starts = datetime.fromisoformat(event["starts_at"])
            except (KeyError, ValueError):
                continue
            if starts.tzinfo is None or not now <= starts <= end:
                continue
            if recherche and search_key(recherche) not in search_key(event.get("titre", "")):
                continue
            participants = event.get("participants", [])
            creator = ctx.guild.get_member(int(event.get("creator_id") or 0))
            rows.append({
                "titre": event.get("titre"), "date": starts.isoformat(),
                "description": clean(event.get("description"), 300),
                "createur": creator.display_name if creator else "non renseigné",
                "inscrits": len(participants), "capacite": event.get("capacity", 8),
                "membres": [
                    m.display_name for uid in participants[:12]
                    if (m := ctx.guild.get_member(int(uid))) is not None
                ],
                "source": f"https://discord.com/channels/{ctx.guild.id}/{channel.id}/{event['message_id']}",
            })
        rows.sort(key=lambda row: row["date"])
        return {"activites": rows[:8], "horizon_jours": jours, "note": "Activités publiées seulement ; aucune inscription effectuée."}

    async def do_conversation_salon(self, ctx):
        if ctx.channel.id not in ctx.config.history_channels:
            raise EvoError("La lecture de l'historique de ce salon n'est pas activée pour Evo.")
        for subject in (ctx.member, ctx.guild.me):
            if not ctx.channel.permissions_for(subject).read_message_history:
                raise EvoError("Permission de lecture de l'historique absente.")
        rows = []
        async for message in ctx.channel.history(limit=15):
            if message.author.bot or getattr(message, "webhook_id", None):
                continue
            rows.append({
                "auteur": clean(message.author.display_name, 80),
                "texte": clean(message.content, 250),
                "date": message.created_at.isoformat(), "source": message.jump_url,
            })
        rows.reverse()
        return {
            "messages": rows, "echantillon": "Au plus 15 messages récents du salon actuel, pas un historique complet.",
            "compte_dans_echantillon": dict(Counter(row["auteur"] for row in rows)),
            "confidentialite": "Aucun MP, pièce jointe ni message supprimé ; pas de conservation durable par Evo.",
        }

    async def do_aide_bot(self, ctx):
        roots = {"objet", "recette", "equipement", "monstre", "exo", "job", "membre", "profil", "activite", "aide", "evo"}
        forbidden = {"del", "delete", "reset", "supprimer", "annuler", "modifier", "gerer", "publier"}
        rows = []
        for command in ctx.bot.tree.walk_commands():
            parts = command.qualified_name.split()
            if parts[0] in roots and not forbidden.intersection(parts):
                rows.append({"commande": "/" + command.qualified_name, "description": clean(command.description, 100)})
        return {
            "commandes": rows[:18],
            "capacites_evo": "Questions et consultations en lecture seule. Pour agir, utiliser la commande Discord autorisée.",
            "web": "Pas de recherche Web générale ni de frais web_search dans cette version.",
        }

    async def do_demander_precision(self, ctx, question):
        return {"clarification": question}
