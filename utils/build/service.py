"""Cas d'usage communs ; autorisation, dépendances figées, prévisualisation, CAS."""
from __future__ import annotations
from collections import OrderedDict
from .models import (Actor, Build, Profile, BuildError, Conflict, Catalog, Report, Frozen,
                     Instance, EffectValue, StatValue, equip, revised, digest, canonical, new_id, now)
from .rules import Rules
from .catalog import verify_snapshot, search
from .calculator import calculate
from .repository import require_owner
from .import_export import export_build, import_build


class Preview(Frozen):
    candidate: Build
    report: Report
    expected: int | None
    request_hash: str


class BuildService:
    def __init__(self, repository, catalogs, rules):
        self.repository, self.catalogs, self.rules = repository, catalogs, rules
        self.cache = OrderedDict()

    async def start(self):
        await self.repository.put_snapshot("rules", self.rules.id, canonical(self.rules))

    async def dependencies(self, build):
        key = build.catalog_id, build.rules_id
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if self.catalogs.latest is not None and self.catalogs.latest.id == build.catalog_id:
            catalog = self.catalogs.latest
        else:
            catalog = Catalog.model_validate_json(await self.repository.snapshot("catalog", build.catalog_id))
            verify_snapshot(catalog)
        rules = self.rules if self.rules.id == build.rules_id else Rules.model_validate_json(await self.repository.snapshot("rules", build.rules_id))
        if rules.id != digest({k: v for k, v in rules.model_dump(mode="json").items() if k != "id"}):
            raise BuildError("Empreinte du profil de règles incohérente.")
        self.cache[key] = (catalog, rules)
        while len(self.cache) > 3:
            self.cache.popitem(last=False)
        return catalog, rules

    async def inspect(self, actor, build_id):
        build = await self.repository.get(actor, build_id)
        catalog, rules = await self.dependencies(build)
        return build, calculate(build, catalog, rules), catalog

    async def preview(self, actor, candidate, request):
        require_owner(candidate, actor)
        catalog, rules = await self.dependencies(candidate)
        report = calculate(candidate, catalog, rules)
        return Preview(candidate=candidate, report=report, expected=candidate.revision or None,
                       request_hash=digest(request))

    async def apply(self, actor, preview, operation):
        require_owner(preview.candidate, actor)
        catalog, rules = await self.dependencies(preview.candidate)
        report = calculate(preview.candidate, catalog, rules)
        if report != preview.report:
            raise BuildError("La prévisualisation ne correspond plus aux calculs.")
        saved = await self.repository.commit(actor, preview.candidate, preview.expected, operation, preview.request_hash, digest(report))
        return saved, calculate(saved, catalog, rules), catalog

    async def new(self, actor, name, profile, *, operation=None):
        request = {"action": "create", "name": name, "profile": profile.model_dump(mode="json")}
        if operation:
            previous = await self.repository.replay(actor, operation, digest(request))
            if previous:
                build = Build.model_validate(previous["build"])
                catalog, rules = await self.dependencies(build)
                return build, calculate(build, catalog, rules), catalog
        catalog = self.catalogs.latest
        if catalog is None:
            raise BuildError("Catalogue pas encore prêt. Le bot retente son chargement automatiquement ; le Staff peut consulter /build diagnostic ou relancer /build actualiser.")
        build = Build(guild_id=actor.guild_id, owner_id=actor.user_id, name=name, catalog_id=catalog.id, rules_id=self.rules.id, profile=profile)
        preview = await self.preview(actor, build, request)
        return await self.apply(actor, preview, operation) if operation else preview

    async def equipment(self, actor, build_id, expected, slot, reference):
        build, _, catalog = await self.inspect(actor, build_id)
        if build.revision != expected:
            raise Conflict("Le build a changé : rouvre l'éditeur.")
        item = catalog.by_ref.get(reference)
        if item is None:
            from .conditions import norm
            matches = [i for i in catalog.items if norm(i.name) == norm(reference)]
            if len(matches) != 1:
                raise BuildError("Choisis une référence dans la recherche : nom absent ou ambigu.")
            item = matches[0]
        candidate = build.with_slot(slot, equip(item))
        return await self.preview(actor, candidate, {"action": "equip", "id": build.id, "revision": expected, "slot": slot, "ref": item.ref})

    async def remove(self, actor, build_id, expected, slot):
        build, _, _ = await self.inspect(actor, build_id)
        if build.revision != expected:
            raise Conflict("Le build a changé.")
        return await self.preview(actor, build.with_slot(slot, None), {"action": "remove", "id": build_id, "revision": expected, "slot": slot})

    async def profile(self, actor, build_id, expected, profile):
        build, _, _ = await self.inspect(actor, build_id)
        if build.revision != expected:
            raise Conflict("Le build a changé.")
        return await self.preview(actor, revised(build, profile=profile), {"action": "profile", "id": build_id, "revision": expected, "profile": profile.model_dump(mode="json")})

    async def rename(self, actor, build_id, expected, name):
        build, _, _ = await self.inspect(actor, build_id)
        if build.revision != expected:
            raise Conflict("Le build a changé.")
        return await self.preview(actor, revised(build, name=name), {"action": "rename", "id": build_id, "revision": expected, "name": name})

    async def jets(self, actor, build_id, expected, slot, mode, final_values, extras):
        build, _, _ = await self.inspect(actor, build_id)
        if build.revision != expected:
            raise Conflict("Le build a changé.")
        old = build.in_slot(slot)
        if old is None:
            raise BuildError("Cet emplacement est vide.")
        instance = revised(old, mode=mode, final_values=final_values, extras=extras)
        return await self.instance(actor, build, slot, instance, "jets")

    async def instance(self, actor, build, slot, instance, action="instance"):
        request = {"action": action, "id": build.id, "revision": build.revision, "slot": slot, "instance": instance.model_dump(mode="json")}
        return await self.preview(actor, build.with_slot(slot, instance), request)

    async def copy(self, actor, code):
        source = await self.repository.shared(actor, code)
        raw = export_build(source)
        candidate = import_build(raw, actor)
        return await self.preview(actor, candidate, {"action": "copy", "code": code, "source_revision": source.revision})

    async def importing(self, actor, raw, *, use_current=False):
        candidate = import_build(raw, actor)
        if use_current:
            catalog = self.catalogs.latest
            if catalog is None:
                raise BuildError("Catalogue actuel indisponible.")
            # Option explicite pour réimporter un export du mode mémoire après
            # redémarrage. Aucune donnée de catalogue du fichier n'est acceptée.
            # Les révisions exactes des objets doivent encore correspondre.
            for row in candidate.slots:
                catalog.resolve(row.item)
            candidate = revised(candidate, catalog_id=catalog.id)
        return await self.preview(actor, candidate, {"action": "import_current_catalog" if use_current else "import",
                             "target_catalog": candidate.catalog_id, "content_hash": digest(raw.hex())})

    async def delete(self, actor, build_id, expected, operation):
        request = {"action": "delete", "id": build_id, "revision": expected}
        await self.repository.delete(actor, build_id, expected, operation, digest(request))

    async def share(self, actor, build, operation):
        require_owner(build, actor)
        return await self.repository.share(actor, build, operation, digest({"action": "share", "id": build.id, "revision": build.revision}))

    async def migrate(self, actor, build_id):
        build, _, old = await self.inspect(actor, build_id)
        latest = self.catalogs.latest
        if latest is None:
            raise BuildError("Aucun catalogue récent disponible.")
        candidate = revised(build, catalog_id=latest.id, rules_id=self.rules.id)
        for row in build.slots:
            template = latest.by_ref.get(row.item.template_ref)
            if template is None:
                raise BuildError("Un objet de ce build est absent du nouveau catalogue ; migration refusée.")
            instance = revised(row.item, template_revision=template.revision)
            if row.item.final_values:
                previous = old.resolve(row.item)
                refs = {e.ref: e for e in previous.effects}
                remapped = []
                for value in row.item.final_values:
                    e = refs[value.ref]
                    matches = [target for target in template.effects if (target.kind, target.stat) == (e.kind, e.stat)]
                    if len(matches) != 1:
                        raise BuildError("Correspondance des jets personnalisés ambiguë ; migration manuelle nécessaire.")
                    remapped.append({"ref": matches[0].ref, "value": value.value})
                instance = revised(instance, final_values=remapped)
            candidate = candidate.with_slot(row.slot, instance)
        return await self.preview(actor, candidate, {"action": "migrate", "id": build.id, "revision": build.revision, "target": latest.id, "rules": self.rules.id})

    async def restore_revision(self, actor, build_id, expected, revision):
        """Restaurer une copie dans une NOUVELLE révision, sans effacer l'historique."""
        current = await self.repository.get(actor, build_id)
        if current.revision != expected:
            raise Conflict("Le build a changé : rouvre son historique.")
        versions = await self.repository.revisions(actor, build_id)
        previous = next((b for b in versions if b.revision == revision), None)
        if previous is None:
            raise BuildError("Cette révision n'est plus conservée.")
        candidate = revised(previous, revision=current.revision,
                            created_at=current.created_at, updated_at=current.updated_at)
        return await self.preview(actor, candidate, {"action": "restore_revision",
            "id": build_id, "revision": expected, "source_revision": revision})
