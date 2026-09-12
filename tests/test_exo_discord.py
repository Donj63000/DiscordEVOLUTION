import asyncio
import copy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from discord.ext import commands
import pytest
import pytest_asyncio

from exo import ExoCog, ExoModal, ExoView, percentage
from utils.dofus_wiki import WikiDetail, WikiEntry
from utils.exo_data import demo_item
from utils.exo_embeds import build_embed
from utils.exo_engine import D, Item, Rates, Rune, STATS, State
from utils.exo_session import Session, export_session
from utils.slash_help import category
from utils.wiki_embeds import utf16_length

REAL_EVALUATE = discord.utils.evaluate_annotation
REAL_INSIDE = discord.utils.is_inside_class


def interaction(user_id=42):
    response=SimpleNamespace(
        is_done=Mock(return_value=False), defer=AsyncMock(),
        send_message=AsyncMock(), send_modal=AsyncMock(),
    )
    async def defer(**kwargs):
        response.is_done.return_value=True
    response.defer.side_effect=defer
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id), guild_id=100, response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(return_value=SimpleNamespace(edit=AsyncMock())),
    )


@pytest_asyncio.fixture
async def panel():
    cog=ExoCog(SimpleNamespace(get_cog=lambda name:None))
    view=ExoView(cog,42,100,Session.create(demo_item()))
    cog.views[view.key]=view
    yield view
    await cog.cog_unload()


def limits(view):
    for row in view.to_components():
        assert len(row["components"]) <= 5
        for component in row["components"]:
            assert len(component.get("custom_id","")) <= 100
            assert len(component.get("options",[])) <= 25
    assert len(view.to_components()) <= 5
    assert len(view.children) <= 25
    embed=view.embed()
    assert len(embed) <= 6000
    for field in embed.fields:
        assert utf16_length(field.name) <= 256
        assert utf16_length(field.value) <= 1024


@pytest.mark.asyncio
async def test_native_command_registration_without_connection(monkeypatch):
    monkeypatch.setattr(discord.utils,"evaluate_annotation",REAL_EVALUATE)
    monkeypatch.setattr(discord.utils,"is_inside_class",REAL_INSIDE)
    async with commands.Bot(command_prefix="!",intents=discord.Intents.none()) as bot:
        await bot.load_extension("exo")
        command=bot.tree.get_command("exo")
        assert command is not None
        assert category(command) == "Dofus Rétro"
        payload=command.to_dict(bot.tree)
        assert payload["name"] == "exo"
        assert {option["name"] for option in payload["options"]} == {"objet","objectif","reprise"}
        assert all(not option.get("required",False) for option in payload["options"])
        await bot.load_extension("slash_commands")
        assert bot.tree.get_command("exo") is command
        await bot.unload_extension("slash_commands")
        assert bot.tree.get_command("exo") is command
        await bot.unload_extension("exo")
        assert bot.tree.get_command("exo") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tab",["atelier","maths","journal","aide"])
async def test_components_and_embeds_fit_discord(panel,tab):
    panel.session.tab=tab
    for page in (0,1):
        panel.page=page
        panel.rebuild()
        limits(panel)


@pytest.mark.asyncio
async def test_large_item_embed_is_bounded(panel):
    item=Item("A"*200,"test",{key:(0,99) for key in STATS},"source",tuple(["Effet inconnu "*30]*5))
    panel.session=Session.create(item)
    panel.session.notice="X"*3000
    panel.rebuild()
    limits(panel)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["jets","rates","observe","math","budget","search"])
async def test_modal_limits(panel,kind):
    modal=ExoModal(panel,kind)
    assert len(modal.children) <= 5
    assert len(modal.title) <= 45
    for child in modal.children:
        assert len(child.label) <= 45
        assert child.max_length <= 4000
        assert len(str(child.default or "")) <= child.max_length
    modal.stop()


