import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aioresponses import aioresponses
import pytest
import pytest_asyncio

from dofus_wiki import DofusWikiCog, DetailView, RecipeQuantityModal, ResultView
from utils.dofus_wiki import INDEX_PATHS, WikiError
from utils.xixou_api import DropSource, Harvest, ItemEnrichment, XixouClient, Zone
from test_dofus_wiki_commands import (
    ITEMS, MONSTERS, context, detail_for, discord_error, interaction,
)
from test_slash_commands import make_interaction, slash_bot
from slash_commands import SlashCommandsCog


@pytest.fixture
def enrichment():
    return ItemEnrichment(
        drops=(DropSource("Gelée Fraise", "0.03–0.07 %", (("24", "0.03 %"),
                         ("30", "0.07 %")), "1000", "1", ("La péninsule des gelées",)),),
        zones=(Zone("La péninsule des gelées", ((10, 28), (11, 28))),
               Zone("Coin des Bouftous", ((2, 10),))),
        harvests=(Harvest("Frêne", "Bûcheron", "1", ((2, 5, 3),)),),
        uses=("Anneau de test",), dates=("2026-09-01T10:30:07+00:00",),
    )


@pytest_asyncio.fixture
async def enriched_cog(enrichment):
    moon = SimpleNamespace(
        items=AsyncMock(return_value=ITEMS), monsters=AsyncMock(return_value=MONSTERS),
        detail=AsyncMock(side_effect=detail_for), warmup=AsyncMock(), close=AsyncMock(),
        is_stale=Mock(return_value=False), peek=Mock(return_value=ITEMS),
    )
    extra = SimpleNamespace(
        enabled=True, enrich=AsyncMock(return_value=enrichment),
        warmup=AsyncMock(), close=AsyncMock(),
    )
    renderer = SimpleNamespace(render=AsyncMock(return_value=b"map-image"), close=AsyncMock())
    cog = DofusWikiCog(
        SimpleNamespace(), client=moon, enrichment_client=extra, map_renderer=renderer,
        image_client=None,
    )
    yield cog
    await cog.cog_unload()


@pytest.mark.asyncio
async def test_prefix_enriches_selected_item_without_rendering_maps(enriched_cog):
    ctx = context()
    await enriched_cog.objet_command.callback(enriched_cog, ctx, nom="Gelano")
    view = ctx.send.call_args.kwargs["view"]
    assert len(view.section_select.options) == 6
    assert view.section == "summary"
    enriched_cog.enrichment_client.enrich.assert_awaited_once()
    enriched_cog.map_renderer.render.assert_not_awaited()
    assert any(getattr(child, "url", "") == "https://xixou.io" for child in view.children)


@pytest.mark.asyncio
async def test_slash_reaches_enriched_object_handler(slash_bot, enriched_cog):
    await slash_bot.add_cog(enriched_cog)
    catalog = SlashCommandsCog(slash_bot)
    catalog.register_commands()
    command = slash_bot.tree.get_command("objet")
    selected = make_interaction(slash_bot, command)
    await command.callback(selected, nom="item:1")
    selected.response.defer.assert_awaited_once_with(thinking=True)
    view = selected.followup.send.call_args.kwargs["view"]
    assert isinstance(view, DetailView)
    assert view.sections and view.owner_id == selected.user.id
    catalog.cog_unload()
    await slash_bot.remove_cog("DofusWikiCog")


@pytest.mark.asyncio
async def test_equipment_selection_enriches_only_after_selection(enriched_cog):
    ctx = context()
    await enriched_cog.equipement_command.callback(
        enriched_cog, ctx, categorie="coiffe", niveau=100,
    )
    results = ctx.send.call_args.kwargs["view"]
    enriched_cog.enrichment_client.enrich.assert_not_awaited()
    selected = interaction(values=["0"])
    await results.choose(selected)
    view = selected.edit_original_response.call_args.kwargs["view"]
    assert view.sections
    enriched_cog.enrichment_client.enrich.assert_awaited_once()


@pytest.mark.asyncio
async def test_autocomplete_never_starts_xixou_or_map_download(enriched_cog):
    enriched_cog.client.is_stale.return_value = True
    await enriched_cog.autocomplete("wiki_items", "gel")
    await asyncio.sleep(0)
    enriched_cog.enrichment_client.warmup.assert_not_awaited()
    enriched_cog.enrichment_client.enrich.assert_not_awaited()
    enriched_cog.map_renderer.render.assert_not_awaited()
    assert enriched_cog.client.peek.call_args.args == (INDEX_PATHS[0],)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, TimeoutError(), ValueError("secret-test-value")])
