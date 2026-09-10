import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
import pytest_asyncio

from dofus_wiki import DofusWikiCog, DetailView, RecipeQuantityModal, ResultView
from utils.dofus_wiki import INDEX_PATHS, WIKI_ORIGIN, WikiDetail, WikiError, parse_entries
from utils.wiki_embeds import item_embeds, monster_embeds, recipe_embeds


ITEMS = parse_entries([
    {"id": 1, "name": "Gelano", "type": "Anneau", "level": 60,
     "url": WIKI_ORIGIN + "/items/gelano/"},
    {"id": 2, "name": "Coiffe du Bouftou", "type": "Chapeau", "level": 10,
     "url": WIKI_ORIGIN + "/items/coiffe-du-bouftou/"},
    {"id": 3, "name": "Coiffe haute", "type": "Chapeau", "level": 120,
     "url": WIKI_ORIGIN + "/items/coiffe-haute/"},
    {"id": 4, "name": "Coiffe moyenne", "type": "Chapeau", "level": 50,
     "url": WIKI_ORIGIN + "/items/coiffe-moyenne/"},
    {"id": 5, "name": "Bois de Frêne", "type": "Bois", "level": 1,
     "url": WIKI_ORIGIN + "/items/bois-de-frene/"},
], "item")
MONSTERS = parse_entries([
    {"id": 1, "name": "Craqueleur", "level_min": "1 à 6",
     "url": WIKI_ORIGIN + "/monstres/craqueleur/"},
    {"id": 1, "name": "Craqueleur", "level_min": "25 à 37",
     "url": WIKI_ORIGIN + "/monstres/craqueleur-106/"},
], "monster")


def detail_for(entry=ITEMS[0], **changes):
    data = {"name": entry.name, "level": entry.level, "type": entry.category,
            "description": "Une fiche de test.", "weight": 5, "stats": ["+1 PA"],
            "recipe": [{"item_id": 100, "name": "Gelée Bleutée", "qty": 100}],
            "grades": [{"level": 25, "hp": 150, "ap": 5, "mp": 3,
                        "resist": {"neutral": 0, "earth": 25, "fire": -50, "water": 6, "air": -12}}],
            **changes}
    return WikiDetail(entry, data, WIKI_ORIGIN + "/icons/item_9_47.png", False)


@pytest_asyncio.fixture
async def cog():
    client = SimpleNamespace(
        items=AsyncMock(return_value=ITEMS), monsters=AsyncMock(return_value=MONSTERS),
        detail=AsyncMock(side_effect=detail_for), close=AsyncMock(), warmup=AsyncMock(),
        is_stale=Mock(return_value=False),
        peek=Mock(side_effect=lambda path: MONSTERS if path == INDEX_PATHS[1] else ITEMS),
    )
    instance = DofusWikiCog(SimpleNamespace(), client=client)
    yield instance
    await instance.cog_unload()


def context():
    return SimpleNamespace(author=SimpleNamespace(id=42), send=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())))


def interaction(*, author=42, values=None):
    return SimpleNamespace(
        user=SimpleNamespace(id=author), data={"values": values or []},
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock(), send_message=AsyncMock(),
                                 send_modal=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


@pytest.mark.asyncio
async def test_object_lookup_sends_fiche_and_recipe_button(cog):
    ctx = context()
    await cog.objet_command.callback(cog, ctx, nom="gelano")
    message = ctx.send.call_args.kwargs
    assert message["embed"].title == "Objet · Gelano"
    assert isinstance(message["view"], DetailView)
    assert message["view"].toggle.label == "Voir la recette"
    assert message["allowed_mentions"].everyone is False
    assert message["embed"].thumbnail.url.endswith(".png")


@pytest.mark.asyncio
@pytest.mark.parametrize("query,total", [("Gelano", "100"), ("3 Gelano", "300"), ("10 item:1", "1 000")])
async def test_recipe_prefix_handles_default_quantity_and_multiple_crafts(cog, query, total):
    ctx = context()
    await cog.recette_command.callback(cog, ctx, recherche=query)
    assert f"**{total} ×** Gelée Bleutée" in ctx.send.call_args.kwargs["embed"].fields[0].value


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["0 Gelano", "-2 Gelano", "10001 Gelano"])
async def test_recipe_rejects_invalid_quantities_before_api_request(cog, query):
    ctx = context()
    await cog.recette_command.callback(cog, ctx, recherche=query)
    assert "quantité" in ctx.send.call_args.args[0]
    cog.client.items.assert_not_awaited()


