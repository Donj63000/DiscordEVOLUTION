import asyncio
from email.utils import formatdate
from unittest.mock import AsyncMock

import aiohttp
from aioresponses import CallbackResult, aioresponses
import pytest
import pytest_asyncio

from utils.dofus_wiki import (
    CacheEntry, DofusWikiClient, INDEX_PATHS, WIKI_ORIGIN, WikiError,
    equipment_category, equipment_suggestions, find_entries, parse_entries, parse_icons,
    retry_after_delay, search_key,
)


ITEMS = [
    {"id": 2469, "name": "Gelano", "type": "Anneau", "level": 60,
     "url": WIKI_ORIGIN + "/items/gelano/"},
    {"id": 2411, "name": "Coiffe du Bouftou", "type": "Chapeau", "level": 10,
     "url": WIKI_ORIGIN + "/items/coiffe-du-bouftou/"},
]
MONSTERS = [
    {"id": 1, "name": "Craqueleur", "level_min": "1 à 6",
     "url": WIKI_ORIGIN + "/monstres/craqueleur/"},
    {"id": 1, "name": "Craqueleur", "level_min": "25 à 37",
     "url": WIKI_ORIGIN + "/monstres/craqueleur-106/"},
]


@pytest_asyncio.fixture
async def client():
    instance = DofusWikiClient(ttl=60)
    yield instance
    await instance.close()


@pytest.mark.parametrize("value,expected", [
    ("Épée de Boisaille", "epee de boisaille"),
    ("GELÉE", "gelee"),
    ("D\\'Épée", "d epee"),
    ("Bâton  du   Bouftou", "baton du bouftou"),
])
def test_search_normalizes_accents_apostrophes_and_spacing(value, expected):
    assert search_key(value) == expected


def test_monsters_keep_distinct_fiches_despite_duplicate_source_ids():
    entries = parse_entries(MONSTERS, "monster")
    assert len(entries) == 2
    assert entries[0].token != entries[1].token
    matches, fuzzy = find_entries(entries, "craqueleur")
    assert matches == list(entries)
    assert not fuzzy
    assert find_entries(entries, "monster:craqueleur-106")[0] == [entries[1]]


@pytest.mark.parametrize("query", ["", "épée", "épée de test"])
def test_search_keeps_every_match_while_autocomplete_can_limit_results(query):
    entries = parse_entries([
        {"id": index + 1, "name": "Épée de test", "type": "Epée", "level": 10,
         "url": WIKI_ORIGIN + f"/items/epee-{index}/"}
        for index in range(150)
    ], "item")
    matches, fuzzy = find_entries(entries, query)
    assert len(matches) == 150
    assert not fuzzy
    assert len(find_entries(entries, query, 25)[0]) == 25
    suggestions, fuzzy = find_entries(entries, "epew de test")
    assert len(suggestions) == 25
    assert fuzzy


@pytest.mark.parametrize("name", ["Craqueleur", "Monstre " + "a" * 200])
def test_monster_variants_have_distinct_bounded_labels_stable_across_catalogue_order(name):
    rows = [{**row, "name": name, "level_min": "1 à 6"} for row in MONSTERS]
    forward = parse_entries(rows, "monster")
    backward = parse_entries(list(reversed(rows)), "monster")
    assert {entry.token: entry.label for entry in forward} == {
        entry.token: entry.label for entry in backward
    }
    assert len({entry.label for entry in forward}) == 2
    assert all(len(entry.label) <= 100 for entry in forward)
    assert {entry.label.split(" · ")[-1] for entry in forward} == {
        "Variante 1/2", "Variante 2/2",
    }
    assert all(find_entries(forward, entry.token)[0] == [entry] for entry in forward)


def test_distinct_monster_levels_do_not_receive_unnecessary_variant_labels():
    entries = parse_entries(MONSTERS, "monster")
    assert all("Variante" not in entry.label for entry in entries)


@pytest.mark.parametrize("data", [None, {}, [], [None], [{"name": "Fake", "url": "https://evil.test/items/fake/"}]])
def test_invalid_catalogues_fail_without_partial_empty_results(data):
    with pytest.raises(WikiError):
        parse_entries(data, "item")


def test_search_suggests_typos_without_treating_them_as_exact_matches():
    matches, fuzzy = find_entries(parse_entries(ITEMS, "item"), "Gelno")
    assert matches[0].name == "Gelano"
    assert fuzzy
    assert find_entries(parse_entries(ITEMS, "item"), "z")[0] == []


def test_items_with_missing_level_remain_searchable_by_name():
    entries = parse_entries([{**ITEMS[0], "level": None}], "item")
    assert entries[0].level == "?"
    assert find_entries(entries, "Gelano")[0] == list(entries)


@pytest.mark.parametrize("query,expected", [("coiffes", "Chapeau"), ("bottes", "Botte"), ("épée", "Epée"), ("sac", "Sac à dos")])
def test_equipment_aliases_resolve_to_real_api_types(query, expected):
    assert equipment_category(query) == expected


def test_equipment_rejects_resources():
    with pytest.raises(WikiError):
        equipment_category("Bois")