async def test_missing_or_failed_enrichment_keeps_base_fiche(enriched_cog, failure, caplog):
    enriched_cog.enrichment_client.enrich.return_value = None
    enriched_cog.enrichment_client.enrich.side_effect = failure
    with caplog.at_level("DEBUG"):
        view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    assert not view.sections
    assert view.embed().title == "Objet · Gelano"
    assert "secret-test-value" not in caplog.text


@pytest.mark.asyncio
async def test_monsters_and_explicitly_disabled_client_keep_existing_behavior(enriched_cog):
    view = await enriched_cog.detail_view(42, MONSTERS[0], "monster")
    assert not view.sections
    enriched_cog.enrichment_client.enrich.assert_not_awaited()
    disabled = DofusWikiCog(
        SimpleNamespace(), client=enriched_cog.client, enrichment_client=None, image_client=None,
    )
    try:
        assert not (await disabled.detail_view(42, ITEMS[0], "item")).sections
    finally:
        await disabled.cog_unload()


@pytest.mark.asyncio
async def test_map_page_upload_and_recipe_round_trip_restore_section_and_page(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item", 3)
    selected = interaction(values=["zones"])
    await view.choose_section(selected)
    payload = selected.edit_original_response.call_args.kwargs
    assert payload["embed"].image.url == "attachment://carte-xixou.png"
    assert payload["attachments"][0].filename == "carte-xixou.png"
    assert view.map_link is not None
    await view.go_next(interaction())
    assert view.page == 1
    recipe = interaction()
    await view.toggle_action(recipe)
    assert view.action == "recipe" and view.section_select.disabled
    assert recipe.edit_original_response.call_args.kwargs["attachments"] == []
    assert view.map_link is None
    modal = RecipeQuantityModal(view)
    modal.quantity._value = "7"
    await modal.on_submit(interaction())
    assert view.quantity == 7
    await view.toggle_action(interaction())
    assert view.section == "zones" and view.page == 1 and view.action == "item"
    assert not view.section_select.disabled
    await view.toggle_action(interaction())
    assert "700 ×" in view.embed().fields[0].value


@pytest.mark.asyncio
async def test_leaving_map_clears_attachments_and_preserves_results(enriched_cog):
    results = ResultView(enriched_cog, 42, ITEMS * 3, "item", title="Équipements")
    results.page = 1
    view = await enriched_cog.detail_view(42, ITEMS[0], "item", results=results.snapshot())
    results.stop()
    await view.choose_section(interaction(values=["zones"]))
    summary = interaction(values=["summary"])
    await view.choose_section(summary)
    assert summary.edit_original_response.call_args.kwargs["attachments"] == []
    assert not summary.edit_original_response.call_args.kwargs["embed"].image.url
    await view.choose_section(interaction(values=["zones"]))
    returned = interaction()
    await view.return_to_results(returned)
    payload = returned.edit_original_response.call_args.kwargs
    assert payload["attachments"] == []
    assert payload["view"].page == 1
    assert payload["view"].title == "Équipements"


@pytest.mark.asyncio
async def test_failed_attachment_retries_text_with_map_link(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    selected.edit_original_response.side_effect = [discord_error(), SimpleNamespace(edit=AsyncMock())]
    await view.choose_section(selected)
    assert selected.edit_original_response.await_count == 2
    payload = selected.edit_original_response.call_args.kwargs
    assert payload["attachments"] == []
    assert not payload["embed"].image.url
    assert view.section == "zones" and view.map_link is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("map unavailable")])
async def test_missing_map_keeps_text_and_links(enriched_cog, failure):
    enriched_cog.map_renderer.render.return_value = None
    enriched_cog.map_renderer.render.side_effect = failure
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    await view.choose_section(selected)
    assert selected.edit_original_response.call_args.kwargs["attachments"] == []
    assert view.map_link is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [discord_error(), asyncio.CancelledError()])
async def test_failed_section_change_rolls_back_visible_state(enriched_cog, error):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    selected.edit_original_response.side_effect = error
    with pytest.raises(type(error)):
        await view.choose_section(selected)
    assert view.section == "summary" and view.page == 0 and view.map_link is None
    assert next(option for option in view.section_select.options if option.default).value == "summary"


@pytest.mark.asyncio
async def test_page_preparation_failure_preserves_navigation_state(enriched_cog, monkeypatch):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    monkeypatch.setattr(view, "build_pages", Mock(side_effect=ValueError("invalid section")))
    selected = interaction(values=["zones"])
    with pytest.raises(ValueError):
        await view.choose_section(selected)
    assert view.section == "summary" and view.page == 0
    selected.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_while_map_renders_does_not_publish_or_reenable_controls(enriched_cog):
    entered, release = asyncio.Event(), asyncio.Event()

    async def render(spec):
        entered.set()
        await release.wait()
        return b"map"

    enriched_cog.map_renderer.render.side_effect = render
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    selected = interaction(values=["zones"])
    task = asyncio.create_task(view.choose_section(selected))
    await entered.wait()
    view.stop()
    release.set()
    await task
    selected.edit_original_response.assert_not_awaited()
    assert all(child.disabled for child in view.children if not getattr(child, "url", None))
    assert view.section == "summary"


