"""Outils Luna : les résultats privés ne retournent JAMAIS dans le canal public.

Les opérations d'écriture préparent uniquement une carte privée à confirmer.
Le propriétaire/serveur sont issus de ToolContext, absents du schéma modèle.
"""
from .models import Actor, Profile, CLASSES, SLOTS, BuildError, canonical, values
from .config import flag

NAMES = frozenset({"build_lire", "build_lister", "build_rechercher_objets", "build_creer_brouillon",
                   "build_preparer_modification", "build_comparer", "build_optimiser",
                   "build_simulateur", "build_prix", "build_optimisation_avancee", "build_recettes", "build_vers_exo"})


def definitions(tool, text, number, choice, array):
    return [
        tool("build_simulateur", "Ouvre en MP le simulateur privé sort/arme, cible, comparaison et optimisation de dégâts. Aucun résultat privé retourné au modèle.", {"build": text(maximum=36)}),
        tool("build_prix", "Ouvre en MP le carnet privé de prix par serveur et jets. Toute saisie ou suppression attend la confirmation du propriétaire.", {"build": text(maximum=36)}),
        tool("build_optimisation_avancee", "Ouvre en MP les contraintes, les objets possédés et le budget de recherche. Le propriétaire confirme toute modification.", {"build": text(maximum=36)}),
        tool("build_recettes", "Envoie en MP les matières premières et recettes indisponibles du build, sans prix inventés.", {"build": text(maximum=36)}),
        tool("build_vers_exo", "Prépare en MP le transfert des jets vers /exo. Le propriétaire renseigne le puits puis confirme le remplacement de sa session.", {"build": text(maximum=36), "emplacement": choice(SLOTS)}),
        tool("build_lire", "Envoie au demandeur sa fiche privée par MP. Le contenu ne revient pas dans la conversation IA publique. Référence UUID requise.", {"build": text(maximum=36)}),
        tool("build_lister", "Envoie en MP uniquement au demandeur sa liste privée de builds. Aucun nom de build privé n'est retourné à l'IA.", {}),
        tool("build_rechercher_objets", "Recherche publique, bornée à cinq objets, dans le catalogue du builder. Ne choisit et n'équipe aucun objet à la place du membre.",
             {"recherche": text(maximum=80), "emplacement": choice(SLOTS), "niveau": number(1, 200)}),
        tool("build_creer_brouillon", "Prépare une carte privée de nouveau build. Pas de sauvegarde avant confirmation par le demandeur dans Discord.",
             {"nom": text(maximum=80), "classe": choice(CLASSES), "niveau": number(1, 200)}),
        tool("build_preparer_modification", "Prévisualise en MP un remplacement sur le build DU DEMANDEUR. Ne sauvegarde rien avant son clic de confirmation. Les résultats privés ne sont pas exposés au salon.",
             {"build": text(maximum=36), "emplacement": choice(SLOTS), "objet": text(maximum=120)}),
        tool("build_comparer", "Envoie en MP la comparaison de deux builds appartenant au demandeur. Ne renvoie pas leurs statistiques privées au modèle.",
             {"premier": text(maximum=36), "second": text(maximum=36)}),
        tool("build_optimiser", "Recherche bornée sur le build DU DEMANDEUR, résultats en MP à confirmer. Pas d'optimum global ni de conformité promis si données incomplètes.",
             {"build": text(maximum=36), "objectif": choice(("fo", "ine", "cha", "age", "pp", "vi", "sa", "do", "so")),
              "pa_min": number(0, 30), "pm_min": number(0, 30), "po_min": number(0, 30),
              "verrouilles": array(choice(SLOTS), 0, 16), "exos_autorises": {"type": "boolean"}}),
    ]


