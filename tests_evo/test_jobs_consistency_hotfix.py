"""Régressions /evo : même annuaire que /job, cache partiel et absence non prouvée."""
import asyncio
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import aiohttp
import discord
import pytest

from job import JobCog, JobPersistenceError
from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import config, context, jobs_cog, Member, Transport
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoError
from utils.evo_jobs import artisan_question, render_artisans, MAX_MEMBER_FETCHES, MEMBER_CONCURRENCY
from utils.evo_tools import EvoTools, prepared_tools


def directory(data=None):
    ctx = context(config(public_job_data=True))
    cog = jobs_cog(data if data is not None else {
        "101": {"name": "Ancien nom A", "jobs": {"Mineur": 100}},
        "102": {"name": "Ancien nom B", "jobs": {"Mineur": 100}},
        "103": {"name": "Niveau inférieur", "jobs": {"Mineur": 87}},
    })
    ctx.bot.cogs["JobCog"] = cog
    ctx.guild.fetch_member = AsyncMock(side_effect=lambda uid: Member(uid, f"Artisan {uid}"))
    return ctx, cog


def discord_error(cls, status, code):
    return cls(NS(status=status, reason="Synthetic error"), {"code": code, "message": "test"})


@pytest.mark.asyncio
async def test_stale_memory_and_partial_cache_read_fresh_snapshot_and_fetch_only_candidates():
    ctx, cog = directory()
    fresh = copy.deepcopy(cog.jobs_data)
    cog.jobs_data = {}  # Ce que l'ancien outil lisait directement.
    cog.read_jobs_snapshot = AsyncMock(return_value=fresh)
    result = await EvoTools().do_artisans(ctx, "mineurs", 100)
    assert result["total"] == 2 and result["verification_complete"] is True
    assert {row["membre"] for row in result["artisans"]} == {"Artisan 101", "Artisan 102"}
    assert all(row["niveau"] == 100 for row in result["artisans"])
    assert {call.args[0] for call in ctx.guild.fetch_member.await_args_list} == {101, 102}
    cog.read_jobs_snapshot.assert_awaited_once_with(ctx.guild)
    assert cog.jobs_data == {}  # La lecture Evo ne modifie pas le registre.


@pytest.mark.asyncio
async def test_native_search_and_evo_use_same_job_snapshot_service():
    ctx = context(config(public_job_data=True))
    cog = JobCog(ctx.bot)
    cog.initialized = True
    ctx.bot.cogs["JobCog"] = cog
    source = {
        "2": {"name": "Val", "jobs": {"Mineur": 100}},
        "3": {"name": "Alex", "jobs": {"Mineur": 100}},
    }

    async def refresh(guild):
        assert cog._mutation_lock.locked()
        cog.jobs_data = copy.deepcopy(source)
        return True

    cog._load_from_console = AsyncMock(side_effect=refresh)
    cog.read_jobs_snapshot = AsyncMock(wraps=cog.read_jobs_snapshot)
    cog.send_logo_embed = AsyncMock()
    native = NS(author=ctx.member, guild=ctx.guild, channel=ctx.channel)
    await cog._execute_job_command(native, "Mineur")
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert cog.read_jobs_snapshot.await_count == 2
    embed = cog.send_logo_embed.await_args.args[1]
    field = next(field for field in embed.fields if field.name == "Mineur")
    for row in result["artisans"]:
        assert row["membre"] in field.value and str(row["niveau"]) in field.value
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_snapshot_is_isolated_and_refresh_keeps_mutation_lock():
    ctx = context()
    cog = JobCog(ctx.bot)
    original = {"2": {"jobs": {"Mineur": 100}}}

    async def refresh(guild):
        assert cog._mutation_lock.locked()
        cog.jobs_data = copy.deepcopy(original)
        return True

    cog._load_from_console = AsyncMock(side_effect=refresh)
    snapshot = await cog.read_jobs_snapshot(ctx.guild)
    snapshot["2"]["jobs"]["Mineur"] = 1
    assert cog.jobs_data == original