@pytest.mark.asyncio
async def test_owner_only(panel):
    before=copy.deepcopy(panel.session)
    event=interaction(99)
    await panel.dispatch(event,"one")
    assert panel.session == before
    event.response.send_message.assert_awaited_once()
    event.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_panel_cannot_mutate(panel):
    panel.stop()
    event=interaction()
    await panel.dispatch(event,"one")
    assert panel.session.sim.attempts == 0
    event.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_attempt_and_undo_restore_random_sequence(panel):
    event=interaction()
    before=copy.deepcopy(panel.session.sim)
    await panel.dispatch(event,"one")
    first=copy.deepcopy(panel.session.sim)
    assert first.attempts == 1
    await panel.dispatch(interaction(),"undo")
    assert panel.session.sim == before
    await panel.dispatch(interaction(),"one")
    assert panel.session.sim == first


@pytest.mark.asyncio
async def test_failed_publication_rolls_back(panel):
    event=interaction()
    event.edit_original_response.side_effect=RuntimeError("network down")
    original=copy.deepcopy(panel.session)
    with pytest.raises(RuntimeError):
        await panel.dispatch(event,"one")
    assert panel.session == original
    assert panel.undo_session is None


@pytest.mark.asyncio
async def test_batch_stops_at_objective(panel):
    item=Item("Test","test",{"fo":(1,10)},"fixture")
    panel.session=Session.create(item)
    panel.session.sim=State({"fo":1},D(100))
    panel.session.rune=Rune("fo")
    panel.session.custom["fo:0"]=Rates(1,0)
    panel.session.goal_stat="fo"
    panel.session.goal_value=3
    await panel.dispatch(interaction(),"hundred")
    assert panel.session.sim.attempts == 2
    assert panel.session.sim.jets["fo"] == 3


@pytest.mark.asyncio
async def test_mode_separation_and_simulation_block(panel):
    await panel.dispatch(interaction(),"one")
    previous=copy.deepcopy(panel.session.sim)
    await panel.dispatch(interaction(),"mode")
    assert panel.session.mode == "observation"
    assert panel.session.observed.attempts == 0
    event=interaction()
    await panel.dispatch(event,"one")
    assert panel.session.sim == previous
    assert panel.session.observed.attempts == 0
    event.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_modal_is_not_applied(panel):
    modal=ExoModal(panel,"jets")
    panel.session.revision += 1
    event=interaction()
    before=copy.deepcopy(panel.session)
    await modal.on_submit(event)
    assert panel.session == before
    event.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_jet_modal_initializes_observations_only(panel):
    panel.session.mode="observation"
    before=copy.deepcopy(panel.session.sim)
    modal=ExoModal(panel,"jets")
    modal.apply(panel.session,{"jets":"pa=1","sink":"?","goal":"pm=1","seed":"42"})
    assert panel.session.sim == before
    assert panel.session.observation_ready
    assert panel.session.observed.sink is None
    assert panel.session.seed == 42


@pytest.mark.asyncio
async def test_manual_observation_requires_declared_jet(panel):
    panel.session.mode="observation"
    modal=ExoModal(panel,"observe")
    with pytest.raises(ValueError,match="déclarez"):
        modal.apply(panel.session,{"outcome":"EC","losses":"pa=1"})


@pytest.mark.asyncio
async def test_export_is_private_and_stream_is_closed(panel):
    event=interaction()
    captures=[]
    async def capture(*args,**kwargs):
        captures.append((kwargs["file"],kwargs["file"].fp.read(),kwargs["ephemeral"]))
    event.followup.send.side_effect=capture
    await panel.dispatch(event,"export")
    file,raw,ephemeral=captures[0]
    assert b'"schema": 1' in raw
    assert ephemeral
    assert file.fp.closed


@pytest.mark.asyncio
async def test_concurrent_buttons_serialized(panel):
    await asyncio.gather(
        panel.dispatch(interaction(),"one"),
        panel.dispatch(interaction(),"one"),
    )
    assert panel.session.sim.attempts == 2
    assert panel.session.revision == 2


