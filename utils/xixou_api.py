"""Je complète les objets avec les catalogues Xixou sans confondre leurs identités."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import json
import logging
import math
import re
import time
import unicodedata

import aiohttp

from utils.dofus_wiki import WikiDetail, clean_text, retry_after_delay
from utils.xixou_image_paths import xixou_image_candidates


log = logging.getLogger(__name__)
XIXOU_ORIGIN = "https://xixou.io"
FAMILIES = ("equipements", "ressources", "monstres", "carte")
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_STALE_SECONDS = 86400
_VERIFIED_PACKET_NAME_ALIASES = {
    (14050, "paquet de cartes : communes"): "paquet de cartes communes",
    (14051, "paquet de cartes : rares"): "paquet de cartes rares",
    (14052, "paquet de cartes : epiques"): "paquet de cartes epiques",
}


@dataclass(frozen=True)
class DropSource:
    name: str
    rate: str = ""
    level_rates: tuple[tuple[str, str], ...] = ()
    pp: str = ""
    maximum: str = ""
    zones: tuple[str, ...] = ()


@dataclass(frozen=True)
class Zone:
    name: str
    cells: tuple[tuple[float, float], ...] = ()
    polygon: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Harvest:
    name: str
    job: str
    level: str = ""
    positions: tuple[tuple[float, float, int | None], ...] = ()


@dataclass(frozen=True)
class ItemEnrichment:
    description: str = ""
    level: int | None = None
    weight: int | None = None
    effects: tuple[str, ...] = ()
    details: tuple[tuple[str, tuple[str, ...]], ...] = ()
    drops: tuple[DropSource, ...] = ()
    zones: tuple[Zone, ...] = ()
    harvests: tuple[Harvest, ...] = ()
    uses: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    stale: bool = False
    image_candidates: tuple[str, ...] = ()


def identity_key(value: object) -> str:
    """Je normalise la typographie en conservant étoiles et autres qualificatifs."""
    text = unicodedata.normalize("NFKD", clean_text(value).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.replace("’", "'").replace("œ", "oe").split())


def _category(value: object) -> str:
    key = " ".join(re.sub(r"[-_']+", " ", identity_key(value)).split())
    aliases = {
        "anneaux": "anneau", "chapeaux": "chapeau", "coiffe": "chapeau",
        "coiffes": "chapeau", "sac a dos": "sac", "sacs a dos": "sac",
        "sacs": "sac", "pierre d ame": "pierre d ame", "pierres": "pierre d ame",
        "pierre d ame vide": "pierre d ame", "clefs": "clef", "cle": "clef",
        "cles": "clef", "yeux": "oeil", "marteaux": "marteau", "cadeaux": "cadeau",
        "materiel d alchimie": "materiel alchimie",
        "ecaille de dragon": "ecailles dragon 133", "ecaille dragon": "ecailles dragon 133",
    }
    key = aliases.get(key, key)
    key = " ".join(word[:-1] if word.endswith("s") and word not in {
        "bois", "os", "dofus", "dos", "bonus",
    } else word for word in key.split() if word not in {"de", "d", "du", "des"})
    return {
        "objet quete": "objet mission", "carte commune": "carte ttg",
        "carte rare": "carte ttg", "carte epique": "carte ttg", "carte unique": "carte ttg",
        "certificat mise en chanil": "certificat chenil",
        "certificat mise en chenil": "certificat chenil",
        "pierre ame gardien donjon": "pierre ame pleine",
        "pierre ame archi monstre": "pierre ame pleine",
    }.get(key, key)


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value)
    if not re.fullmatch(r"\d{1,9}", text):
        return None
    return int(text)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(clean_text(row) for row in value
                               if isinstance(row, (str, int, float)) and clean_text(row)))


def _value(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _count(value: object, *, unlimited=False) -> str:
    if unlimited and value == "∞":
        return "∞"
    number = _integer(value)
    return str(number) if number is not None else ""


def percent(value: object) -> str:
    """Je conserve la précision des taux de base, y compris zéro et les taux rares."""
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip().removesuffix("%").strip().replace(",", ".")
    if len(text) > 40:
        return ""
    try:
        number = Decimal(text)
    except InvalidOperation:
        return ""
    if not number.is_finite() or not 0 <= number <= 100:
        return ""
    if number == 0:
        return "0%"
    if number and number.adjusted() < -18:
        return ""
    return (format(number, "f").rstrip("0").rstrip(".")
            if "." in format(number, "f") else format(number, "f")) + "%"


def _records(catalog: dict | None):
    data = (catalog or {}).get("data", {})
    if not isinstance(data, dict):
        return
    for category, rows in data.items():
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and clean_text(row.get("name")):
                    yield category, row


def _match_item(detail: WikiDetail, catalogs: dict[str, dict]):
    name = identity_key(detail.entry.name)
    category = _category(detail.entry.category)
    level = _integer(detail.data.get("level", detail.entry.level))
    identifier = _integer(detail.entry.identifier)
    packet_alias = (_VERIFIED_PACKET_NAME_ALIASES.get((identifier, name))
                    if category == "paquet carte" and level == 1 else None)
    candidates = []
    for family in ("equipements", "ressources"):
        for row_category, row in _records(catalogs.get(family)):
            row_name = identity_key(row.get("name"))
            verified_packet_alias = (
                packet_alias is not None and row_name == packet_alias and family == "ressources"
                and row_category == "paquet-de-cartes" and _integer(row.get("level")) == 1
            )
            if row_name != name and not verified_packet_alias:
                continue
            row_id = _integer(row.get("id"))
            row_level = _integer(row.get("level"))
            if _value(row, "id") is not None and row_id is None:
                continue
            if row_id is not None and identifier is not None and row_id != identifier:
                continue
            if row_level is not None and level is not None and row_level != level:
                continue
            verified_id = row_id is not None and identifier is not None and row_id == identifier
            accepted_categories = {_category(row_category), _category(row.get("category", ""))}
            generic_categories = {"", "objet", "ressource", "equipement"}
            if not verified_id and category not in generic_categories | accepted_categories:
                continue
            candidates.append((family, row, row_category))
    id_matches = [candidate for candidate in candidates
                  if identifier is not None and _integer(candidate[1].get("id")) == identifier]
    if id_matches:
        candidates = id_matches
    unique = {}
    for family, row, row_category in candidates:
        key = (family, json.dumps(row, sort_keys=True, ensure_ascii=True))
        record = unique.setdefault(key, (family, row, []))
        record[2].append(row_category)
    if len(unique) != 1:
        log.debug("Xixou: item_match candidates=%s item_id=%s", len(unique), detail.entry.identifier)
        return None
    return next(iter(unique.values()))


def _monster_primary(monster: dict) -> str:
    return clean_text(monster.get("name")).split(" - ", 1)[0]


def _monster_drop(monster: dict, item_name: str, category: str, family: str) -> dict | None:
    rows = monster.get("drops")
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict)
               and identity_key(row.get("name")) == item_name
               and {"equipement": "equipements", "arme": "equipements",
                    "ressource": "ressources"}.get(identity_key(row.get("type")), family) == family
               and (not row.get("categorie") or _category(row["categorie"]) == category)]
    return matches[0] if len(matches) == 1 else None


def _link_monster(drop: dict, monsters: list[dict], name: str, category: str, family: str):
    monster_id = _integer(drop.get("monster_id"))
    if _value(drop, "monster_id") is not None and monster_id is None:
        return None, None
    drop_name = identity_key(_value(drop, "monster_name", "monster", "name"))
    candidates = []
    for monster in monsters:
        full_name = identity_key(monster.get("name"))
        primary_name = identity_key(_monster_primary(monster))
        if drop_name not in {full_name, primary_name}:
            continue
        if monster_id is not None and _integer(monster.get("id")) != monster_id:
            continue
        matched_drop = _monster_drop(monster, name, category, family)
        if matched_drop is not None:
            candidates.append((monster, matched_drop))
    return candidates[0] if len(candidates) == 1 else (None, None)


def _source(drop: dict, monster: dict | None = None, monster_drop: dict | None = None):
    name = clean_text(_value(drop, "monster_name", "monster", "name"))
    rate = percent(_value(drop, "drop_rate_percent", "rate_percent", "dropRate", "rate", "taux"))
    pp = _count(drop.get("pp"))
    maximum = _count(_value(drop, "drop_max", "max"), unlimited=True)
    pairs = []
    zones = ()
    if monster is not None and monster_drop is not None:
        name = _monster_primary(monster)
        zones = _strings(monster.get("zones"))
        stats = monster.get("stats", {})
        levels = stats.get("niveau", {}) if isinstance(stats, dict) else {}
        ranks = levels.get("ranks", {}) if isinstance(levels, dict) else {}
        rates = monster_drop.get("taux_ranks", [])
        inactive = monster.get("ranks_inactifs", [])
        suspect = monster.get("ranks_suspects", [])
        active_rates = []
        if isinstance(rates, list) and isinstance(ranks, dict):
            for number, candidate in enumerate(rates, 1):
                if isinstance(inactive, list) and number in inactive:
                    continue
                level = _integer(ranks.get(f"rank_{number}"))
                level_rate = percent(candidate)
                if level_rate:
                    active_rates.append(Decimal(level_rate[:-1]))
                if level is not None and level_rate:
                    suffix = " (à vérifier)" if isinstance(suspect, list) and number in suspect else ""
                    pairs.append((f"{level}{suffix}", level_rate))
        if active_rates:
            values = sorted(set(active_rates))
            rate = percent(values[0])
            if len(values) > 1:
                rate += " – " + percent(values[-1])
        elif isinstance(rates, list) and rates:
            rate = ""
        else:
            rate = percent(monster_drop.get("taux")) or rate
        if _value(monster_drop, "pp") is not None:
            pp = _count(monster_drop["pp"])
        if _value(monster_drop, "max") is not None:
            maximum = _count(monster_drop["max"], unlimited=True)
    return DropSource(name, rate, tuple(pairs), pp, maximum, zones)


def _points(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list):
        return ()
    points = []
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        if any(isinstance(coord, bool) or not isinstance(coord, (float, int))
               or not math.isfinite(coord) or abs(coord) > 1000 for coord in pair[:2]):
            continue
        points.append((float(pair[0]), float(pair[1])))
    return tuple(dict.fromkeys(points))


def _zones(names: tuple[str, ...], carte: dict) -> tuple[Zone, ...]:
    rows = carte.get("zones", [])
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    result = []
    for name in names:
        matches = [row for row in rows if identity_key(row.get("name")) == identity_key(name)]
        if len(matches) == 1:
            row = matches[0]
            result.append(Zone(clean_text(row["name"]), _points(row.get("cells")),
                               _points(row.get("polygon"))))
        else:
            result.append(Zone(name))
    return tuple(result)


HARVEST_JOBS = {
    "bucheron": ("Bûcheron", {"bois"}), "mineur": ("Mineur", {"minerai"}),
    "alchimiste": ("Alchimiste", {"plante", "fleur"}),
    "paysan": ("Paysan", {"cereale", "plante"}),
}


def _harvests(name: str, category: str, carte: dict) -> tuple[Harvest, ...]:
    resources = carte.get("resources", {})
    if not isinstance(resources, dict):
        return ()
    found = []
    for job, (label, categories) in HARVEST_JOBS.items():
        if category not in categories:
            continue
        rows = resources.get(job, [])
        if not isinstance(rows, list):
            continue
        target = re.sub(r"^bois (?:de |d')", "", name) if job == "bucheron" else name
        matches = [row for row in rows if isinstance(row, dict)
                   and identity_key(row.get("name")) == target]
        if len(matches) != 1:
            continue
        row = matches[0]
        positions = []
        raw_positions = row.get("positions", [])
        if isinstance(raw_positions, list):
            for point in raw_positions:
                coords = _points([point])
                if coords:
                    count = _integer(point[2]) if len(point) > 2 else None
                    positions.append((*coords[0], count))
        found.append(Harvest(clean_text(row.get("name")), label,
                             str(row["level"]) if _integer(row.get("level")) is not None else "",
                             tuple(dict.fromkeys(positions))))
    return tuple(found)


def build_enrichment(detail: WikiDetail, catalogs: dict[str, dict], *, stale=False):
    """Je rapproche une fiche uniquement avec une source identifiée sans ambiguïté."""
    if detail.entry.kind != "item":
        return None
    matched = _match_item(detail, catalogs)
    if matched is None:
        return None
    family, row, source_categories = matched
    image_options = {xixou_image_candidates(family, source_category, row.get("image"))
                     for source_category in source_categories}
    image_candidates = next(iter(image_options)) if len(image_options) == 1 else ()
    category = _category(detail.entry.category)
    name = identity_key(row.get("name"))
    monsters = catalogs.get("monstres", {}).get("data", [])
    monsters = [row for row in monsters if isinstance(row, dict)] if isinstance(monsters, list) else []
    carte = catalogs.get("carte", {}).get("data", {})
    carte = carte if isinstance(carte, dict) else {}
    raw_drops = row.get("drops", [])
    drops = []
    linked_ids = set()
    if isinstance(raw_drops, list):
        for raw in raw_drops:
            if not isinstance(raw, dict) or not _value(raw, "monster_name", "monster", "name"):
                continue
            monster, monster_drop = _link_monster(raw, monsters, name, category, family)
            drops.append(_source(raw, monster, monster_drop))
            if monster is not None:
                linked_ids.add(_integer(monster.get("id")))
    all_items = [(selected_family, candidate_category, candidate)
                 for selected_family in ("equipements", "ressources")
                 for candidate_category, candidate in _records(catalogs.get(selected_family))
                 if identity_key(candidate.get("name")) == name]
    identities = {(selected_family, _integer(candidate.get("id")),
                   _integer(candidate.get("level")), _category(candidate_category))
                  for selected_family, candidate_category, candidate in all_items}
    if len(identities) == 1:
        existing_names = {identity_key(existing.name.split(" - ", 1)[0]) for existing in drops}
        for monster in monsters:
            if _integer(monster.get("id")) in linked_ids:
                continue
            monster_drop = _monster_drop(monster, name, category, family)
            if monster_drop is not None:
                source = _source({"name": _monster_primary(monster)}, monster, monster_drop)
                if identity_key(source.name) not in existing_names:
                    drops.append(source)
    drops = tuple(dict.fromkeys(drops))
    zone_names = tuple(dict.fromkeys(zone for drop in drops for zone in drop.zones))
    extra = []
    for label, field in (("Conditions", "conditions"), ("Arme", "arme_stats"),
                         ("Panoplie", "panoplie")):
        values = _strings(row.get(field))
        if values:
            extra.append((label, values))
    uses = []
    for field in ("crafts", "craft_de"):
        crafts = row.get(field, [])
        if isinstance(crafts, list):
            uses.extend(clean_text(_value(craft, "item_name", "name")) for craft in crafts
                        if isinstance(craft, dict) and _value(craft, "item_name", "name"))
    dates = tuple(sorted({clean_text(catalog.get("genere_le")) for key, catalog in catalogs.items()
                          if key in {family, "monstres", "carte"} and catalog.get("genere_le")}))
    result = ItemEnrichment(
        description=clean_text(row.get("description")), level=_integer(row.get("level")),
        weight=_integer(row.get("pods")), effects=_strings(_value(row, "effets", "effects")),
        details=tuple(extra), drops=drops, zones=_zones(zone_names, carte),
        harvests=_harvests(name, category, carte), uses=tuple(dict.fromkeys(uses)),
        dates=dates, stale=stale,
        image_candidates=image_candidates,
    )
    log.debug("Xixou: enriched item_id=%s drops=%s zones=%s harvests=%s stale=%s",
              detail.entry.identifier, len(drops), len(result.zones), len(result.harvests), stale)
    return result


class XixouError(Exception):
    """Je distingue une réponse inexploitable d'une erreur de programmation."""