@pytest.mark.asyncio
async def test_uncertain_persistence_is_restored_under_lock_before_reading():
    ctx = context()
    cog = JobCog(ctx.bot)
    cog._remote_uncertain = True
    cog._load_from_console = AsyncMock()

    async def restore(guild):
        assert cog._mutation_lock.locked()
        cog.jobs_data = {"2": {"jobs": {"Mineur": 100}}}
        cog._remote_uncertain = False

    cog._restore_jobs_for_mutation = AsyncMock(side_effect=restore)
    assert (await cog.read_jobs_snapshot(ctx.guild))["2"]["jobs"]["Mineur"] == 100
    cog._restore_jobs_for_mutation.assert_awaited_once_with(ctx.guild)
    cog._load_from_console.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unavailable", "invalid"])
async def test_snapshot_failure_is_not_empty_annuaire(state):
    ctx = context()
    cog = JobCog(ctx.bot)
    cog.jobs_data = [] if state == "invalid" else {}
    cog._load_from_console = AsyncMock(return_value=state != "unavailable")
    with pytest.raises(JobPersistenceError):
        await cog.read_jobs_snapshot(ctx.guild)


@pytest.mark.asyncio
async def test_failed_sync_never_falls_back_to_stale_data_or_empty_result():
    ctx, cog = directory()
    cog.read_jobs_snapshot.side_effect = JobPersistenceError("Source non confirmée")
    result = await EvoTools().execute("artisans", '{"metier":"Mineur","niveau_min":100}', ctx, {"artisans"})
    assert "erreur" in result and "artisans" not in result
    assert "conclure" in render_artisans(result)
    ctx.guild.fetch_member.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    discord_error(discord.Forbidden, 403, 50013),
    discord_error(discord.HTTPException, 500, 0),
    discord_error(discord.NotFound, 404, 10004),
    TimeoutError(),
    OSError(),
    aiohttp.ServerDisconnectedError(),
])
async def test_unverified_members_are_not_declared_absent(error):
    ctx, _ = directory()
    ctx.guild.fetch_member.side_effect = error
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["total"] == 0
    assert result["verification_complete"] is False and result["absence_confirmee"] is False
    assert result["declarations_non_verifiees"] == 2
    assert result["total_declarations"] == 2
    assert {row["nom_declare"] for row in result["declarations_a_verifier"]} == {
        "Ancien nom A", "Ancien nom B",
    }
    assert all("membre" not in row for row in result["declarations_a_verifier"])
    assert "compte Discord non vérifié" in render_artisans(result)


@pytest.mark.asyncio
async def test_only_unknown_member_response_confirms_departure():
    ctx, _ = directory()
    ctx.guild.fetch_member.side_effect = discord_error(discord.NotFound, 404, 10007)
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["total"] == 0 and result["absence_confirmee"] is True


@pytest.mark.asyncio
async def test_empty_synced_registry_is_an_explicit_empty_result():
    ctx, _ = directory({})
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["absence_confirmee"] is True
    ctx.guild.fetch_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_names_and_bad_levels_are_signaled_not_attributed_or_rewritten():
    ctx, cog = directory({
        "ancien_pseudo": {"name": "NOM_NON_VERIFIE", "jobs": {"Mineur": 100}},
        "2": {"jobs": {"Mineur": "100"}},
        "3": {"jobs": {"Mineur": True}},
        "²": {"jobs": {"Mineur": 100}},
        str(2**64): {"jobs": {"Mineur": 100}},
    })
    before = copy.deepcopy(cog.jobs_data)
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["verification_complete"] is False and result["absence_confirmee"] is False
    assert result["lignes_invalides"] == 2 and result["declarations_non_verifiees"] == 3
    assert result["total"] == 0 and result["total_declarations"] == 3
    assert result["declarations_sans_nom"] == 2
    assert result["declarations_a_verifier"][0]["nom_declare"] == "NOM_NON_VERIFIE"
    assert "membre" not in result["declarations_a_verifier"][0]
    assert cog.jobs_data == before
    ctx.guild.fetch_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_bots_are_excluded_even_if_rest_resolves_them():
    ctx, _ = directory({"101": {"jobs": {"Mineur": 100}}})
    ctx.guild.fetch_member.return_value = Member(101, "BOT", bot=True)
    ctx.guild.fetch_member.side_effect = None
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["artisans"] == [] and result["verification_complete"] is True