@pytest.mark.asyncio
async def test_timeout_disables_and_releases_session(panel):
    panel.message=SimpleNamespace(edit=AsyncMock())
    await panel.on_timeout()
    assert panel.retired
    assert panel.key not in panel.cog.views
    assert all(child.disabled for child in panel.children)
    panel.message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_command_works_without_api(panel):
    event=interaction()
    await ExoCog.exo.callback(panel.cog,event)
    created=panel.cog.views[(100,42)]
    assert created.session.item.token == "demo:gelano"
    event.response.defer.assert_awaited_once_with(ephemeral=True,thinking=True)
    event.edit_original_response.assert_awaited_once()
    assert panel.retired


@pytest.mark.asyncio
async def test_import_option_does_not_fetch_external_catalog(panel):
    event=interaction()
    attachment=SimpleNamespace(
        size=5000,filename="session.json",
        read=AsyncMock(return_value=export_session(Session.create(demo_item()))),
    )
    await ExoCog.exo.callback(panel.cog,event,reprise=attachment)
    assert "Snapshot importé" in panel.cog.views[(100,42)].session.item.source


@pytest.mark.asyncio
async def test_borrowed_clients_not_closed(panel):
    wiki=SimpleNamespace(
        client=SimpleNamespace(close=AsyncMock()),
        enrichment_client=SimpleNamespace(close=AsyncMock()),
        image_client=SimpleNamespace(close=AsyncMock()),
    )
    panel.cog.bot=SimpleNamespace(get_cog=lambda name:wiki)
    await panel.cog.cog_unload()
    wiki.client.close.assert_not_awaited()
    wiki.enrichment_client.close.assert_not_awaited()
    wiki.image_client.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_autocomplete_uses_cache_only(panel):
    entry=WikiEntry("item","1","Gelano","Anneau",60,"/items/gelano","gelano")
    client=SimpleNamespace(peek=Mock(return_value=(entry,)),items=AsyncMock())
    wiki=SimpleNamespace(client=client,start_warmup=Mock(),_closed=False)
    panel.cog.bot=SimpleNamespace(get_cog=lambda name:wiki)
    values=await panel.cog.autocomplete(interaction(),"gela")
    assert values[0].value == "item:1"
    client.items.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_search_requires_selection(panel):
    entries=tuple(
        WikiEntry("item",str(i),f"Anneau Test {i}","Anneau",10,f"/items/test-{i}",f"anneau test {i}")
        for i in range(30)
    )
    panel.cog.entries=AsyncMock(return_value=entries)
    panel.cog.load_item=AsyncMock()
    await panel.search(interaction(),"test")
    assert len(panel.search_entries) == 30
    limits(panel)
    panel.cog.load_item.assert_not_awaited()
    await panel.dispatch(interaction(),"next")
    assert panel.search_page == 1
    limits(panel)


@pytest.mark.asyncio
async def test_validated_item_thumbnail_and_fallback(panel):
    panel.image=SimpleNamespace(data=b"testpng",source_url="https://wiki.moon-bot.io/icons/item.png")
    event=interaction()
    await panel.publish(event)
    kwargs=event.edit_original_response.call_args.kwargs
    assert kwargs["embed"].thumbnail.url == "attachment://exo-objet.png"
    assert kwargs["attachments"][0].fp.closed
    response=SimpleNamespace(status=403,reason="Forbidden")
    event=interaction()
    event.edit_original_response.side_effect=[
        discord.HTTPException(response,"missing attach permission"),
        SimpleNamespace(edit=AsyncMock()),
    ]
    await panel.publish(event)
    assert event.edit_original_response.call_count == 2
    kwargs=event.edit_original_response.call_args.kwargs
    assert kwargs["attachments"] == []
    assert kwargs["embed"].thumbnail.url.startswith("https://wiki.moon-bot.io")


@pytest.mark.parametrize("value,expected",[("1",.01),("0,5 %",.005),("100%",1)])
def test_french_rate_input(value,expected):
    assert percentage(value) == expected


@pytest.mark.asyncio
async def test_new_form_retires_previous_forms(panel):
    forms=[]
    for _ in range(5):
        event=interaction()
        await panel.dispatch(event,"jets")
        forms.append(event.response.send_modal.call_args.args[0])
    assert len(panel.modals) == 1
    assert all(form.is_finished() for form in forms[:-1])
    assert not forms[-1].is_finished()