@dataclass(frozen=True)
class CatalogCache:
    value: dict
    fetched_at: float
    etag: str = ""


def validate_catalog(family: str, value: object) -> dict:
    if not isinstance(value, dict) or "data" not in value:
        raise XixouError("Catalogue absent")
    data = value["data"]
    if family == "monstres":
        valid = isinstance(data, list) and bool(data) and all(isinstance(row, dict) for row in data)
    elif family == "carte":
        valid = (isinstance(data, dict) and isinstance(data.get("zones"), list)
                 and isinstance(data.get("resources"), dict))
    else:
        valid = (isinstance(data, dict) and bool(data)
                 and all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
                         for rows in data.values()) and any(data.values()))
    if not valid:
        raise XixouError("Structure du catalogue invalide")
    if value.get("famille") is not None and value.get("famille") != family:
        raise XixouError("Famille du catalogue incohérente")
    return value


class XixouClient:
    """Je partage les catalogues et je conserve un secours temporaire sans écrire de secrets."""

    def __init__(self, api_key: str = "", *, ttl: float = 3600, timeout: float = 10,
                 session: aiohttp.ClientSession | None = None, clock=time.monotonic):
        self._api_key = api_key.strip()
        self.ttl = max(60, min(float(ttl), MAX_STALE_SECONDS))
        self.timeout = max(1, min(float(timeout), 30))
        self.clock = clock
        self._session = session
        self._owns_session = session is None
        self._cache: dict[str, CatalogCache] = {}
        self._inflight: dict[str, asyncio.Task] = {}
        self._retry_at: dict[str, float] = {}
        self._backoff_until = 0.0
        self._semaphore = asyncio.Semaphore(2)
        self._closed = False
        self._enrichment_snapshot: tuple[tuple[str, dict], ...] = ()
        self._enrichment_cache: OrderedDict[tuple, ItemEnrichment | None] = OrderedDict()
        self._enrichment_inflight: dict[tuple, asyncio.Task] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._api_key) and not self._closed

    def _peek(self, family: str):
        cache = self._cache.get(family)
        if cache and self.clock() - cache.fetched_at <= MAX_STALE_SECONDS:
            return cache
        return None

    async def _fetch(self, family: str) -> CatalogCache:
        if family not in FAMILIES or not self.enabled:
            raise XixouError("Client indisponible")
        async with self._semaphore, asyncio.timeout(self.timeout):
            if self._backoff_until > self.clock():
                raise XixouError("Délai de reprise actif")
            if self._session is None:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    cookie_jar=aiohttp.DummyCookieJar(),
                    headers={"User-Agent": "EvolutionBOT/1.0 (Dofus Retro encyclopedia)"},
                )
            cache = self._cache.get(family)
            headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
            if cache and cache.etag:
                headers["If-None-Match"] = cache.etag
            log.debug("Xixou: request family=%s conditional=%s", family, "If-None-Match" in headers)
            async with self._session.get(
                f"{XIXOU_ORIGIN}/api/v1/{family}.json", headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status == 304 and cache is not None:
                    return CatalogCache(cache.value, self.clock(), cache.etag)
                if response.status != 200:
                    log.debug("Xixou: http_error family=%s status=%s", family, response.status)
                    if response.status == 429:
                        delay = retry_after_delay(response.headers.get("Retry-After", "60"))
                        self._backoff_until = max(self._backoff_until, self.clock() + delay)
                    elif response.status in {401, 403}:
                        self._backoff_until = max(self._backoff_until, self.clock() + 300)
                    raise XixouError("Réponse HTTP indisponible")
                if response.content_type != "application/json":
                    raise XixouError("Type de contenu inattendu")
                if response.content_length and response.content_length > MAX_CATALOG_BYTES:
                    raise XixouError("Catalogue trop volumineux")
                payload = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    payload.extend(chunk)
                    if len(payload) > MAX_CATALOG_BYTES:
                        raise XixouError("Catalogue trop volumineux")
                try:
                    value = json.loads(payload.decode("utf-8-sig"))
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise XixouError("Catalogue illisible") from exc
                value = validate_catalog(family, value)
                return CatalogCache(value, self.clock(), response.headers.get("ETag", ""))

    async def _refresh(self, family: str):
        try:
            cache = await self._fetch(family)
            self._cache[family] = cache
            self._retry_at.pop(family, None)
            log.debug("Xixou: cache_refreshed family=%s", family)
            return cache.value
        except (aiohttp.ClientError, TimeoutError, XixouError) as exc:
            self._retry_at[family] = max(self.clock() + 30, self._backoff_until)
            cache = self._peek(family)
            log.debug("Xixou: unavailable family=%s reason=%s fallback=%s",
                      family, type(exc).__name__, cache is not None)
            return cache.value if cache is not None else None

    async def catalog(self, family: str) -> dict | None:
        if family not in FAMILIES:
            raise ValueError("Famille Xixou inconnue")
        if not self.enabled:
            return None
        cache = self._peek(family)
        if cache and self.clock() - cache.fetched_at < self.ttl:
            return cache.value
        if max(self._retry_at.get(family, 0), self._backoff_until) > self.clock():
            return cache.value if cache else None
        task = self._inflight.get(family)
        if task is None:
            task = asyncio.create_task(self._refresh(family))
            self._inflight[family] = task

            def finished(completed):
                self._inflight.pop(family, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def warmup(self):
        if self.enabled:
            await asyncio.gather(*(self.catalog(family) for family in FAMILIES))

    def _snapshot_key(self):
        return tuple((family, id(value)) for family, value in self._enrichment_snapshot)

    async def _compute_enrichment(self, detail: WikiDetail, catalogs: dict, snapshot: tuple, key):
        result = await asyncio.to_thread(build_enrichment, detail, catalogs)
        if self._closed:
            return None
        if self._snapshot_key() == snapshot:
            self._enrichment_cache[key] = result
            self._enrichment_cache.move_to_end(key)
            while len(self._enrichment_cache) > 128:
                self._enrichment_cache.popitem(last=False)
        return result

    async def enrich(self, detail: WikiDetail) -> ItemEnrichment | None:
        if not self.enabled or detail.entry.kind != "item":
            return None
        values = await asyncio.gather(*(self.catalog(family) for family in FAMILIES))
        if self._closed:
            return None
        catalogs = {family: value for family, value in zip(FAMILIES, values) if value is not None}
        stale = any(self.clock() - self._cache[family].fetched_at >= self.ttl for family in catalogs)
        snapshot = tuple((family, id(value)) for family, value in catalogs.items())
        if snapshot != self._snapshot_key():
            self._enrichment_snapshot = tuple(catalogs.items())
            self._enrichment_cache.clear()
        key = (detail.entry, _integer(detail.data.get("level", detail.entry.level)))
        if key in self._enrichment_cache:
            result = self._enrichment_cache[key]
            self._enrichment_cache.move_to_end(key)
            log.debug("Xixou: enrichment_cache_hit item_id=%s", detail.entry.identifier)
        else:
            task_key = (snapshot, key)
            task = self._enrichment_inflight.get(task_key)
            if task is None:
                task = asyncio.create_task(self._compute_enrichment(detail, catalogs, snapshot, key))
                self._enrichment_inflight[task_key] = task

                def finished(completed):
                    self._enrichment_inflight.pop(task_key, None)
                    if not completed.cancelled():
                        completed.exception()

                task.add_done_callback(finished)
            result = await asyncio.shield(task)
        if self._closed:
            return None
        return replace(result, stale=stale) if result is not None else None

    async def close(self):
        self._closed = True
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        computations = list(self._enrichment_inflight.values())
        if computations:
            await asyncio.gather(*computations, return_exceptions=True)
        if self._session is not None and self._owns_session:
            await self._session.close()
        self._cache.clear()
        self._enrichment_cache.clear()
        self._enrichment_snapshot = ()
        log.debug("Xixou: client_closed")
