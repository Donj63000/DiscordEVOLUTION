import asyncio
import copy
import threading
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
from utils.exo_engine import D, Item, Rates, Rune, STATS, State, attempt, observe
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
    view.session.seed = 1
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
@pytest.mark.parametrize("tab",["atelier","maths","journal","aide","settings"])
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
@pytest.mark.parametrize("kind",["jets","goals","rates","observe","math","budget","search"])
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
    assert b'"schema": 3' in raw
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


def session_with_history(session):
    attempt(session.item, session.sim, Rune("pm"), None, 1, 250000)
    observe(session.item, session.observed, Rune("pm"), "EC", {"pa": 1}, 125000)
    session.observation_ready = True


def jet_form(panel, **overrides):
    modal = ExoModal(panel, "jets")
    values = {key: str(control.default) for key, control in modal.inputs.items()}
    values.update(overrides)
    modal.inputs = {key: SimpleNamespace(value=value) for key, value in values.items()}
    return modal, values


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["simulation", "observation"])
@pytest.mark.parametrize("change", ["goal", "seed", "unchanged", "zero_line"])
async def test_jet_form_preserves_history_without_jet_or_sink_change(panel, mode, change):
    session_with_history(panel.session)
    panel.session.mode = mode
    panel.session.seed = 1
    before = copy.deepcopy(panel.session)
    overrides = {
        "goal": {"goal": "po=1"}, "seed": {"seed": "42"}, "unchanged": {},
        "zero_line": {"jets": "pa=0; pm=0"},
    }[change]
    modal, _ = jet_form(panel, **overrides)
    await modal.on_submit(interaction())
    assert panel.session.sim == before.sim
    assert panel.session.observed == before.observed
    assert "conservés" in panel.session.notice
    if change == "goal":
        assert panel.session.goal_stat == "po"
    if change == "seed":
        assert panel.session.seed == 42
    await panel.dispatch(interaction(), "undo")
    assert panel.session.sim == before.sim
    assert panel.session.observed == before.observed
    assert panel.session.goal_stat == before.goal_stat
    assert panel.session.seed == before.seed


@pytest.mark.asyncio
async def test_omitted_zero_line_does_not_reset_history(panel):
    session_with_history(panel.session)
    panel.session.sim.jets["pm"] = 0
    before = copy.deepcopy(panel.session.sim)
    modal, _ = jet_form(panel, jets="pa=0")
    await modal.on_submit(interaction())
    assert panel.session.sim == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["simulation", "observation"])
@pytest.mark.parametrize("change", ["jet", "sink"])
async def test_jet_form_resets_only_current_mode_after_actual_change(panel, mode, change):
    session_with_history(panel.session)
    panel.session.mode = mode
    before = copy.deepcopy(panel.session)
    overrides = {"jets": "pa=1"} if change == "jet" else {"sink": "12"}
    modal, _ = jet_form(panel, **overrides)
    await modal.on_submit(interaction())
    state = panel.session.state
    assert (state.attempts, state.successes, state.spent, state.sequence) == (0, 0, 0, 0)
    assert state.journal == []
    assert "remis à zéro" in panel.session.notice
    if mode == "simulation":
        assert panel.session.observed == before.observed
    else:
        assert panel.session.sim == before.sim
    await panel.dispatch(interaction(), "undo")
    assert panel.session.sim == before.sim
    assert panel.session.observed == before.observed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["simulation", "observation"])
async def test_goal_publication_failure_restores_complete_state(panel, mode):
    session_with_history(panel.session)
    panel.session.mode = mode
    panel.undo_session = copy.deepcopy(panel.session)
    panel.rebuild()
    before = copy.deepcopy(panel.session)
    undo = copy.deepcopy(panel.undo_session)
    components = panel.to_components()
    modal, _ = jet_form(panel, goal="po=1")
    event = interaction()
    event.edit_original_response.side_effect = RuntimeError("Discord unavailable")
    with pytest.raises(RuntimeError, match="Discord unavailable"):
        await modal.on_submit(event)
    assert panel.session == before
    assert panel.undo_session == undo
    assert panel.to_components() == components