@pytest.mark.parametrize("query,expected", [
    ("coiffe", "Chapeau"), ("COIFFES", "Chapeau"), ("coif", "Chapeau"),
    ("bottes", "Botte"), ("dagues", "Dague"), ("ÉPÉES", "Epée"),
    ("sac", "Sac à dos"), ("anneaux", "Anneau"), ("marteaux", "Marteau"),
    ("sacs à dos", "Sac à dos"), ("armes magiques", "Arme magique"), ("Bois", None),
])
def test_equipment_suggestions_understand_supported_aliases(query, expected):
    suggestions = equipment_suggestions(query)
    if expected is None:
        assert suggestions == []
    else:
        assert expected in suggestions
        assert all(equipment_category(value) == value for value in suggestions)


def test_equipment_suggestions_are_unique_and_respect_the_requested_limit():
    suggestions = equipment_suggestions("")
    assert len(suggestions) == len(set(suggestions))
    assert len(suggestions) <= 25
    assert equipment_suggestions("", 3) == suggestions[:3]


def test_icon_index_accepts_only_wiki_assets_and_fiches():
    assert parse_icons([
        {"u": "/items/gelano/", "i": "/icons/item_9_47.png"},
        {"u": "/items/evil/", "i": "https://evil.test/track.png"},
        {"u": "https://evil.test/items/gelano/", "i": "/icons/item_9_47.png"},
    ]) == {"/items/gelano/": WIKI_ORIGIN + "/icons/item_9_47.png"}


@pytest.mark.parametrize("url", ["https://[invalid/items/fake/", "https://example.com：443/items/fake/"])
def test_malformed_url_in_one_row_does_not_break_valid_catalogues_or_icons(url):
    entries = parse_entries([*ITEMS, {**ITEMS[0], "url": url}], "item")
    assert len(entries) == 2
    assert parse_icons([
        {"u": "/items/gelano/", "i": "/icons/item_9_47.png"},
        {"u": url, "i": "/icons/item_9_47.png"},
        {"u": "/items/gelano/", "i": url},
    ]) == {"/items/gelano/": WIKI_ORIGIN + "/icons/item_9_47.png"}


@pytest.mark.asyncio
async def test_http_catalogue_is_parsed_once_and_cached(client):
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], payload=ITEMS)
        first = await client.items()
        assert await client.items() is first
        assert len(first) == 2
        assert sum(len(calls) for calls in http.requests.values()) == 1


@pytest.mark.asyncio
async def test_concurrent_requests_share_download_even_if_one_caller_cancels(client, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return ITEMS

    mock = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(client, "_fetch", mock)
    first = asyncio.create_task(client.items())
    await entered.wait()
    second = asyncio.create_task(client.items())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert len(await second) == 2
    mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_network_failure_uses_stale_cache_then_expires_it(client):
    now = [100.0]
    client.clock = lambda: now[0]
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], payload=ITEMS)
        expected = await client.items()
        now[0] += 61
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], status=503)
        assert await client.items() is expected
        assert client.is_stale(INDEX_PATHS[0])
        assert await client.items() is expected
        assert sum(len(calls) for calls in http.requests.values()) == 2
        now[0] += 86400
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], status=503)
        with pytest.raises(WikiError):
            await client.items()
        assert client.peek(INDEX_PATHS[0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"status": 404}, {"status": 200, "body": "<html>erreur</html>", "content_type": "text/html"},
    {"status": 200, "body": "not JSON", "content_type": "application/json"},
    {"exception": asyncio.TimeoutError()}, {"payload": {"unexpected": True}},
])
async def test_http_errors_and_invalid_payloads_are_reported_as_wiki_errors(client, response):
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], **response)
        with pytest.raises(WikiError):
            await client.items()


@pytest.mark.asyncio
async def test_rate_limit_stops_other_outgoing_requests_until_retry_after(client):
    now = [100.0]
    client.clock = lambda: now[0]
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], status=429, headers={"Retry-After": "120"})
        with pytest.raises(WikiError):
            await client.items()
        with pytest.raises(WikiError):
            await client.monsters()
        assert sum(len(calls) for calls in http.requests.values()) == 1
        now[0] += 121
        http.get(WIKI_ORIGIN + INDEX_PATHS[1], payload=MONSTERS)
        assert len(await client.monsters()) == 2


@pytest.mark.parametrize("value,expected", [
    ("120", 120), (" 120 ", 120), ("0", 60), ("1", 60),
    ("99999999", 86400), ("9" * 5000, 86400), ("-1", 60),
    ("²", 60), ("NaN", 60), ("", 60), ("not a date", 60),
])
def test_retry_after_delay_accepts_seconds_and_bounds_invalid_or_extreme_values(value, expected):
    assert retry_after_delay(value, now=1_800_000_000) == expected


@pytest.mark.parametrize("offset,expected", [(300, 300), (-300, 60), (172800, 86400)])
def test_retry_after_delay_accepts_http_dates(offset, expected):
    now = 1_800_000_000
    value = formatdate(now + offset, usegmt=True)
    assert retry_after_delay(value, now=now) == expected


