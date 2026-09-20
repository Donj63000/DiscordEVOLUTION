"""Régressions de l'incident /build : aucun jeton ni accès réseau nécessaire."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from build import BuildCog
from utils.build.catalog import CatalogService, freeze_catalog
from utils.build.models import BuildError, canonical, digest
from utils.build.repository import MemoryRepository
from utils.slash_support import EvolutionCommandTree
from tests_build.test_catalog_tools import source
from tests_build.test_console_repository import discord_store, reopen, MARKER


def fake_interaction(*, deferred=True, ephemeral=True, loading=True):
    response = SimpleNamespace(
        is_done=Mock(return_value=deferred),
        type=(discord.InteractionResponseType.deferred_channel_message if deferred else None),
        send_message=AsyncMock(),
    )
    return SimpleNamespace(
        id=123, extras={}, response=response, is_expired=lambda: False,
        original_response=AsyncMock(return_value=SimpleNamespace(
            flags=SimpleNamespace(ephemeral=ephemeral, loading=loading))),
        edit_original_response=AsyncMock(), followup=SimpleNamespace(send=AsyncMock()),
        command=SimpleNamespace(qualified_name="build creer"),
        data={"name": "build", "options": [{"type": 1, "name": "creer"}]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("deferred,ephemeral,loading", [
    (True, True, True), (True, False, True), (False, True, False), (True, True, False),
])
async def test_build_error_has_one_private_detail_despite_global_dispatch(
    monkeypatch, deferred, ephemeral, loading,
):
    monkeypatch.setenv("BUILD_ENABLED", "true")
    interaction = fake_interaction(deferred=deferred, ephemeral=ephemeral, loading=loading)
    cog = SimpleNamespace()
    cog.error = lambda i, e: BuildCog.error(cog, i, e)
    error = app_commands.CommandInvokeError(
        SimpleNamespace(name="creer"), BuildError("Catalogue indisponible : détail local"),
    )
    # Ordre réel de discord.py : handlers locaux, puis CommandTree.on_error.
    await BuildCog.cog_app_command_error(cog, interaction, error)
    await EvolutionCommandTree.on_error(SimpleNamespace(), interaction, error)
    sent = list(interaction.response.send_message.await_args_list)
    sent += list(interaction.followup.send.await_args_list)
    sent += list(interaction.edit_original_response.await_args_list)
    contents = [call.kwargs.get("content", call.args[0] if call.args else "") for call in sent]
    assert sum("détail local" in content for content in contents) == 1
    assert not any("La commande a rencontré une erreur" in content for content in contents)
    if deferred and loading and ephemeral:
        interaction.edit_original_response.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
    if deferred and loading and not ephemeral:
        assert "détail local" not in interaction.edit_original_response.await_args.kwargs["content"]
    assert interaction.extras["evolution_error_handled"] is True


@pytest.mark.asyncio
async def test_global_error_still_handles_unhandled_commands():
    interaction = fake_interaction(deferred=False)
    interaction.command.qualified_name = "autre"
    interaction.data = {"name": "autre"}
    await EvolutionCommandTree.on_error(SimpleNamespace(), interaction, RuntimeError("secret"))
    interaction.response.send_message.assert_awaited_once()
    assert "secret" not in str(interaction.response.send_message.await_args)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_expired_local_error_does_not_raise_or_claim_delivery():
    interaction = fake_interaction()
    interaction.is_expired = lambda: True
    await BuildCog.error(SimpleNamespace(), interaction, BuildError("Catalogue indisponible"))
    interaction.followup.send.assert_not_awaited()
    assert not interaction.extras.get("evolution_error_handled")


@pytest.mark.asyncio
async def test_startup_retries_catalog_even_when_storage_is_ready(monkeypatch):
    catalogs = SimpleNamespace(latest=None)
    cog = SimpleNamespace(
        bot=SimpleNamespace(wait_until_ready=AsyncMock()), closed=False,
        ready=True, catalogs=catalogs, start_error="catalogue indisponible",
    )
    calls = []
    async def initialize():
        calls.append(1)
        if len(calls) == 2:
            catalogs.latest = object()
    cog.initialize = initialize
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    await BuildCog.start_when_ready(cog)
    assert len(calls) == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_keeps_restored_catalog_available_without_infinite_retry(monkeypatch):
    cog = SimpleNamespace(
        bot=SimpleNamespace(wait_until_ready=AsyncMock()), closed=False,
        ready=True, catalogs=SimpleNamespace(latest=object()),
        initialize=AsyncMock(), start_error="actualisation distante indisponible",
    )
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    await BuildCog.start_when_ready(cog)
    cog.initialize.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_restore_runs_before_any_snapshot_write():
    calls = []
    async def record(value):
        calls.append(value)
    cog = SimpleNamespace(
        init_lock=asyncio.Lock(), closed=False, ready=False, storage_open=False, start_error="",
        repository=SimpleNamespace(open=lambda: record("open")),
        service=SimpleNamespace(start=lambda: record("rules")),
        catalogs=SimpleNamespace(restore=lambda: record("restore"), refresh=lambda: record("refresh")),
    )
    await BuildCog.initialize(cog)
    assert calls == ["open", "restore", "rules", "refresh"]
    assert cog.ready


def catalog_service(tmp_path, repository):
    entries, payload = source()
    wiki = SimpleNamespace(
        client=SimpleNamespace(items=AsyncMock(return_value=entries)),
        enrichment_client=SimpleNamespace(enabled=True, catalog=AsyncMock(return_value=payload)),
    )
    overrides = tmp_path / "overrides.json"
    overrides.write_text('{"schema_version":1,"items":{},"sets":[]}', encoding="utf-8")
    return CatalogService(repository, lambda: wiki, overrides), wiki


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [BuildError("Console temporairement indisponible"), asyncio.CancelledError()])
async def test_retry_reuses_exact_pending_catalog_without_new_source_request(tmp_path, failure):
    repository = MemoryRepository()
    service, wiki = catalog_service(tmp_path, repository)
    put = repository.put_snapshot
    attempts = []
    async def flaky(kind, identifier, payload):
        attempts.append((kind, identifier, payload))
        if len(attempts) == 1:
            raise failure
        await put(kind, identifier, payload)
    repository.put_snapshot = flaky
    with pytest.raises(type(failure)):
        await service.refresh()
    assert service.latest is None
    wiki.client.items.side_effect = AssertionError("La reprise ne doit pas changer de catalogue")
    wiki.enrichment_client.catalog.side_effect = AssertionError("Aucun nouvel appel source")
    result = await service.refresh()
    assert result is service.latest
    assert attempts[0] == attempts[1]
    assert await repository.latest_snapshot("catalog") == canonical(result)
    assert not service.last_error
    assert service._pending_candidate is None


@pytest.mark.asyncio
async def test_failed_archival_keeps_old_catalog_until_commit(tmp_path, catalog):
    repository = MemoryRepository()
    await repository.put_snapshot("catalog", catalog.id, canonical(catalog))
    service, _ = catalog_service(tmp_path, repository)
    await service.restore()
    repository.put_snapshot = AsyncMock(side_effect=BuildError("Console indisponible"))
    assert await service.refresh() == catalog
    assert service.latest == catalog
    assert service.last_error
    assert service._pending_candidate is not None


@pytest.mark.asyncio
async def test_catalog_diagnostic_identifies_phase_and_redacts_exception_values(tmp_path):
    repository = MemoryRepository()
    service, _ = catalog_service(tmp_path, repository)
    repository.put_snapshot = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), {"code": 50013, "message": "SECRET_NEVER_LOG"}))
    with pytest.raises(BuildError):
        await service.refresh()
    assert "archivage_console" in service.last_error
    assert "403" in service.last_error
    assert "50013" in service.last_error
    assert "SECRET_NEVER_LOG" not in service.last_error


@pytest.mark.asyncio
async def test_source_failure_identifies_source_not_console(tmp_path):
    from utils.dofus_wiki import WikiError
    service, wiki = catalog_service(tmp_path, MemoryRepository())
    wiki.client.items.side_effect = WikiError("https://secret.invalid?key=SECRET_NEVER_LOG")
    with pytest.raises(BuildError):
        await service.refresh()
    assert "sources" in service.last_error and "WikiError" in service.last_error
    assert "SECRET_NEVER_LOG" not in service.last_error


@pytest.mark.asyncio
async def test_restore_recovers_complete_catalog_without_wiki_or_reupload(discord_store, catalog, rules):
    repository = await reopen(discord_store)
    await repository.put_snapshot("rules", rules.id, canonical(rules))
    descriptor = await repository._write_blob(canonical(catalog).encode("utf-8"))
    assert "catalog" not in repository._root["latest"]
    before = len(discord_store[1].sent)
    restarted = await reopen(discord_store)
    service = CatalogService(restarted, lambda: None)
    assert await service.restore() == catalog
    assert len(discord_store[1].sent) == before
    assert restarted._root["snapshots"]["catalog"][catalog.id] == descriptor
    assert await restarted.latest_snapshot("rules") == canonical(rules)
    again = await reopen(discord_store)
    assert await again.latest_snapshot("catalog") == canonical(catalog)


@pytest.mark.asyncio
async def test_recovery_preserves_existing_catalog_and_does_not_select_orphan(discord_store, catalog):
    repository = await reopen(discord_store)
    await repository.put_snapshot("catalog", catalog.id, canonical(catalog))
    newer = freeze_catalog(catalog.items, diagnostics=("Autre version synthétique",))
    orphan = await repository._write_blob(canonical(newer).encode())
    root_before = deepcopy(repository._root)
    service = CatalogService(repository, lambda: None)
    assert await service.restore() == catalog
    assert repository._root == root_before
    assert all(p["id"] in discord_store[1].messages for p in orphan["parts"])


@pytest.mark.asyncio
async def test_recovery_never_guesses_between_two_complete_catalogs(discord_store, catalog):
    repository = await reopen(discord_store)
    for candidate in (catalog, freeze_catalog(catalog.items, diagnostics=("autre",))):
        await repository._write_blob(canonical(candidate).encode())
    root_before = deepcopy(repository._root)
    with pytest.raises(BuildError, match="[Pp]lusieurs"):
        await CatalogService(repository, lambda: None).restore()
    assert repository._root == root_before
    assert not discord_store[1].deleted


@pytest.mark.asyncio
async def test_recovery_rejects_corrupted_catalog_hash_without_deleting_fragments(discord_store, catalog):
    repository = await reopen(discord_store)
    payload = catalog.model_dump(mode="json")
    payload["id"] = "0" * 64
    descriptor = await repository._write_blob(canonical(payload).encode())
    with pytest.raises(BuildError):
        await CatalogService(repository, lambda: None).restore()
    assert "catalog" not in repository._root["latest"]
    assert all(part["id"] in discord_store[1].messages for part in descriptor["parts"])


@pytest.mark.asyncio
async def test_recovery_ignores_non_catalog_complete_upload(discord_store, rules):
    repository = await reopen(discord_store)
    descriptor = await repository._write_blob(canonical(rules).encode())
    assert await CatalogService(repository, lambda: None).restore() is None
    assert "catalog" not in repository._root["latest"]
    assert all(part["id"] in discord_store[1].messages for part in descriptor["parts"])


@pytest.mark.asyncio
async def test_recovery_does_not_publish_partial_upload(discord_store, catalog):
    channel = discord_store[1]
    channel.guild.filesize_limit = 2048
    repository = await reopen(discord_store)
    catalog = freeze_catalog(catalog.items, diagnostics=("synthetic padding" * 10,) * 30)
    assert len(canonical(catalog).encode()) > channel.guild.filesize_limit
    send = channel.send
    async def interrupted(content, **kwargs):
        result = await send(content, **kwargs)
        if content.startswith(f"{MARKER} blob "):
            raise asyncio.CancelledError()
        return result
    channel.send = interrupted
    with pytest.raises(asyncio.CancelledError):
        await repository._write_blob(canonical(catalog).encode())
    channel.send = send
    restarted = await reopen(discord_store)
    assert await CatalogService(restarted, lambda: None).restore() is None
    assert "catalog" not in restarted._root["latest"]


@pytest.mark.asyncio
async def test_inventory_has_separate_total_and_per_request_budgets(discord_store, monkeypatch):
    import utils.build.console_repository as storage
    channel = discord_store[1]
    channel.guild.filesize_limit = 2048
    repository = await reopen(discord_store)
    descriptor = await repository._write_blob(b"x" * 3000)
    for part in descriptor["parts"]:
        item = channel.messages[part["id"]].attachments[0]
        data = item.data
        async def slow_read(raw=data):
            await asyncio.sleep(0.04)
            return raw
        item.read = slow_read
    monkeypatch.setattr(storage, "IO_TIMEOUT", 0.065)
    inventory, journals = await repository._upload_inventory()
    assert len(inventory) == 2 and len(journals) == 1


@pytest.mark.asyncio
async def test_log_size_upload_is_not_an_exceeded_discord_file_limit(discord_store):
    repository = await reopen(discord_store)
    raw = "x" * 7624918  # Taille exacte du catalogue annoncée dans l'extrait, données synthétiques.
    await repository.put_snapshot("attacks", digest(raw), raw)
    descriptor = repository._root["snapshots"]["attacks"][digest(raw)]
    assert [part["size"] for part in descriptor["parts"]] == [4194304, 3430614]
    assert await repository.latest_snapshot("attacks") == raw


@pytest.mark.asyncio
async def test_failed_local_delivery_leaves_global_fallback_enabled(monkeypatch):
    import build as module

    interaction = fake_interaction(deferred=False)
    monkeypatch.setattr(module, "send_interaction_error", AsyncMock(return_value=False))
    error = app_commands.CommandInvokeError(
        SimpleNamespace(name="creer"), BuildError("Catalogue indisponible"),
    )
    await BuildCog.error(SimpleNamespace(), interaction, error)
    assert not interaction.extras.get("evolution_error_handled")
    await EvolutionCommandTree.on_error(SimpleNamespace(), interaction, error)
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_startup_backoff_is_capped_and_stops_after_catalog_commit(monkeypatch):
    cog = SimpleNamespace(
        bot=SimpleNamespace(wait_until_ready=AsyncMock()), closed=False, ready=True,
        catalogs=SimpleNamespace(latest=None), start_error="archivage indisponible",
    )
    calls = 0
    async def initialize():
        nonlocal calls
        calls += 1
        if calls == 8:
            cog.catalogs.latest = object()
    cog.initialize = initialize
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    await BuildCog.start_when_ready(cog)
    assert [call.args[0] for call in sleep.await_args_list] == [30, 60, 120, 240, 300, 300, 300]
    assert cog.startup_attempts == 8
    assert cog.next_retry_at == 0


@pytest.mark.asyncio
async def test_startup_cancellation_is_not_retried(monkeypatch):
    cog = SimpleNamespace(
        bot=SimpleNamespace(wait_until_ready=AsyncMock()), closed=False, ready=True,
        catalogs=SimpleNamespace(latest=None), start_error="",
        initialize=AsyncMock(side_effect=asyncio.CancelledError()),
    )
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await BuildCog.start_when_ready(cog)
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_console_discovery_supports_current_pins_async_iterator(discord_store):
    repository = await reopen(discord_store)
    channel = discord_store[1]
    root_id = repository._root_message.id
    async def pins():
        for message in list(channel.messages.values()):
            if message.pinned:
                yield message
    channel.pins = pins
    restarted = await reopen(discord_store)
    assert restarted._root_message.id == root_id
    assert len(channel.messages) == 1


@pytest.mark.asyncio
async def test_root_write_forbidden_keeps_phase_code_and_retryable_identical_candidate(
    discord_store, tmp_path, caplog,
):
    repository = await reopen(discord_store)
    channel = discord_store[1]
    service, wiki = catalog_service(tmp_path, repository)
    edit = repository._root_message.edit
    repository._root_message.edit = AsyncMock(side_effect=discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"code": 50013, "message": "SECRET_HTTP_BODY"},
    ))
    with pytest.raises(BuildError):
        await service.refresh()
    assert service.latest is None
    assert "archivage_console" in service.last_error
    assert "HTTP 403" in service.last_error and "50013" in service.last_error
    assert "SECRET_HTTP_BODY" not in service.last_error
    assert "SECRET_HTTP_BODY" not in caplog.text
    assert "HTTP 403" in caplog.text
    assert "catalog" not in repository._root["latest"]
    candidate = service._pending_candidate
    before = len(channel.sent)
    repository._root_message.edit = edit
    wiki.client.items.side_effect = AssertionError("La source ne doit pas être rechargée")
    assert await service.refresh() is candidate
    assert len(channel.sent) == before
    assert await repository.latest_snapshot("catalog") == canonical(candidate)


@pytest.mark.asyncio
async def test_recovery_refuses_missing_active_pointer_when_archives_exist(discord_store, catalog):
    repository = await reopen(discord_store)
    await repository.put_snapshot("catalog", catalog.id, canonical(catalog))
    target = deepcopy(repository._root)
    target["latest"].pop("catalog")
    await repository._commit_root(target)
    orphan = freeze_catalog(catalog.items, diagnostics=("autre candidat",))
    descriptor = await repository._write_blob(canonical(orphan).encode())
    before = deepcopy(repository._root)
    with pytest.raises(BuildError, match="sans version active"):
        await CatalogService(repository, lambda: None).restore()
    assert repository._root == before
    assert all(p["id"] in discord_store[1].messages for p in descriptor["parts"])


@pytest.mark.asyncio
async def test_recovery_checks_leadership_and_keeps_fragments_on_refusal(discord_store, catalog):
    repository = await reopen(discord_store)
    descriptor = await repository._write_blob(canonical(catalog).encode())
    before = deepcopy(repository._root)
    discord_store[2].allowed = False
    with pytest.raises(BuildError, match="Leadership"):
        await CatalogService(repository, lambda: None).restore()
    assert repository._root == before
    assert all(p["id"] in discord_store[1].messages for p in descriptor["parts"])


def test_sanitized_trace_omits_messages_values_and_source_lines(caplog):
    import logging
    from utils.build.diagnostics import log_failure

    try:
        try:
            raise ValueError("SECRET_SOURCE_AND_VALUE")
        except ValueError as original:
            raise BuildError("SECRET_WRAPPED_MESSAGE") from original
    except BuildError as error:
        log_failure(logging.getLogger("build_test"), "test diagnostic", error)
    assert "BuildError <- ValueError" in caplog.text
    assert "test_sanitized_trace_omits_messages_values_and_source_lines" in caplog.text
    assert "SECRET_SOURCE_AND_VALUE" not in caplog.text
    assert "SECRET_WRAPPED_MESSAGE" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_real_discord_tree_dispatch_creer_after_console_recovery_or_missing_catalog(
    discord_store, catalog, actor, monkeypatch, recover,
):
    """Vraie résolution/validation/chaîne d'erreurs discord.py ; transport simulé."""
    from discord.ext import commands
    from utils.build.service import BuildService

    monkeypatch.setenv("BUILD_ENABLED", "true")
    monkeypatch.setattr(BuildCog, "cog_load", AsyncMock())
    bot = commands.Bot(
        command_prefix="!", intents=discord.Intents.none(), tree_cls=EvolutionCommandTree,
    )
    member = SimpleNamespace(id=actor.user_id, bot=False)
    guild = SimpleNamespace(id=actor.guild_id, get_member=lambda identifier: member)
    monkeypatch.setattr(bot, "get_guild", lambda identifier: guild)
    monkeypatch.setattr(bot, "dispatch", Mock())
    repository = await reopen(discord_store)
    if recover:
        await repository._write_blob(canonical(catalog).encode())
        repository = await reopen(discord_store)
    cog = BuildCog(bot)
    cog.repository = repository
    cog.catalogs = CatalogService(repository, lambda: None)
    cog.service = BuildService(repository, cog.catalogs, cog.rules)
    # Même séquence que le démarrage, sans actualisation réseau.
    await cog.initialize(refresh_equipment=False)
    cog.show = AsyncMock()
    try:
        await bot.add_cog(cog)
        interaction = fake_interaction(deferred=False)
        interaction.guild, interaction.guild_id = guild, guild.id
        interaction.user = member
        interaction._state = bot._connection
        interaction.type = discord.InteractionType.application_command
        interaction.command_failed = False
        interaction.data["options"][0]["options"] = [
            {"type": 3, "name": "nom", "value": "Build de recette synthétique"},
            {"type": 3, "name": "classe", "value": "enutrof"},
            {"type": 4, "name": "niveau", "value": 200},
        ]
        async def defer(**kwargs):
            interaction.response.is_done.return_value = True
            interaction.response.type = discord.InteractionResponseType.deferred_channel_message
        interaction.response.defer = AsyncMock(side_effect=defer)

        # Ne pas appeler les handlers à la main : utiliser le dispatcher de discord.py.
        await bot.tree._call(interaction)
        interaction.response.defer.assert_awaited_once()
        if recover:
            assert not interaction.command_failed
            cog.show.assert_awaited_once()
            builds = await repository.list(actor)
            assert len(builds) == 1
            assert builds[0].catalog_id == catalog.id
            assert builds[0].name == "Build de recette synthétique"
            assert not interaction.extras.get("evolution_error_handled")
            again = await reopen(discord_store)
            assert (await again.list(actor))[0] == builds[0]
        else:
            assert interaction.command_failed
            cog.show.assert_not_awaited()
            interaction.edit_original_response.assert_awaited_once()
            interaction.followup.send.assert_not_awaited()
            assert "Catalogue" in interaction.edit_original_response.await_args.kwargs["content"]
            assert interaction.extras["evolution_error_handled"]
            assert await repository.list(actor) == ()
    finally:
        await bot.close()
