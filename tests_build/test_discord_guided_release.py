"""Exécutés uniquement avec la véritable discord.py. Aucun jeton ni connexion Discord."""
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import pytest
pytest.importorskip('discord', reason='discord.py absent : tests guidés réels réservés à la CI.')
import discord
from build import BuildCog
from utils.build.models import BuildError, Profile, revised, equip
from utils.build.panels import (BuildListView, CreateModal, ProfileEditorView, CapitalModal, IdentityModal,
                               SlotActionsView, JetsEditorView, JetsModal, ExoModal, HistoryView, DetailsView)
from utils.build.views import ProfileModal, SearchModal
from utils.build.calculator import calculate
from utils.build.embeds import card
from utils.build.sharing import SharedBuildView, SharedBuildAction, PATTERN


def fake_cog(actor, service=None):
    tracked = set()
    return SimpleNamespace(track=tracked.add, untrack=tracked.discard, views=tracked,
        ensure_ready=lambda: None, actor=lambda i, g=None: actor, error=AsyncMock(),
        service=service, repository=SimpleNamespace(durable=False), show_preview=AsyncMock())


@pytest.mark.asyncio
async def test_all_new_widgets_serialize_and_respect_limits(build, actor, catalog, rules):
    cog = fake_cog(actor)
    template = catalog.items[0]
    instance = equip(template)
    build = revised(build, profile=Profile(classe='enutrof', level=200))
    report = calculate(build, catalog, rules)
    widgets = [BuildListView(cog, actor, (build,)), ProfileEditorView(cog, actor, build),
        SlotActionsView(cog, actor, build, catalog, 'coiffe'),
        JetsEditorView(cog, actor, build, template, 'coiffe', instance),
        HistoryView(cog, actor, revised(build, revision=2), (build,)),
        *(DetailsView(cog, actor, build, report, catalog, page=i) for i in range(4))]
    modals = [CreateModal(cog, actor), CapitalModal(cog, actor, build, 'cha'), IdentityModal(cog, actor, build),
              ProfileModal(cog, actor, build), SearchModal(cog, actor, build, 'coiffe'),
              JetsModal(cog, actor, widgets[3]), ExoModal(cog, actor, widgets[3])]
    try:
        for widget in widgets:
            assert len(widget.children) <= 25
            assert len(widget.to_components()) <= 5
            for child in widget.children:
                assert len(child.custom_id or '') <= 100
                if isinstance(child, discord.ui.Select):
                    assert 1 <= len(child.options) <= 25
                    assert len(child.placeholder or '') <= 150
                    assert all(len(o.label) <= 100 and len(o.value) <= 100 for o in child.options)
                if isinstance(child, discord.ui.Button):
                    assert len(child.label or '') <= 80
        for modal in modals:
            assert len(modal.title) <= 45 and 1 <= len(modal.children) <= 5
            assert all(1 <= len(field.label) <= 45 for field in modal.children)
            modal.to_dict()
    finally:
        for widget in (*widgets, *modals): widget.stop()
    assert not cog.views


@pytest.mark.asyncio
async def test_draft_modal_cannot_overwrite_changed_jets(build, actor, catalog):
    cog = fake_cog(actor)
    template = catalog.items[0]
    editor = JetsEditorView(cog, actor, build, template, 'coiffe', equip(template))
    modal = JetsModal(cog, actor, editor)
    editor.draft_revision += 1
    interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    with pytest.raises(BuildError, match='brouillon'):
        await modal.on_submit(interaction)
    interaction.response.edit_message.assert_not_awaited()
    editor.stop(); modal.stop()


@pytest.mark.asyncio
async def test_public_dynamic_items_rehydrate_without_network():
    code = 'A' * 32
    view = SharedBuildView(code)
    assert view.is_persistent() and len(view.children) == 2
    for action in ('voir', 'copier'):
        custom_id = f'evobuild:{action}:{code}'
        restored = await SharedBuildAction.from_custom_id(None, discord.ui.Button(custom_id=custom_id), re.fullmatch(PATTERN, custom_id))
        assert restored.code == code and restored.action == action
    with pytest.raises(ValueError): SharedBuildAction('voir', '../bad')
    view.stop()


@pytest.mark.asyncio
async def test_public_share_revalidates_actor_and_does_not_leak(service, actor, profile):
    from utils.build.models import NotFound, Actor
    saved, _, _ = await service.new(actor, 'secret', profile, operation='create')
    receipt = await service.share(actor, saved, 'share')
    other = Actor(guild_id=999, user_id=actor.user_id)
    cog = fake_cog(other, service)
    cog.repository = service.repository
    interaction = SimpleNamespace(client=SimpleNamespace(get_cog=lambda name: cog),
        response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
    await SharedBuildAction('voir', receipt['code']).callback(interaction)
    cog.error.assert_awaited_once()
    assert isinstance(cog.error.await_args.args[1], NotFound)
    interaction.edit_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_initialize_retry_does_not_duplicate_pool():
    cog = SimpleNamespace(init_lock=__import__('asyncio').Lock(), closed=False, ready=False, storage_open=False,
        config=SimpleNamespace(backend='postgres', dsn='test-not-a-real-dsn'),
        repository=SimpleNamespace(open=AsyncMock(side_effect=[BuildError('indisponible'), None])),
        service=SimpleNamespace(start=AsyncMock()),
        catalogs=SimpleNamespace(restore=AsyncMock(), refresh=AsyncMock()), start_error='')
    await BuildCog.initialize(cog)
    assert not cog.ready and not cog.storage_open
    await BuildCog.initialize(cog)
    await BuildCog.initialize(cog)
    assert cog.ready and cog.storage_open
    assert cog.repository.open.await_count == 2
    assert cog.service.start.await_count == 1
    assert cog.catalogs.refresh.await_count == 2


@pytest.mark.asyncio
async def test_initialize_refresh_failure_keeps_saved_builds_accessible():
    cog = SimpleNamespace(init_lock=__import__('asyncio').Lock(), closed=False, ready=False, storage_open=False,
        config=SimpleNamespace(backend='memory', dsn=''),
        repository=SimpleNamespace(open=AsyncMock()), service=SimpleNamespace(start=AsyncMock()),
        catalogs=SimpleNamespace(restore=AsyncMock(), refresh=AsyncMock(side_effect=BuildError('catalogue absent'))), start_error='')
    await BuildCog.initialize(cog)
    assert cog.ready and cog.start_error == 'catalogue absent'
    cog.repository.open.assert_awaited_once()


@pytest.mark.asyncio
async def test_embed_keeps_last_dofus_and_all_slots(build, catalog, rules):
    embed = card(build, calculate(build, catalog, rules), catalog, False)
    equipment = '\n'.join(field.value for field in embed.fields if field.name.startswith('Équipement'))
    assert 'Dofus 6' in equipment and 'Familier / monture' in equipment
    assert len(embed) <= 6000 and all(len(f.value) <= 1024 for f in embed.fields)