async def dispatch(name, ctx, **params):
    from utils.evo_config import EvoError
    from .catalog import search
    from .embeds import card, safe
    from .views import BuildView, OptimizationView
    from .comparison import compare
    from .optimizer import Constraints
    from io import BytesIO
    import discord
    if name not in NAMES or not flag("BUILD_ENABLED", True) or not flag("BUILD_AI_ENABLED"):
        raise EvoError("Les outils Luna du builder sont désactivés. Utilise /build directement.")
    await ctx.ensure_access()
    cog = ctx.bot.get_cog("BuildCog")
    if cog is None:
        raise EvoError("Evolution Build n'est pas chargé.")
    try:
        cog.ensure_ready()
        actor = Actor(guild_id=ctx.guild.id, user_id=ctx.member.id)
        if name == "build_rechercher_objets":
            catalog = cog.catalogs.latest
            if catalog is None:
                raise BuildError("Catalogue du builder indisponible.")
            found = search(catalog, params["recherche"], params["emplacement"], params["niveau"], limit=5)
            return {"objets": [{"nom": i.name, "reference": i.ref, "niveau": i.level,
                                "effets_non_interpretes": sum(e.kind in {"unknown", "context"} for e in i.effects),
                                "source": i.source} for i in found], "source_catalogue": "https://xixou.io/"}
        sent = getattr(ctx, "_build_private_cards", 0)
        if sent >= 2:
            raise BuildError("Deux cartes privées ont déjà été demandées. Continuer avec /build.")
        setattr(ctx, "_build_private_cards", sent + 1)
        if name in {"build_simulateur", "build_prix", "build_optimisation_avancee", "build_vers_exo"}:
            from .advanced_views import SimulatorView, PriceBookView, AdvancedOptimizerView, ExoTransferView
            current, _, catalog = await cog.service.inspect(actor, params["build"])
            if name == "build_simulateur":
                view = SimulatorView(cog, actor, current)
                content = view.content()
            elif name == "build_prix":
                view = PriceBookView(cog, actor, current, catalog)
                content = "Carnet privé de prix, associés au serveur et aux jets exacts. Toute modification attend ta confirmation."
            elif name == "build_vers_exo":
                from .fm_adapter import to_exo_values
                instance = current.in_slot(params["emplacement"])
                if instance is None:
                    raise BuildError("Emplacement vide.")
                view = ExoTransferView(cog, actor, to_exo_values(instance, catalog.resolve(instance)))
                content = "Renseigne le puits, puis confirme l'ouverture de /exo avec ces jets."
            else:
                view = AdvancedOptimizerView(cog, actor, current)
                content = view.content()
            try:
                view.message = await ctx.member.send(content, view=view, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                view.stop()
                raise
        elif name == "build_recettes":
            import asyncio
            from .crafting import shopping_list
            current, _, catalog = await cog.service.inspect(actor, params["build"])
            wiki = cog.wiki()
            if wiki is None:
                raise BuildError("Wiki indisponible.")
            async with asyncio.timeout(30):
                result = await shopping_list(current, catalog, wiki)
            await ctx.member.send(file=discord.File(BytesIO(canonical(result).encode()), filename="recettes-privees.json"), allowed_mentions=discord.AllowedMentions.none())
        elif name == "build_creer_brouillon":
            preview = await cog.service.new(actor, params["nom"], Profile(classe=params["classe"], level=params["niveau"]))
            await cog.dm_preview(ctx.member, actor, preview)
        elif name == "build_preparer_modification":
            current = await cog.repository.get(actor, params["build"])
            preview = await cog.service.equipment(actor, current.id, current.revision, params["emplacement"], params["objet"])
            await cog.dm_preview(ctx.member, actor, preview)
        elif name == "build_lire":
            build, report, catalog = await cog.service.inspect(actor, params["build"])
            cog.remember(build)
            view = BuildView(cog, actor, build)
            try:
                view.message = await ctx.member.send(embed=card(build, report, catalog, cog.repository.durable), view=view, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                view.stop()
                raise
        elif name == "build_lister":
            builds = await cog.repository.list(actor)
            for build in builds:
                cog.remember(build)
            text = "\n".join(f"{b.name} · {b.profile.classe} {b.profile.level}\n{b.id}" for b in builds) or "Aucun build enregistré. /build creer"
            await ctx.member.send(file=discord.File(BytesIO(text.encode()), filename="mes-builds-prives.txt"), allowed_mentions=discord.AllowedMentions.none())
        elif name == "build_comparer":
            a, ra, _ = await cog.service.inspect(actor, params["premier"])
            b, rb, _ = await cog.service.inspect(actor, params["second"])
            await ctx.member.send(file=discord.File(BytesIO(canonical(compare(a, ra, b, rb)).encode()), filename="comparaison-privee.json"), allowed_mentions=discord.AllowedMentions.none())
        elif name == "build_optimiser":
            if not flag("BUILD_OPTIMIZER_ENABLED"):
                raise BuildError("Optimiseur désactivé.")
            current, _, catalog = await cog.service.inspect(actor, params["build"])
            _, rules = await cog.service.dependencies(current)
            constraints = Constraints(objective=params["objectif"], minimums=values({"pa": params["pa_min"], "pm": params["pm_min"], "po": params["po_min"]}),
                                      locked=tuple(params["verrouilles"]), allow_exos=params["exos_autorises"])
            result = await cog.worker.run(current, catalog, rules, constraints)
            choices = result.get("solutions", []) + result.get("tentative", [])
            view = OptimizationView(cog, actor, result, current) if choices else None
            try:
                message = await ctx.member.send(f"Optimisation : {result['status']}. {result.get('message', '')}", view=view, allowed_mentions=discord.AllowedMentions.none())
                if view:
                    view.message = message
            except discord.HTTPException:
                if view:
                    view.stop()
                raise
        return {"carte_privee_envoyee": True, "contenu_prive_non_expose": True,
                "modification_effectuee": False, "message": "Carte envoyée en MP au demandeur. Les créations/remplacements nécessitent son clic de confirmation. N'invente pas les chiffres du contenu privé."}
    except BuildError as exc:
        raise EvoError(str(exc)) from exc
    except discord.HTTPException as exc:
        raise EvoError("Le message privé n'a pas pu être livré. Utilise /build dans le serveur ; ne publie pas ces données dans le salon.") from exc
