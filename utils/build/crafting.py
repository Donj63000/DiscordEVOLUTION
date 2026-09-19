"""Agrégation des recettes du wiki existant, bornée à 16 objets, sans prix inventés."""
import asyncio
from collections import Counter
from .models import BuildError
from utils.dofus_wiki import WikiError


async def shopping_list(build, catalog, wiki, owned_slots=()):
    counts = Counter(r.item.template_ref for r in build.slots if r.slot not in owned_slots)
    entries = {}
    if any(not catalog.by_ref[ref].recipe_known for ref in counts):
        try:
            entries = {e.token: e for e in await wiki.client.items()}
        except WikiError:
            pass  # Les recettes non résolues seront explicitement marquées manquantes.
    semaphore = asyncio.Semaphore(2)
    async def recipe(ref, quantity):
        template = catalog.by_ref[ref]
        if template.recipe_known:
            return ref, quantity, [(r.ref, r.name, r.quantity) for r in template.recipe], template.source
        entry = entries.get(ref)
        if entry is None:
            return ref, quantity, None, template.source
        async with semaphore:
            try:
                async with asyncio.timeout(12):
                    detail = await wiki.client.detail(entry)
                rows = detail.data.get("recipe")
                if not isinstance(rows, list) or len(rows) > 100 or not rows:
                    return ref, quantity, None, entry.url
                parsed = []
                for r in rows:
                    if not isinstance(r, dict) or type(r.get("qty")) is not int or not 1 <= r["qty"] <= 1000000 or type(r.get("item_id")) is not int or r["item_id"] <= 0 or not isinstance(r.get("name"), str):
                        raise ValueError("Recette invalide.")
                    parsed.append((f"item:{r['item_id']}", r["name"][:180], r["qty"]))
                return ref, quantity, parsed, entry.url
            except (TimeoutError, ValueError, WikiError):
                return ref, quantity, None, entry.url
    recipes = await asyncio.gather(*(recipe(ref, n) for ref, n in counts.items()))
    totals, missing, sources = {}, [], set()
    for ref, quantity, rows, source in recipes:
        sources.add(source)
        if rows is None:
            missing.append(catalog.by_ref[ref].name)
            continue
        for ingredient, name, amount in rows:
            key = ingredient or "name:" + name
            item = totals.setdefault(key, {"ref": ingredient, "name": name, "quantity": 0})
            if item["name"] != name:
                raise BuildError("Identité d'ingrédient contradictoire ; agrégation refusée.")
            item["quantity"] += amount * quantity
    return {"ingredients": sorted(totals.values(), key=lambda r: r["name"]), "recettes_manquantes": missing,
            "sources": sorted(sources), "prix": "inconnus", "limite": "Recettes actuelles du wiki, pas inventaire possédé ni coût HDV. Pas de décomposition récursive."}