@pytest.mark.asyncio
async def test_rate_limit_http_date_defers_all_requests_until_date(client, monkeypatch):
    now = [100.0]
    client.clock = lambda: now[0]
    monkeypatch.setattr("utils.dofus_wiki.time.time", lambda: 1_800_000_000)
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], status=429, headers={
            "Retry-After": formatdate(1_800_000_300, usegmt=True),
        })
        with pytest.raises(WikiError):
            await client.items()
        now[0] += 299
        with pytest.raises(WikiError):
            await client.monsters()
        assert sum(len(calls) for calls in http.requests.values()) == 1
        now[0] += 31
        http.get(WIKI_ORIGIN + INDEX_PATHS[1], payload=MONSTERS)
        assert len(await client.monsters()) == 2


@pytest.mark.asyncio
async def test_concurrent_rate_limits_never_shorten_the_existing_wait(client):
    client.clock = lambda: 100.0
    both_entered = asyncio.Event()
    first_finished = asyncio.Event()
    entered = []

    async def callback(url, **kwargs):
        entered.append(str(url))
        if len(entered) == 2:
            both_entered.set()
        await both_entered.wait()
        if str(url).endswith("/items.json"):
            return CallbackResult(status=429, headers={"Retry-After": "120"})
        await first_finished.wait()
        return CallbackResult(status=429, headers={"Retry-After": "60"})

    async def request_items():
        try:
            await client.items()
        finally:
            first_finished.set()

    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], callback=callback)
        http.get(WIKI_ORIGIN + INDEX_PATHS[1], callback=callback)
        results = await asyncio.wait_for(asyncio.gather(
            request_items(), client.monsters(), return_exceptions=True,
        ), timeout=2)
    assert all(isinstance(result, WikiError) for result in results)
    assert client._backoff_until == 220.0


@pytest.mark.asyncio
async def test_monster_detail_uses_page_link_instead_of_list_identifier(client):
    entry = parse_entries(MONSTERS, "monster")[1]
    page = '<a href="/api/monster/106.json">JSON</a><meta property="og:image" content="/icons/sprite_1008.png">'
    data = {"id": 106, "name": "Craqueleur", "url": entry.url, "grades": []}
    with aioresponses() as http:
        http.get(entry.url, body=page, content_type="text/html")
        http.get(WIKI_ORIGIN + "/api/monster/106.json", payload=data)
        detail = await client.detail(entry)
        assert detail.data["id"] == 106
        assert detail.icon == WIKI_ORIGIN + "/icons/sprite_1008.png"
        assert sum(len(calls) for calls in http.requests.values()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"id": 99}, {"name": "Autre monstre"}, {"url": WIKI_ORIGIN + "/monstres/autre/"}])
async def test_monster_mismatches_never_display_another_fiche(client, change):
    entry = parse_entries(MONSTERS, "monster")[0]
    with aioresponses() as http:
        http.get(entry.url, body='<a href="/api/monster/37.json">JSON</a>', content_type="text/html")
        http.get(WIKI_ORIGIN + "/api/monster/37.json", payload={"id": 37, "name": "Craqueleur", "url": entry.url, **change})
        with pytest.raises(WikiError):
            await client.detail(entry)


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [
    '<a href="https://evil.test/api/monster/37.json">JSON</a>',
    '<a href="/api/monster/37.json">A</a><a href="/api/monster/38.json">B</a>',
    '<script>fetch("/api/monster/37.json")</script>',
])
async def test_monster_page_requires_one_explicit_local_json_link(client, page):
    entry = parse_entries(MONSTERS, "monster")[0]
    with aioresponses() as http:
        http.get(entry.url, body=page, content_type="text/html")
        with pytest.raises(WikiError):
            await client.detail(entry)


@pytest.mark.asyncio
async def test_transport_refuses_arbitrary_paths_and_redirects(client):
    with pytest.raises(WikiError):
        await client._fetch("//evil.test/api/items.json")
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], status=302, headers={"Location": "https://evil.test/"})
        with pytest.raises(WikiError):
            await client.items()
        assert sum(len(calls) for calls in http.requests.values()) == 1


@pytest.mark.asyncio
async def test_large_response_is_rejected(client):
    with aioresponses() as http:
        http.get(WIKI_ORIGIN + INDEX_PATHS[0], body=" " * (8 * 1024 * 1024 + 1))
        with pytest.raises(WikiError, match="volumineuse"):
            await client.items()


@pytest.mark.asyncio
async def test_cache_remains_bounded_without_evicting_catalogues(client, monkeypatch):
    for path in INDEX_PATHS:
        client._cache[path] = CacheEntry(["index"], client.clock())
    monkeypatch.setattr(client, "_fetch", AsyncMock(return_value={"detail": True}))
    for identifier in range(1, 270):
        await client.resource(f"/api/item/{identifier}.json")
    assert len(client._cache) == 259
    assert all(path in client._cache for path in INDEX_PATHS)
    assert "/api/item/1.json" not in client._cache


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_downloads_and_closes_session(client, monkeypatch):
    entered = asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_fetch", fetch)
    request = asyncio.create_task(client.items())
    await entered.wait()
    await client.close()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert not client._inflight