@pytest.mark.asyncio
async def test_wrong_rest_identity_is_not_accepted():
    ctx, _ = directory()
    ctx.guild.fetch_member.side_effect = lambda uid: Member(999, "Autre membre")
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["total"] == 0 and result["declarations_non_verifiees"] == 2


@pytest.mark.asyncio
async def test_crossguild_rest_identity_is_not_accepted():
    ctx, _ = directory()

    def other_guild(uid):
        member = Member(uid, "Autre serveur")
        member.guild = NS(id=77)
        return member

    ctx.guild.fetch_member.side_effect = other_guild
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["total"] == 0 and not result["verification_complete"]


@pytest.mark.asyncio
async def test_revoked_permissions_after_lookup_prevent_disclosure():
    ctx, _ = directory()

    def revoked(uid):
        ctx.channel.denied.add(ctx.member.id)
        return Member(uid, "Ne pas divulguer")

    ctx.guild.fetch_member.side_effect = revoked
    with pytest.raises(EvoError, match="Permission"):
        await EvoTools().do_artisans(ctx, "Mineur", 100)


@pytest.mark.asyncio
async def test_concurrent_tools_share_snapshot_and_membership_lookups():
    ctx, cog = directory({
        "101": {"jobs": {"Mineur": 100, "Tailleur": 100}},
        "102": {"jobs": {"Mineur": 100}},
    })
    tools = EvoTools()
    artisans, directory_result = await asyncio.gather(
        tools.do_artisans(ctx, "Mineur", 100), tools.do_liste_metiers(ctx),
    )
    assert artisans["total"] == 2 and directory_result["total"] == 2
    cog.read_jobs_snapshot.assert_awaited_once()
    assert ctx.guild.fetch_member.await_count == 2


@pytest.mark.asyncio
async def test_member_fetch_count_and_concurrency_are_bounded_across_tools():
    ctx, _ = directory({str(uid): {"jobs": {"Mineur": 100, "Tailleur": 100}} for uid in range(100, 170)})
    running, peak = 0, 0

    async def fetch(uid):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0)
            return Member(uid, f"Artisan {uid}")
        finally:
            running -= 1

    ctx.guild.fetch_member.side_effect = fetch
    tools = EvoTools()
    first = await tools.do_artisans(ctx, "Mineur", 100)
    second = await tools.do_liste_metiers(ctx)
    assert ctx.guild.fetch_member.await_count == MAX_MEMBER_FETCHES
    assert peak <= MEMBER_CONCURRENCY and running == 0
    assert first["total"] == MAX_MEMBER_FETCHES
    assert first["declarations_non_verifiees"] == 70 - MAX_MEMBER_FETCHES
    assert second["verification_complete"] is False


@pytest.mark.asyncio
async def test_cancelled_request_leaves_no_lookup_running():
    ctx, _ = directory()
    entered = asyncio.Event()
    running = 0

    async def fetch(uid):
        nonlocal running
        running += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            running -= 1

    ctx.guild.fetch_member.side_effect = fetch
    request = asyncio.create_task(EvoTools().do_artisans(ctx, "Mineur", 100))
    await entered.wait()
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert running == 0


@pytest.mark.asyncio
async def test_lookup_deadline_is_partial_and_drains_tasks(monkeypatch):
    import utils.evo_jobs as jobs_module
    monkeypatch.setattr(jobs_module, "LOOKUP_TIMEOUT", 0.01)
    ctx, _ = directory()
    running = 0

    async def fetch(uid):
        nonlocal running
        running += 1
        try:
            await asyncio.Event().wait()
        finally:
            running -= 1

    ctx.guild.fetch_member.side_effect = fetch
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert running == 0 and result["verification_complete"] is False