@pytest.mark.asyncio
async def test_equipment_filters_type_and_maximum_level_and_sorts_descending(cog):
    ctx = context()
    await cog.equipement_command.callback(cog, ctx, categorie="coiffes", niveau=100)
    view = ctx.send.call_args.kwargs["view"]
    assert [entry.level for entry in view.entries] == [50, 10]
    assert all(entry.category == "Chapeau" for entry in view.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize("category,level", [("Bois", 100), ("Chapeau", 0), ("Chapeau", 201)])
async def test_invalid_equipment_options_do_not_query_api(cog, category, level):
    ctx = context()
    await cog.equipement_command.callback(cog, ctx, categorie=category, niveau=level)
    assert "embed" not in ctx.send.call_args.kwargs
    cog.client.items.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_monster_name_opens_selection_without_guessing(cog):
    ctx = context()
    await cog.monstre_command.callback(cog, ctx, nom="Craqueleur")
    view = ctx.send.call_args.kwargs["view"]
    assert isinstance(view, ResultView)
    assert len(view.select.options) == 2
    cog.client.detail.assert_not_awaited()
    selected = interaction(values=["1"])
    await view.choose(selected)
    cog.client.detail.assert_awaited_once_with(MONSTERS[1])
    selected.response.defer.assert_awaited_once()
    selected.edit_original_response.assert_awaited_once()
    assert view.is_finished()


@pytest.mark.asyncio
async def test_fuzzy_single_suggestion_still_requires_selection(cog):
    ctx = context()
    await cog.objet_command.callback(cog, ctx, nom="Gelno")
    view = ctx.send.call_args.kwargs["view"]
    assert isinstance(view, ResultView)
    assert view.fuzzy
    cog.client.detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_selection_error_preserves_results_for_retry(cog):
    view = ResultView(cog, 42, list(ITEMS), "item")
    cog.client.detail.side_effect = WikiError("Source indisponible.")
    selected = interaction(values=["0"])
    await view.choose(selected)
    selected.followup.send.assert_awaited_once_with("Source indisponible.", ephemeral=True)
    selected.edit_original_response.assert_not_awaited()
    assert not view.is_finished()


@pytest.mark.asyncio
async def test_empty_or_overlong_queries_do_not_access_api(cog):
    for name in ("", "  ", "a" * 251):
        ctx = context()
        await cog.objet_command.callback(cog, ctx, nom=name)
        ctx.send.assert_awaited_once()
    cog.client.items.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_error_is_explained_instead_of_crashing(cog):
    cog.client.items.side_effect = WikiError("Le wiki ne répond pas.")
    ctx = context()
    await cog.objet_command.callback(cog, ctx, nom="Gelano")
    assert ctx.send.call_args.args[0] == "Le wiki ne répond pas."


@pytest.mark.asyncio
async def test_autocomplete_uses_only_cached_entries_and_underlying_stable_tokens(cog):
    choices = await cog.autocomplete("wiki_monsters", "craqueleur")
    assert len(choices) == 2
    assert choices[0].value != choices[1].value
    assert "25 à 37" in " ".join(choice.name for choice in choices)
    assert (await cog.autocomplete("wiki_items", "gel"))[0].value == "item:1"
    cog.client.items.assert_not_awaited()
    cog.client.monsters.assert_not_awaited()
    cog.client.detail.assert_not_awaited()


@pytest.mark.asyncio
async def test_cold_autocomplete_returns_immediately_and_starts_background_warmup(cog):
    gate = asyncio.Event()
    cog.client.peek.return_value = None
    cog.client.peek.side_effect = None
    cog.client.is_stale.return_value = True
    cog.client.warmup.side_effect = gate.wait
    choices = await cog.autocomplete("wiki_items", "gel")
    assert len(choices) == 1
    assert choices[0].value == "gel"
    assert "chargement" in choices[0].name
    assert await cog.autocomplete("wiki_items", "") == []
    await asyncio.sleep(0)
    assert cog._warmup is not None and not cog._warmup.done()


@pytest.mark.asyncio
async def test_only_author_can_navigate_and_expiry_disables_controls(cog):
    view = DetailView(cog, 42, detail_for(), "item")
    stranger = interaction(author=99)
    assert not await view.interaction_check(stranger)
    stranger.response.send_message.assert_awaited_once()
    assert await view.interaction_check(interaction())
    view.message = SimpleNamespace(edit=AsyncMock())
    await view.on_timeout()
    assert all(child.disabled for child in view.children if not getattr(child, "url", None))
    assert not [child for child in view.children if getattr(child, "url", None)][0].disabled
    view.message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recipe_toggle_preserves_quantity(cog):
    view = DetailView(cog, 42, detail_for(), "recipe", 3)
    await view.toggle_action(interaction())
    assert view.action == "item"
    await view.toggle_action(interaction())
    assert view.action == "recipe"
    assert "300 ×" in view.embed().fields[0].value


@pytest.mark.asyncio
async def test_bad_recipe_does_not_leave_an_unusable_registered_view(cog):
    with pytest.raises(WikiError):
        DetailView(cog, 42, detail_for(recipe=[{"item_id": 1}]), "recipe")
    assert not cog.views


@pytest.mark.asyncio
async def test_result_pagination_shows_every_entry_within_discord_select_limit(cog):
    view = ResultView(cog, 42, list(ITEMS) * 5, "item")
    assert len(view.select.options) == 10
    await view.go_next(interaction())
    assert view.select.options[0].value == "10"
    await view.go_next(interaction())
    assert len(view.select.options) == 5
    assert view.next.disabled
    await view.go_previous(interaction())
    assert view.page == 1


@pytest.mark.asyncio
async def test_navigation_acknowledges_immediately_while_selection_is_loading(cog):
    view = ResultView(cog, 42, list(ITEMS) * 3, "item")
    entered, release = asyncio.Event(), asyncio.Event()

    async def detail(entry):
        entered.set()
        await release.wait()
        return detail_for(entry)

    cog.client.detail.side_effect = detail
    selection = asyncio.create_task(view.choose(interaction(values=["0"])))
    await entered.wait()
    navigation = interaction()
    move = asyncio.create_task(view.go_next(navigation))
    await asyncio.sleep(0)
    navigation.response.defer.assert_awaited_once()
    assert not move.done()
    release.set()
    await asyncio.gather(selection, move)
    navigation.edit_original_response.assert_not_awaited()
    navigation.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_equipment_filters_do_not_invent_missing_levels(cog):
    unknown = parse_entries([{"id": 9, "name": "Coiffe sans niveau", "type": "Chapeau",
                              "level": None, "url": WIKI_ORIGIN + "/items/coiffe-sans-niveau/"}], "item")
    cog.client.items.return_value = ITEMS + unknown
    ctx = context()
    await cog.equipement_command.callback(cog, ctx, categorie="Chapeau", niveau=100)
    assert [entry.level for entry in ctx.send.call_args.kwargs["view"].entries] == [50, 10]


def test_monster_missing_vital_stats_are_not_presented_as_real_zero_values():
    detail = detail_for(MONSTERS[1], grades=[{
        "level": 25, "hp": 0, "ap": 0, "mp": 0,
        "resist": {"neutral": 18, "earth": 10, "fire": 10, "water": 15, "air": 2},
    }])
    embed = monster_embeds(detail)[0]
    assert [field.value for field in embed.fields[:3]] == ["Non renseigné"] * 3
    assert next(field for field in embed.fields if field.name == "Résistance la plus basse").value == "Air (2 %)"


def test_recipe_quantities_are_aggregated_and_empty_recipe_does_not_claim_uncraftable():
    recipe = [{"item_id": 1, "name": "Cuivre", "qty": 2}, {"item_id": 1, "name": "Cuivre", "qty": 3}]
    assert recipe_embeds(detail_for(recipe=recipe), 4)[0].fields[0].value == "**20 ×** Cuivre"
    assert "renseignée" in recipe_embeds(detail_for(recipe=[]), 1)[0].fields[0].value


@pytest.mark.parametrize("recipe", [
    {"invalid": True}, [None], [{"item_id": 1, "name": "Cuivre", "qty": 0}],
    [{"item_id": 1, "name": "Cuivre", "qty": "2"}],
    [{"item_id": 1, "name": "Cuivre", "qty": 1}, {"item_id": 1, "name": "Autre", "qty": 1}],
])
def test_bad_ingredients_do_not_produce_incorrect_totals(recipe):
    with pytest.raises(WikiError):
        recipe_embeds(detail_for(recipe=recipe), 1)


def test_long_fiches_are_paginated_and_mentions_are_neutralized():
    detail = detail_for(stats=["@everyone " + "x" * 1300] * 30, description="d" * 5000)
    pages = item_embeds(detail)
    assert len(pages) > 1
    for page in pages:
        assert len(page) <= 6000
        assert len(page.description) <= 4096
        assert all(len(field.value) <= 1024 for field in page.fields)
        assert "@everyone" not in page.fields[-1].value


def test_stale_fiches_are_labelled():
    original = detail_for()
    stale = WikiDetail(original.entry, original.data, original.icon, True)
    assert "cache" in item_embeds(stale)[0].footer.text


def discord_error(status=403):
    response = SimpleNamespace(status=status, reason="Forbidden" if status == 403 else "Not Found")
    return discord.HTTPException(response, "Message inaccessible")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["item", "recipe", "equipment"])