@pytest.mark.asyncio
async def test_first_observation_declaration_without_state_change_is_ready(panel):
    panel.session.mode = "observation"
    state = copy.deepcopy(panel.session.observed)
    modal, _ = jet_form(panel)
    await modal.on_submit(interaction())
    assert panel.session.observation_ready
    assert panel.session.observed == state


@pytest.mark.asyncio
async def test_invalid_seed_rejects_jet_form_atomically(panel):
    session_with_history(panel.session)
    before = copy.deepcopy(panel.session)
    modal, values = jet_form(panel, jets="pa=1", goal="po=1;pa=1", seed="invalid")
    with pytest.raises(ValueError):
        modal.apply(panel.session, values)
    assert panel.session == before


@pytest.mark.asyncio
async def test_busy_modal_replies_during_loading_and_preserves_existing_form(panel):
    loading, release = asyncio.Event(), asyncio.Event()

    async def load(entry):
        loading.set()
        await release.wait()
        return demo_item(), None

    previous_form = ExoModal(panel, "search")
    panel.search_entries = [SimpleNamespace(label="Gelano")]
    panel.cog.load_item = load
    panel.rebuild()
    selection = asyncio.create_task(panel.dispatch(interaction(), "item", "0"))
    try:
        await asyncio.wait_for(loading.wait(), 1)
        event = interaction()
        await asyncio.wait_for(panel.dispatch(event, "tab", "search"), .5)
        event.response.send_message.assert_awaited_once()
        assert event.response.send_message.call_args.args == (
            "Une action est en cours. Réessaie dans un instant.",
        )
        kwargs = event.response.send_message.call_args.kwargs
        assert kwargs["ephemeral"]
        assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
        event.response.send_modal.assert_not_awaited()
        assert panel.modals == {previous_form}
        assert not previous_form.is_finished()
        assert not selection.done()
    finally:
        release.set()
        await selection
    event = interaction()
    await panel.dispatch(event, "tab", "search")
    event.response.send_modal.assert_awaited_once()
    assert previous_form.is_finished()


@pytest.mark.asyncio
async def test_modal_lock_wait_is_bounded_when_a_waiter_already_exists(panel):
    release, held = asyncio.Event(), asyncio.Event()

    async def queued_action():
        async with panel.lock:
            held.set()
            await release.wait()

    await panel.lock.acquire()
    waiter = asyncio.create_task(queued_action())
    await asyncio.sleep(0)
    panel.lock.release()
    try:
        event = interaction()
        await asyncio.wait_for(panel.dispatch(event, "jets"), .5)
        assert held.is_set()
        assert panel.lock.locked()
        event.response.send_message.assert_awaited_once()
        event.response.send_modal.assert_not_awaited()
    finally:
        release.set()
        await waiter


def delayed_math_renderer(panel, monkeypatch):
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()
    namespace = ExoView.publish.__globals__
    original = namespace["build_embed"]
    loop_thread = threading.get_ident()
    snapshots = []

    def render(snapshot):
        assert threading.get_ident() != loop_thread
        snapshots.append(snapshot)
        loop.call_soon_threadsafe(started.set)
        assert release.wait(5), "Le test doit liberer le rendu"
        return original(snapshot)

    monkeypatch.setitem(namespace, "build_embed", render)
    return started, release, snapshots