@pytest.mark.asyncio
async def test_concurrent_navigation_defers_while_rendering_and_keeps_order(enriched_cog):
    entered, release = asyncio.Event(), asyncio.Event()

    async def render(spec):
        entered.set()
        await release.wait()
        return b"map"

    enriched_cog.map_renderer.render.side_effect = render
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    first = asyncio.create_task(view.choose_section(interaction(values=["zones"])))
    await entered.wait()
    selected = interaction()
    second = asyncio.create_task(view.go_next(selected))
    await asyncio.sleep(0)
    selected.response.defer.assert_awaited_once()
    assert not second.done()
    release.set()
    await asyncio.gather(first, second)
    assert view.section == "zones" and view.page == 1


@pytest.mark.asyncio
async def test_section_selection_preserves_ownership_and_rejects_unknown_values(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    assert not await view.interaction_check(interaction(author=99))
    for values in ([], ["not-a-section"], ["drops", "zones"]):
        selected = interaction(values=values)
        await view.choose_section(selected)
        selected.edit_original_response.assert_not_awaited()
    assert view.section == "summary"
    await view.toggle_action(interaction())
    selected = interaction(values=["drops"])
    await view.choose_section(selected)
    selected.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_loading_and_shutdown_manage_both_clients_and_renderer(enriched_cog):
    await enriched_cog.cog_load()
    await asyncio.gather(enriched_cog._warmup, enriched_cog._enrichment_warmup)
    enriched_cog.client.warmup.assert_awaited_once()
    enriched_cog.enrichment_client.warmup.assert_awaited_once()
    await enriched_cog.cog_unload()
    enriched_cog.client.close.assert_awaited_once()
    enriched_cog.enrichment_client.close.assert_awaited_once()
    enriched_cog.map_renderer.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_during_enrichment_cannot_register_a_new_view(enriched_cog, enrichment):
    entered, release = asyncio.Event(), asyncio.Event()

    async def enrich(detail):
        entered.set()
        await release.wait()
        return enrichment

    enriched_cog.enrichment_client.enrich.side_effect = enrich
    loading = asyncio.create_task(enriched_cog.detail_view(42, ITEMS[0], "item"))
    await entered.wait()
    await enriched_cog.cog_unload()
    release.set()
    with pytest.raises(WikiError, match="redémarre"):
        await loading
    assert not enriched_cog.views


@pytest.mark.asyncio
async def test_closed_cog_cannot_publish_a_prepared_view(enriched_cog):
    view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    await enriched_cog.cog_unload()
    sender = AsyncMock()
    with pytest.raises(WikiError, match="redémarre"):
        await view.publish(sender)
    sender.assert_not_awaited()
    assert not enriched_cog.views


@pytest.mark.asyncio
async def test_http_catalogues_reach_drop_and_map_sections(enriched_cog):
    source = "https://xixou.io"
    zone = "La péninsule des gelées"
    families = {
        "equipements": {"anneaux": [{"name": "Gelano", "level": "60", "pods": "5",
                                       "drops": [{"name": "Gelée Fraise", "rate": "0.07%", "pp": 1000}]}]},
        "ressources": {"bois": [{"name": "Bois de Frêne", "level": "1"}]},
        "monstres": [{"id": 57, "name": "Gelée Fraise - Gelaviv le Glaçon", "zones": [zone],
                       "stats": {"niveau": {"ranks": {"rank_1": "24", "rank_2": "30"}}},
                       "drops": [{"name": "Gelano", "type": "equipement", "taux": "0.07%",
                                  "taux_ranks": [0.03, 0.07], "pp": 1000, "max": "1"}]}],
        "carte": {"zones": [{"name": zone, "cells": [[10, 28]]}], "resources": {}},
    }
    xixou = XixouClient(api_key="test-key")
    enriched_cog.enrichment_client = xixou
    with aioresponses() as http:
        for family, data in families.items():
            http.get(f"{source}/api/v1/{family}.json", payload={
                "source": source, "famille": family, "genere_le": "2026-09-01T10:30:07+00:00",
                "data": data,
            })
        view = await enriched_cog.detail_view(42, ITEMS[0], "item")
    assert view.sections
    await view.choose_section(interaction(values=["drops"]))
    text = str(view.embed().to_dict())
    assert "0.03" in text or "0,03" in text
    assert "1000" in text or "1 000" in text
    await view.choose_section(interaction(values=["zones"]))
    assert view.map_link is not None
    assert "zone=" in view.map_link.url