@pytest.mark.parametrize("error", [discord_error(), asyncio.CancelledError()])
async def test_failed_or_cancelled_sends_release_unpublished_views(cog, action, error):
    ctx = context()
    ctx.send.side_effect = error
    with pytest.raises(type(error)):
        if action == "equipment":
            await cog.equipement_command.callback(cog, ctx, categorie="coiffe")
        else:
            await cog.search(ctx, "Gelano", action)
    assert not cog.views


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [discord_error(404), asyncio.CancelledError()])
async def test_failed_or_cancelled_selection_edit_cleans_new_view(cog, error):
    view = ResultView(cog, 42, ITEMS, "item")
    selected = interaction(values=["0"])
    selected.edit_original_response.side_effect = error
    with pytest.raises(type(error)):
        await view.choose(selected)
    assert cog.views == {view}
    assert not view.is_finished()


@pytest.mark.asyncio
async def test_expiry_during_detail_fetch_does_not_replace_expired_results(cog):
    view = ResultView(cog, 42, ITEMS, "item")
    selected = interaction(values=["0"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_detail(entry):
        entered.set()
        await release.wait()
        return detail_for(entry)

    cog.client.detail.side_effect = delayed_detail
    loading = asyncio.create_task(view.choose(selected))
    await entered.wait()
    view.stop()
    release.set()
    await loading
    assert not cog.views
    selected.edit_original_response.assert_not_awaited()
    assert "expiré" in selected.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_queued_timeout_cannot_restore_old_results_after_successful_selection(cog):
    view = ResultView(cog, 42, ITEMS, "item")
    view.message = SimpleNamespace(edit=AsyncMock())
    selected = interaction(values=["0"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_edit(**kwargs):
        entered.set()
        await release.wait()
        return SimpleNamespace(edit=AsyncMock())

    selected.edit_original_response.side_effect = delayed_edit
    loading = asyncio.create_task(view.choose(selected))
    await entered.wait()
    expired = asyncio.create_task(view.on_timeout())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(loading, expired)
    view.message.edit.assert_not_awaited()
    assert len(cog.views) == 1
    assert isinstance(next(iter(cog.views)), DetailView)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["go_next", "toggle_action", "return_to_results"])
async def test_detail_callbacks_queued_before_expiry_do_not_edit_finished_view(cog, method):
    original = ResultView(cog, 42, ITEMS, "recipe", 3)
    view = DetailView(cog, 42, detail_for(), "recipe", 3, results=original.snapshot())
    original.stop()
    selected = interaction()
    await view.lock.acquire()
    task = asyncio.create_task(getattr(view, method)(selected))
    await asyncio.sleep(0)
    selected.response.defer.assert_awaited_once()
    view.stop()
    view.lock.release()
    await task
    selected.edit_original_response.assert_not_awaited()
    selected.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_return_to_results_restores_page_filters_and_updated_recipe_quantity(cog):
    view = ResultView(cog, 42, ITEMS * 5, "recipe", 3, title="Suggestions", fuzzy=True, stale=True)
    await view.go_next(interaction())
    selected = interaction(values=["12"])
    await view.choose(selected)
    detail = selected.edit_original_response.call_args.kwargs["view"]
    assert detail.results.page == 1
    modal = RecipeQuantityModal(detail)
    modal.quantity._value = "7"
    submitted = interaction()
    await modal.on_submit(submitted)
    assert detail.quantity == 7
    assert "700 ×" in detail.embed().fields[0].value
    returned = interaction()
    await detail.return_to_results(returned)
    restored = returned.edit_original_response.call_args.kwargs["view"]
    assert restored.page == 1
    assert restored.quantity == 7
    assert restored.title == "Suggestions"
    assert restored.fuzzy and restored.stale
    assert restored.select.options[0].value == "10"
    assert cog.views == {restored}
    selected_again = interaction(values=["10"])
    await restored.choose(selected_again)
    next_detail = selected_again.edit_original_response.call_args.kwargs["view"]
    assert "700 ×" in next_detail.embed().fields[0].value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["0", "10001", "-1", "abc", "1.5"])
async def test_quantity_modal_rejects_bad_values_without_changing_recipe(cog, value):
    view = DetailView(cog, 42, detail_for(), "recipe", 3)
    modal = RecipeQuantityModal(view)
    modal.quantity._value = value
    selected = interaction()
    await modal.on_submit(selected)
    assert view.quantity == 3
    selected.edit_original_response.assert_not_awaited()
    assert "quantité" in selected.followup.send.call_args.args[0]
    assert not view.modals


@pytest.mark.asyncio
async def test_quantity_modal_checks_owner_expiry_and_publication_failure(cog):
    view = DetailView(cog, 42, detail_for(), "recipe", 3)
    opened = interaction()
    await view.open_quantity(opened)
    modal = opened.response.send_modal.call_args.args[0]
    assert modal.quantity.default == "3"
    assert not await modal.interaction_check(interaction(author=99))
    view.stop()
    assert modal.is_finished()
    assert not view.modals
    submitted = interaction()
    await modal.on_submit(submitted)
    submitted.edit_original_response.assert_not_awaited()
    assert "expiré" in submitted.followup.send.call_args.args[0]


@pytest.mark.asyncio
async def test_failed_modal_send_leaves_no_modal_registered(cog):
    view = DetailView(cog, 42, detail_for(), "recipe")
    opened = interaction()
    opened.response.send_modal.side_effect = discord_error()
    with pytest.raises(discord.HTTPException):
        await view.open_quantity(opened)
    assert not view.modals


@pytest.mark.asyncio
async def test_failed_quantity_update_rolls_back_recipe_state(cog):
    view = DetailView(cog, 42, detail_for(), "recipe", 3)
    modal = RecipeQuantityModal(view)
    modal.quantity._value = "7"
    submitted = interaction()
    submitted.edit_original_response.side_effect = discord_error()
    with pytest.raises(discord.HTTPException):
        await modal.on_submit(submitted)
    assert view.quantity == 3
    assert "300 ×" in view.embed().fields[0].value
    assert not view.modals


@pytest.mark.asyncio
async def test_failed_return_keeps_detail_and_releases_new_results(cog):
    results = ResultView(cog, 42, ITEMS, "item")
    view = DetailView(cog, 42, detail_for(), "item", results=results.snapshot())
    results.stop()
    returned = interaction()
    returned.edit_original_response.side_effect = discord_error(404)
    with pytest.raises(discord.HTTPException):
        await view.return_to_results(returned)
    assert cog.views == {view}
    assert not view.is_finished()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["results", "detail", "toggle"])
async def test_failed_navigation_preserves_visible_state(cog, action):
    selected = interaction()
    selected.edit_original_response.side_effect = discord_error()
    if action == "results":
        view = ResultView(cog, 42, ITEMS * 5, "item")
    else:
        view = DetailView(cog, 42, detail_for(stats=["x" * 900] * 3), "item")
    with pytest.raises(discord.HTTPException):
        await (view.toggle_action(selected) if action == "toggle" else view.go_next(selected))
    assert view.page == 0
    assert view.action == "item"
    assert view.previous.disabled


@pytest.mark.asyncio
@pytest.mark.parametrize("query,category", [("coiffe", "Chapeau"), ("bottes", "Botte"), ("dagues", "Dague")])
async def test_equipment_autocomplete_understands_same_aliases_as_command(cog, query, category):
    assert category in [choice.value for choice in await cog.autocomplete("wiki_types", query)]


@pytest.mark.asyncio
async def test_equipment_accepts_minimum_level_and_name_filter(cog):
    ctx = context()
    await cog.equipement_command.callback(cog, ctx, categorie="coiffe", niveau=100, niveau_min=20, nom="moyenne")
    view = ctx.send.call_args.kwargs["view"]
    assert [entry.name for entry in view.entries] == ["Coiffe moyenne"]
    assert view.title == "Chapeau · Niveau 20 à 100"


@pytest.mark.asyncio
@pytest.mark.parametrize("minimum,name", [(0, ""), (101, ""), (20, "x" * 251)])
async def test_invalid_minimum_level_or_name_filter_does_not_call_api(cog, minimum, name):
    ctx = context()
    await cog.equipement_command.callback(cog, ctx, categorie="coiffe", niveau=100, niveau_min=minimum, nom=name)
    cog.client.items.assert_not_awaited()
    assert "embed" not in ctx.send.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("action,example", [("item", "!objet Gelano"), ("recipe", "!recette 3 Gelano"),
                                           ("monster", "!monstre Bouftou Royal")])
async def test_empty_prefix_queries_explain_prefix_usage(cog, action, example):
    ctx = context()
    await cog.search(ctx, "", action)
    assert example in ctx.send.call_args.args[0]


@pytest.mark.asyncio
async def test_empty_slash_query_explains_slash_usage(cog):
    ctx = context()
    ctx.interaction = object()
    await cog.search(ctx, "", "item")
    assert "/objet" in ctx.send.call_args.args[0]
    assert "!objet" not in ctx.send.call_args.args[0]


def test_monster_known_mp_survives_missing_hp_and_ap():
    detail = detail_for(MONSTERS[1], grades=[{"level": 25, "hp": None, "ap": None, "mp": 5}])
    embed = monster_embeds(detail)[0]
    assert [field.value for field in embed.fields[:3]] == ["Non renseigné", "Non renseigné", "5"]
    assert next(field.value for field in embed.fields if field.name == "Données manquantes").endswith("PV, PA.")


def test_monster_real_zero_mp_is_preserved_when_other_stats_exist():
    detail = detail_for(MONSTERS[1], grades=[{"level": 25, "hp": 100, "ap": 5, "mp": 0}])
    assert monster_embeds(detail)[0].fields[2].value == "0"


@pytest.mark.parametrize("resistance", [-20, 40])
def test_partial_resistances_are_clearly_labelled_as_known_values(resistance):
    detail = detail_for(MONSTERS[1], grades=[{"level": 25, "resist": {"neutral": resistance}}])
    fields = monster_embeds(detail)[0].fields
    assert any(field.name == "Résistance connue la plus basse" for field in fields)
    assert not any(field.name in {"Faiblesse", "Résistance la plus basse"} for field in fields)


@pytest.mark.asyncio
async def test_every_matching_object_remains_accessible_after_first_hundred(cog):
    many = parse_entries([
        {"id": index, "name": f"Épée {index}", "type": "Epée", "level": 10,
         "url": WIKI_ORIGIN + f"/items/epee-{index}/"}
        for index in range(1, 151)
    ], "item")
    cog.client.items.return_value = many
    ctx = context()
    await cog.search(ctx, "épée", "item")
    view = ctx.send.call_args.kwargs["view"]
    assert len(view.entries) == 150
    for _ in range(14):
        await view.go_next(interaction())
    assert view.page == 14
    assert view.select.options[-1].value == "149"
    assert view.next.disabled


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["results", "detail", "toggle", "quantity"])
async def test_edits_keep_latest_interaction_message_for_timeout(cog, action):
    selected = interaction()
    if action == "results":
        view = ResultView(cog, 42, ITEMS * 5, "item")
    else:
        view = DetailView(cog, 42, detail_for(stats=["x" * 900] * 3), "item")
    previous_message = SimpleNamespace(edit=AsyncMock(side_effect=discord_error()))
    view.message = previous_message
    if action == "toggle":
        await view.toggle_action(selected)
    elif action == "quantity":
        modal = RecipeQuantityModal(view)
        modal.quantity._value = "7"
        await modal.on_submit(selected)
    else:
        await view.go_next(selected)
    latest_message = selected.edit_original_response.return_value
    assert view.message is latest_message
    await view.on_timeout()
    latest_message.edit.assert_awaited_once()
    previous_message.edit.assert_not_awaited()


@pytest.mark.parametrize("field", ["hp", "ap", "mp", "level", "resist"])
def test_excessive_monster_numbers_are_rejected_before_discord_embed_limits(field):
    grade = {"level": 25, "hp": 100, "ap": 5, "mp": 3, "resist": {"neutral": 10}}
    grade[field] = {"neutral": -(10 ** 1000)} if field == "resist" else 10 ** 1000
    with pytest.raises(WikiError):
        monster_embeds(detail_for(MONSTERS[1], grades=[grade]))


def test_excessive_recipe_quantity_cannot_silently_truncate_ingredient_totals():
    with pytest.raises(WikiError):
        recipe_embeds(detail_for(recipe=[{"item_id": 1, "name": "Cuivre", "qty": 10 ** 1000}]), 1)


def test_largest_accepted_monster_numbers_stay_within_discord_field_limits():
    maximum = 10 ** 30 - 1
    grades = [{"level": maximum, "hp": maximum, "ap": maximum, "mp": maximum,
               "resist": {key: maximum for key in ("neutral", "earth", "fire", "water", "air")}}]
    for embed in monster_embeds(detail_for(MONSTERS[1], grades=grades)):
        assert len(embed) <= 6000
        assert all(len(field.value) <= 1024 for field in embed.fields)
