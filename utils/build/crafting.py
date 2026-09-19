"""Je décompose les recettes connues sans confondre matière première et source absente."""
from __future__ import annotations

import asyncio
from collections import Counter
import logging

from .models import BuildError, SLOTS
from utils.dofus_wiki import WikiError

log = logging.getLogger(__name__)


async def shopping_list(build, catalog, wiki, owned_slots=(), *, max_depth=8,
                        max_nodes=1000, max_quantity=1_000_000_000, timeout=45):
    if (type(max_depth) is not int or not 1 <= max_depth <= 20
            or type(max_nodes) is not int or not 1 <= max_nodes <= 10000
            or type(max_quantity) is not int or not 1 <= max_quantity <= 1_000_000_000
            or not 1 <= timeout <= 120 or set(owned_slots) - set(SLOTS)):
        raise BuildError("Limites de décomposition ou emplacements possédés invalides.")
    counts = Counter(row.item.template_ref for row in build.slots if row.slot not in owned_slots)
    entries = None
    index_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(2)
    pending = {}
    totals, missing, sources, problems = {}, set(), set(), []
    traversed = 0

    async def index():
        nonlocal entries
        async with index_lock:
            if entries is None:
                try:
                    entries = {entry.token: entry for entry in await wiki.client.items()}
                except (WikiError, TimeoutError):
                    log.debug("Build recettes: index_unavailable")
                    entries = {}
        return entries

    async def fetch_recipe(ref):
        template = catalog.by_ref.get(ref)
        if template is not None and template.recipe_known:
            return [(row.ref, row.name, row.quantity) for row in template.recipe], template.source
        entry = (await index()).get(ref)
        if entry is None:
            return None, template.source if template else ""
        async with semaphore:
            try:
                async with asyncio.timeout(12):
                    detail = await wiki.client.detail(entry)
                rows = detail.data.get("recipe")
                if not isinstance(rows, list) or len(rows) > 100:
                    return None, entry.url
                parsed = []
                for row in rows:
                    if (not isinstance(row, dict) or type(row.get("qty")) is not int
                            or not 1 <= row["qty"] <= 1_000_000
                            or type(row.get("item_id")) is not int or row["item_id"] <= 0
                            or not isinstance(row.get("name"), str) or not row["name"].strip()
                            or len(row["name"]) > 180):
                        raise ValueError("Recette invalide.")
                    parsed.append((f"item:{row['item_id']}", row["name"], row["qty"]))
                return parsed, entry.url
            except (TimeoutError, ValueError, WikiError):
                log.debug("Build recettes: recipe_unavailable ref=%s", ref)
                return None, entry.url

    async def recipe(ref):
        if ref not in pending:
            if len(pending) >= max_nodes:
                raise BuildError("Nombre maximal de recettes atteint.")
            pending[ref] = asyncio.create_task(fetch_recipe(ref))
        return await pending[ref]

    def terminal(ref, name, quantity, status):
        key = ref or "name:" + name
        row = totals.setdefault(key, {"ref": ref, "name": name, "quantity": 0, "status": status})
        if row["name"] != name or row["status"] != status:
            raise BuildError("Identité ou disponibilité d'ingrédient contradictoire.")
        row["quantity"] += quantity
        if row["quantity"] > max_quantity:
            raise BuildError("Quantité totale trop élevée pour la décomposition.")

    async def visit(ref, name, quantity, chain):
        nonlocal traversed
        traversed += 1
        if traversed > max_nodes or quantity > max_quantity:
            raise BuildError("Volume maximal de décomposition atteint.")
        if ref and ref in chain:
            problems.append({"code": "RECIPE_CYCLE", "ref": ref, "name": name})
            missing.add(name)
            terminal(ref, name, quantity, "recipe_unknown")
            return
        if len(chain) >= max_depth:
            problems.append({"code": "RECIPE_DEPTH", "ref": ref, "name": name})
            missing.add(name)
            terminal(ref, name, quantity, "recipe_unknown")
            return
        rows, source = await recipe(ref)
        if source:
            sources.add(source)
        if rows is None:
            missing.add(name)
            terminal(ref, name, quantity, "recipe_unknown")
        elif not rows:
            terminal(ref, name, quantity, "raw_material")
        else:
            for ingredient_ref, ingredient_name, amount in rows:
                await visit(ingredient_ref, ingredient_name, amount * quantity, chain + (ref,))

    try:
        async with asyncio.timeout(timeout):
            for ref, count in counts.items():
                await visit(ref, catalog.by_ref[ref].name, count, ())
    except TimeoutError as exc:
        raise BuildError("La décomposition a dépassé son délai ; réessaie plus tard.") from exc
    finally:
        for task in pending.values():
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending.values(), return_exceptions=True)
    log.debug("Build recettes: roots=%s nodes=%s leaves=%s missing=%s diagnostics=%s",
              len(counts), traversed, len(totals), len(missing), len(problems))
    return {"ingredients": sorted(totals.values(), key=lambda row: row["name"]),
            "recettes_manquantes": sorted(missing), "sources": sorted(sources),
            "diagnostics": problems, "complete": not missing and not problems,
            "prix": "inconnus", "limite": "Décomposition des recettes connues ; une recette absente reste inconnue. Aucun prix HDV supposé."}