@pytest.mark.asyncio
async def test_probability_render_keeps_event_loop_free_and_uses_snapshot(panel, monkeypatch):
    started, release, snapshots = delayed_math_renderer(panel, monkeypatch)
    event = interaction()
    task = asyncio.create_task(panel.dispatch(event, "tab", "maths"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert snapshots[0] is not panel.session
        assert snapshots[0] == panel.session
        assert not task.done()
        event.response.defer.assert_awaited_once()
        event.edit_original_response.assert_not_awaited()
        busy = interaction()
        await asyncio.wait_for(panel.dispatch(busy, "math"), .5)
        busy.response.send_message.assert_awaited_once()
    finally:
        release.set()
        await task
    event.edit_original_response.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiration", ["stop", "deadline", "unload"])
async def test_probability_render_expiration_prevents_publication(panel, monkeypatch, expiration):
    started, release, _ = delayed_math_renderer(panel, monkeypatch)
    before = copy.deepcopy(panel.session)
    unloading = None
    saved = []

    async def capture_snapshot(**kwargs):
        saved.append(kwargs["attachments"][0].fp.read())

    if expiration == "unload":
        panel.message = SimpleNamespace(edit=AsyncMock(side_effect=capture_snapshot))
    event = interaction()
    task = asyncio.create_task(panel.dispatch(event, "tab", "maths"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if expiration == "stop":
            panel.stop()
        elif expiration == "deadline":
            panel.created -= 841
        else:
            unloading = asyncio.create_task(panel.cog.cog_unload())
            await asyncio.sleep(0)
            assert panel.cog.closed
            assert not unloading.done()
            panel.message.edit.assert_not_awaited()
    finally:
        release.set()
        await task
        if unloading is not None:
            await asyncio.wait_for(unloading, 1)
    assert panel.session == before
    event.edit_original_response.assert_not_awaited()
    event.followup.send.assert_awaited_once()
    if expiration != "deadline":
        assert all(child.disabled for child in panel.children)
    if expiration == "unload":
        assert saved == [export_session(before)]


@pytest.mark.asyncio
async def test_probability_render_cancel_restores_state_and_waits_for_worker(panel, monkeypatch):
    started, release, _ = delayed_math_renderer(panel, monkeypatch)
    before = copy.deepcopy(panel.session)
    event = interaction()
    task = asyncio.create_task(panel.dispatch(event, "tab", "maths"))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert panel.lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert panel.session == before
    assert not panel.lock.locked()
    event.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_probability_render_checks_expiration_before_starting_queued_work(panel, monkeypatch):
    renderer = Mock()
    monkeypatch.setitem(ExoView.publish.__globals__, "build_embed", renderer)
    await panel.cog.compute_slots.acquire()
    await panel.cog.compute_slots.acquire()
    before = copy.deepcopy(panel.session)
    event = interaction()
    task = asyncio.create_task(panel.dispatch(event, "tab", "maths"))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        panel.stop()
    finally:
        panel.cog.compute_slots.release()
        panel.cog.compute_slots.release()
        await task
    renderer.assert_not_called()
    assert panel.session == before
    event.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_probability_renders_share_two_compute_slots(panel, monkeypatch):
    loop = asyncio.get_running_loop()
    started, release = asyncio.Event(), threading.Event()
    counter_lock = threading.Lock()
    namespace = ExoView.publish.__globals__
    original = namespace["build_embed"]
    active, maximum, calls = 0, 0, 0

    def render(snapshot):
        nonlocal active, maximum, calls
        with counter_lock:
            active += 1
            maximum = max(maximum, active)
            calls += 1
            if calls == 2:
                loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5)
            return original(snapshot)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setitem(namespace, "build_embed", render)
    views = [panel]
    for owner in (43, 44):
        view = ExoView(panel.cog, owner, 100, Session.create(demo_item()))
        panel.cog.views[view.key] = view
        views.append(view)
    tasks = []
    for view in views:
        view.session.tab = "maths"
        tasks.append(asyncio.create_task(view.publish(interaction(view.owner_id))))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert calls == 2
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert maximum == 2
    assert calls == 3


@pytest.mark.asyncio
async def test_stale_component_does_not_consume_another_rune(panel):
    await panel.dispatch(interaction(), "one", revision=0)
    before = copy.deepcopy(panel.session)
    event = interaction()
    await panel.dispatch(event, "one", revision=0)
    assert panel.session == before
    event.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_two_queued_clicks_on_same_revision_consume_one_rune(panel):
    await asyncio.gather(
        panel.dispatch(interaction(), "one", revision=0),
        panel.dispatch(interaction(), "one", revision=0),
    )
    assert panel.session.sim.attempts == 1


@pytest.mark.asyncio
async def test_busy_modal_gets_immediate_response(panel):
    await panel.lock.acquire()
    event = interaction()
    try:
        await asyncio.wait_for(panel.dispatch(event, "goals"), timeout=.5)
    finally:
        panel.lock.release()
    event.response.send_message.assert_awaited_once()
    event.response.send_modal.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_session_and_undo(panel, monkeypatch):
    before = copy.deepcopy(panel.session)
    original = panel.rebuild
    calls = 0

    def broken_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("render failure")
        original()

    monkeypatch.setattr(panel, "rebuild", broken_once)
    with pytest.raises(RuntimeError, match="render failure"):
        await panel.dispatch(interaction(), "one")
    assert panel.session == before
    assert panel.undo_session is None


@pytest.mark.asyncio
async def test_goal_modal_keeps_attempts_and_journal(panel):
    await panel.dispatch(interaction(), "one")
    before = copy.deepcopy(panel.session.sim)
    modal = ExoModal(panel, "goals")
    modal.apply(panel.session, {"goals": "pm=1;pa=1"})
    assert panel.session.sim == before
    assert panel.session.requirements == {"pm": 1, "pa": 1}
    modal.stop()


@pytest.mark.asyncio
async def test_expiration_attaches_reusable_snapshot_and_closes_stream(panel):
    import json
    from utils.exo_session import import_session

    await panel.dispatch(interaction(), "one")
    captures = []
    async def capture(**kwargs):
        file = kwargs["attachments"][0]
        captures.append((file, file.fp.read()))
    panel.message = SimpleNamespace(edit=AsyncMock(side_effect=capture))
    await panel.on_timeout()
    file, raw = captures[0]
    assert json.loads(raw)["schema"] == 3
    assert import_session(raw).sim == panel.session.sim
    assert file.fp.closed
    assert panel.retired


@pytest.mark.asyncio
async def test_explicit_close_attaches_snapshot(panel):
    event = interaction()
    await panel.dispatch(event, "close")
    kwargs = event.edit_original_response.call_args.kwargs
    assert kwargs["attachments"][0].filename.endswith(".json")
    assert kwargs["attachments"][0].fp.closed
    assert panel.retired


@pytest.mark.asyncio
async def test_new_jet_is_undoable_without_touching_observations(panel):
    before = copy.deepcopy(panel.session)
    await panel.dispatch(interaction(), "preset", "minimum")
    await panel.dispatch(interaction(), "undo")
    assert panel.session.sim == before.sim
    assert panel.session.observed == before.observed
    assert panel.session.seed == before.seed


@pytest.mark.asyncio
async def test_history_navigation_reaches_oldest_entry(panel):
    from utils.exo_engine import attempt

    panel.session.sim = State({"pa": 0}, D(1000))
    for _ in range(20):
        attempt(panel.session.item, panel.session.sim, Rune("fo"), Rates(0, 0), 1)
    panel.session.tab = "journal"
    for _ in range(19):
        await panel.dispatch(interaction(), "older")
    assert panel.session.journal_page == 19
    assert "Essai #1" in panel.embed().fields[0].name
    await panel.dispatch(interaction(), "latest")
    assert panel.session.journal_page == 0


@pytest.mark.asyncio
async def test_existing_thumbnail_is_retained_without_reupload(panel):
    panel.image = SimpleNamespace(data=b"testpng", source_url="https://wiki.moon-bot.io/icons/item.png")
    attachment = SimpleNamespace(filename="exo-objet.png")
    event = interaction()
    event.edit_original_response.return_value = SimpleNamespace(
        edit=AsyncMock(), attachments=[attachment],
    )
    await panel.publish(event)
    event2 = interaction()
    await panel.publish(event2)
    assert event2.edit_original_response.call_args.kwargs["attachments"] == [attachment]


@pytest.mark.asyncio
async def test_catalogue_timeout_does_not_leave_opening_in_thinking_state(panel):
    from utils.dofus_wiki import WikiError

    panel.cog.entries = AsyncMock(side_effect=WikiError("catalogue indisponible"))
    event = interaction()
    await ExoCog.exo.callback(panel.cog, event, objet="Gelano")
    assert "catalogue" in event.edit_original_response.call_args.kwargs["content"]
    event.followup.send.assert_not_awaited()
    assert not panel.retired


@pytest.mark.asyncio
async def test_missing_enrichment_and_image_do_not_lose_valid_item(panel):
    detail = WikiDetail(
        WikiEntry("item", "1", "Gelano", "Anneau", 60, "/items/gelano", "gelano"),
        {"stats": ["+1 PA"]}, None, False,
    )
    wiki = SimpleNamespace(
        _closed=False,
        client=SimpleNamespace(detail=AsyncMock(return_value=detail)),
        enrichment_client=SimpleNamespace(enrich=AsyncMock(side_effect=TimeoutError)),
        resolve_item_image=AsyncMock(side_effect=TimeoutError),
    )
    panel.cog.bot = SimpleNamespace(get_cog=lambda name: wiki)
    item, image = await panel.cog.load_item(detail.entry)
    assert item.bounds == {"pa": (1, 1)}
    assert image is None


@pytest.mark.asyncio
async def test_actual_components_have_revision_stamps_and_modal_goals_fit(panel):
    assert all(child.custom_id.endswith(":0") for child in panel.children)
    await panel.dispatch(interaction(), "one")
    assert all(child.custom_id.endswith(":1") for child in panel.children)
    limits(panel)



@pytest.mark.asyncio
async def test_explicit_pa_goal_is_checked_against_the_requested_item_not_demo(panel):
    entry = WikiEntry("item", "321", "Anneau test", "Anneau", 50, "/items/test", "anneau test")
    panel.cog.entries = AsyncMock(return_value=(entry,))
    item = Item("Anneau test", "item:321", {"fo": (1, 50)}, "fixture")
    panel.cog.load_item = AsyncMock(return_value=(item, None))
    request = interaction(user_id=43)  # No replacement/timeout of the fixture's existing workshop.
    await ExoCog.exo.callback(panel.cog, request, objet="item:321", objectif="pa")
    opened = panel.cog.views.get((100, 43))
    assert opened is not None
    assert opened.session.item == item
    assert opened.session.requirements == {"pa": 1, "fo": 1}
    assert opened.session.goal_stat == "pa"


@pytest.mark.asyncio
async def test_explicit_goal_survives_ambiguous_item_selection(panel):
    first = WikiEntry("item", "321", "Anneau test A", "Anneau", 50, "/items/a", "anneau test a")
    second = WikiEntry("item", "322", "Anneau test B", "Anneau", 50, "/items/b", "anneau test b")
    panel.cog.entries = AsyncMock(return_value=(first, second))
    item = Item("Anneau test A", "item:321", {"fo": (1, 50)}, "fixture")
    panel.cog.load_item = AsyncMock(return_value=(item, None))
    await ExoCog.exo.callback(panel.cog, interaction(user_id=43), objet="anneau test", objectif="pa")
    opened = panel.cog.views[(100, 43)]
    assert len(opened.search_entries) == 2
    assert opened.search_objective == "pa"
    await opened.dispatch(interaction(user_id=43), "item", "0")
    assert opened.session.goal_stat == "pa"
    assert opened.search_objective is None

@pytest.mark.asyncio
async def test_full_history_budget_is_released_when_panel_stops(panel):
    from utils.fm_retro_limits import history_size
    candidate = copy.deepcopy(panel.session)
    candidate.rune = Rune("pa")
    candidate.sim = State({"pa": 0}, D(1000))
    attempt(candidate.item, candidate.sim, candidate.rune, Rates(1, 0), 1)
    await panel.commit(interaction(), candidate, undo=True)
    assert panel.cog.history_budget.allocations[id(panel)] == (
        history_size(panel.session) + history_size(panel.undo_session)
    )
    panel.stop()
    assert id(panel) not in panel.cog.history_budget.allocations


def configure_build_transfer(panel):
    entry = WikiEntry("item", "321", "Anneau test", "Anneau", 50, "/items/test", "anneau test")
    item = Item("Anneau test", "item:321", {"fo": (1, 50)}, "fixture")
    panel.cog.entries = AsyncMock(return_value=(entry,))
    panel.cog.load_item = AsyncMock(return_value=(item, None))
    return item


@pytest.mark.asyncio
async def test_build_transfer_opens_native_session_with_independent_sink_and_jets(panel):
    item = configure_build_transfer(panel)
    jets = {"fo": 42, "pm": 1}
    opened = await panel.cog.open_build_session(interaction(), "item:321", jets, D("7.5"))
    assert opened is panel.cog.views[(100, 42)]
    assert opened.session.item == item
    assert opened.session.sim.jets == jets
    assert opened.session.sim.sink == D("7.5")
    assert opened.session.observed.jets == jets
    assert opened.session.observed.sink is None
    jets["fo"] = 10
    assert opened.session.sim.jets["fo"] == 42
    assert panel.retired
    assert id(panel) not in panel.cog.history_budget.allocations
    assert id(opened) in panel.cog.history_budget.allocations
    assert not panel.cog.open_locks
    limits(opened)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["entries", "load_item", "publish"])
async def test_build_transfer_failure_keeps_previous_session_and_history_budget(panel, stage):
    configure_build_transfer(panel)
    event = interaction()
    original = copy.deepcopy(panel.session)
    panel.cog.history_budget.reserve(id(panel), 23)
    budget = dict(panel.cog.history_budget.allocations)
    if stage == "publish":
        event.edit_original_response.side_effect = RuntimeError("publication failed")
    else:
        getattr(panel.cog, stage).side_effect = RuntimeError("loading failed")
    with pytest.raises(RuntimeError):
        await panel.cog.open_build_session(event, "item:321", {"fo": 42}, D(0))
    assert panel.cog.views[(100, 42)] is panel
    assert panel.session == original
    assert not panel.retired
    assert panel.cog.history_budget.allocations == budget
    assert not panel.cog.open_locks


@pytest.mark.asyncio
async def test_build_transfer_rejects_invalid_sink_before_touching_previous_session(panel):
    configure_build_transfer(panel)
    with pytest.raises(ValueError):
        await panel.cog.open_build_session(interaction(), "item:321", {"fo": 42}, D(-1))
    panel.cog.entries.assert_not_awaited()
    assert panel.cog.views[(100, 42)] is panel
    assert not panel.retired
    assert not panel.cog.open_locks


@pytest.mark.asyncio
async def test_build_transfer_rejects_invalid_jets_before_publishing(panel):
    configure_build_transfer(panel)
    event = interaction()
    with pytest.raises(ValueError):
        await panel.cog.open_build_session(event, "item:321", {"fo": 10000, "pm": 10}, D(0))
    event.edit_original_response.assert_not_awaited()
    assert panel.cog.views[(100, 42)] is panel
    assert not panel.retired
    assert not panel.cog.open_locks


@pytest.mark.asyncio
async def test_build_transfer_cancelled_loading_keeps_previous_session(panel):
    configure_build_transfer(panel)
    panel.cog.load_item.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await panel.cog.open_build_session(interaction(), "item:321", {"fo": 42}, D(0))
    assert panel.cog.views[(100, 42)] is panel
    assert not panel.retired
    assert not panel.cog.open_locks


@pytest.mark.asyncio
async def test_build_transfer_confirmed_new_session_survives_previous_archive_failure(panel):
    configure_build_transfer(panel)
    panel.message = SimpleNamespace(edit=AsyncMock(side_effect=RuntimeError("archive failed")))
    opened = await panel.cog.open_build_session(interaction(), "item:321", {"fo": 42}, D(0))
    assert panel.cog.views[(100, 42)] is opened
    assert not opened.retired
    assert panel.retired
    assert id(opened) in panel.cog.history_budget.allocations


@pytest.mark.asyncio
async def test_build_transfer_cancelled_previous_archive_keeps_confirmed_new_session(panel):
    configure_build_transfer(panel)
    panel.on_timeout = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await panel.cog.open_build_session(interaction(), "item:321", {"fo": 42}, D(0))
    opened = panel.cog.views[(100, 42)]
    assert opened is not panel and not opened.retired
    assert panel.retired
    assert id(opened) in panel.cog.history_budget.allocations
    assert not panel.cog.open_locks