@pytest.mark.asyncio
async def test_aliases_and_duplicate_member_identifiers_do_not_duplicate_artisans():
    ctx, _ = directory({
        "2": {"jobs": {"Alchi": 100, "ALCHIMISTE": 90}},
        "02": {"jobs": {"Alchimiste": 100}},
    })
    result = await EvoTools().do_artisans(ctx, "alchimistes", 100)
    assert result["artisans"] == [{"membre": "Val", "metier": "Alchimiste", "niveau": 100}]
    assert result["total"] == 1 and result["verification_complete"] is True


@pytest.mark.parametrize("question, metier, level", [
    ("Y a-t-il des mineurs 100 ?", "Mineur", 100),
    ("Il y a des mineurs niveau 100 ?", "Mineur", 100),
    ("Est-ce qu'il y a des mineurs 100 ?", "Mineur", 100),
    ("Des tailleurs 100 ?", "Tailleur", 100),
    ("Qui est mineur ?", "Mineur", 1),
    ("Cherche-moi un mineur niveau 100 dans la guilde stp", "Mineur", 100),
    ("Trouve moi des bûcherons 100", "Bûcheron", 100),
    ("Y a des mineurs lvl 100 ?", "Mineur", 100),
    ("Evo, on a des mineurs 100 ?", "Mineur", 100),
])
def test_simple_artisan_queries_use_authoritative_lookup(question, metier, level):
    assert prepared_tools(question) == [("artisans", {"metier": metier, "niveau_min": level})]


@pytest.mark.parametrize("question", [
    "Ajoute-moi Mineur 100", "Retire mon métier Mineur", "Ne cherche pas de mineur",
    "Des mineurs 100 et crée une activité", "Des mineurs 100 ou des tailleurs ?",
    'Dis « des mineurs 100 »', "Cherche les mineurs sur Internet",
    "Des mineurs exactement niveau 80 ?", "Des mineurs 101 ?", "Des mineurs 0 ?",
    "Comment monter mineur 100 ?", "Des mineurs de moins de 100 ?",
])
def test_compound_ambiguous_mutation_or_unrelated_queries_are_not_short_circuited(question):
    assert artisan_question(question) is None


@pytest.mark.asyncio
async def test_simple_reply_cannot_hallucinate_none_and_makes_no_model_call():
    ctx, cog = directory()
    budget = await create_budget(ctx.config)
    transport = Transport()
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        key = (ctx.guild.id, ctx.channel.id, ctx.member.id)
        agent.sessions.save(key, "Des mineurs 100 ?", "Aucun membre n'a déclaré ce métier.", [], set())
        reply = await agent.answer(ctx, "Y a-t-il des mineurs 100 ?", 700)
        assert "2 artisan(s)" in reply and "Artisan 101" in reply and "Artisan 102" in reply
        assert "Aucun membre" not in reply and transport.calls == []
        assert transport.count_calls == []
        assert (await budget.status())["used_nano"] == 0

        # Le contexte peut être réutilisé par le caller, mais pas son snapshot métier.
        cog.jobs_data = {}
        reply = await agent.answer(ctx, "Des mineurs 100 ?", 701)
        assert "Aucun artisan correspondant" in reply
        assert cog.read_jobs_snapshot.await_count == 2
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_simple_reply_reports_source_failure_without_asking_model():
    ctx, cog = directory()
    cog.read_jobs_snapshot.side_effect = JobPersistenceError("Lecture refusée")
    budget = await create_budget(ctx.config)
    transport = Transport()
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        reply = await agent.answer(ctx, "Des mineurs 100 ?", 702)
        assert "conclure" in reply and transport.calls == []
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_result_truncation_is_explicit_and_names_cannot_inject_markdown_or_mentions():
    ctx, _ = directory({str(uid): {"jobs": {"Mineur": 100}} for uid in range(100, 112)})
    ctx.guild.fetch_member.side_effect = lambda uid: Member(uid, f"{uid} **@everyone** [x](https://evil.test)")
    result = await EvoTools().do_artisans(ctx, "Mineur", 100)
    assert result["total"] == 12 and len(result["artisans"]) == 10
    rendered = render_artisans(result)
    assert "10 sur 12" in rendered and "@everyone" not in rendered
    assert "[x](https://evil.test)" not in rendered
