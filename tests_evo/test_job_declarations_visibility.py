"""Régression : conserver le nom déclaré sans inventer de compte Discord."""
import copy
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest

from job import JobCog
from tests_evo.budget_helpers import create_budget
from tests_evo.helpers import (
    Member, Transport, answer, config, context, function, jobs_cog, response,
)
from utils.evo_agent import EvoAgent, MeteredModel
from utils.evo_config import EvoError
from utils.evo_jobs import render_artisans
from utils.evo_tools import EvoTools


def directory(data, **settings):
    ctx = context(config(public_job_data=True, **settings))
    cog = jobs_cog(data)
    ctx.bot.cogs["JobCog"] = cog
    ctx.bot.cogs["ProfilCog"] = NS(store=NS(get_by_owner=AsyncMock()))
    return ctx, cog


@pytest.mark.asyncio
async def test_screenshot_legacy_tailleur_is_named_without_model_or_identity_guess():
    source = {"ancienne-fiche": {"name": "Couturier-Test", "jobs": {"Tailleur": 100}}}
    ctx, cog = directory(copy.deepcopy(source))
    # Même pseudo dans le cache : ce n'est toujours pas une preuve de propriété.
    ctx.guild.members.append(Member(500, "Couturier-Test"))
    ctx.guild.fetch_member = AsyncMock()
    budget = await create_budget(ctx.config)
    transport = Transport()
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        reply = await agent.answer(ctx, "qui est tailleur level 100", 800)
        assert "**Couturier-Test** : 100" in reply
        assert "compte Discord non vérifié" in reply
        assert "Aucun artisan" not in reply and "0 ligne(s)" not in reply
        assert "<@500>" not in reply
        assert not transport.calls and not transport.count_calls
        assert (await budget.status())["used_nano"] == 0
        ctx.guild.fetch_member.assert_not_awaited()
        ctx.bot.cogs["ProfilCog"].store.get_by_owner.assert_not_awaited()
        assert cog.jobs_data == source
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_native_search_and_evo_both_display_registered_label_with_partial_cache():
    source = {"101": {"name": "Couturier-Test", "jobs": {"Tailleur": 100}}}
    ctx, _ = directory(source)
    cog = JobCog(ctx.bot)
    cog.initialized = True

    async def refresh(guild):
        assert cog._mutation_lock.locked()
        cog.jobs_data = copy.deepcopy(source)
        return True

    cog._load_from_console = AsyncMock(side_effect=refresh)
    cog.send_logo_embed = AsyncMock()
    ctx.bot.cogs["JobCog"] = cog
    ctx.guild.fetch_member = AsyncMock(side_effect=TimeoutError())
    native = NS(author=ctx.member, guild=ctx.guild, channel=ctx.channel)
    await cog._execute_job_command(native, "Tailleur")
    result = await EvoTools().execute(
        "artisans", '{"metier":"Tailleur","niveau_min":100}', ctx, {"artisans"},
    )
    field = next(f for f in cog.send_logo_embed.await_args.args[1].fields if f.name == "Tailleur")
    assert "Couturier-Test" in field.value
    assert result["total"] == 0 and result["total_declarations"] == 1
    assert result["declarations_a_verifier"] == [{
        "nom_declare": "Couturier-Test", "metier": "Tailleur", "niveau": 100,
        "raison": "appartenance_discord_non_confirmee",
    }]
    assert "**Couturier-Test** : 100" in render_artisans(result)
    assert cog.jobs_data == source


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, "", "   ", "\u200b", 123, True, ["pseudo"], {"nom": "pseudo"}])
async def test_missing_or_invalid_name_is_not_replaced_with_a_guessed_member_or_raw_key(name):
    ctx, _ = directory({"CLE_A_NE_PAS_PUBLIER": {"name": name, "jobs": {"Tailleur": 100}}})
    result = await EvoTools().do_artisans(ctx, "Tailleur", 100)
    assert result["total_declarations"] == 1 and result["declarations_sans_nom"] == 1
    assert result["declarations_a_verifier"][0]["nom_declare"] is None
    assert result["absence_confirmee"] is False
    assert "Nom non renseigné" in render_artisans(result)
    assert "CLE_A_NE_PAS_PUBLIER" not in json.dumps(result)


@pytest.mark.asyncio
async def test_verified_and_unverified_names_are_never_mixed_or_double_counted():
    ctx, _ = directory({
        "2": {"name": "Ancien nom privé", "jobs": {"Tailleur": 75}},
        "fiche-A": {"name": "Même pseudo", "jobs": {"Tailleur": 100, "TAILLEUR": 90}},
        "fiche-B": {"name": "Même pseudo", "jobs": {"Tailleur": 85}},
    })
    tools = EvoTools()
    result = await tools.do_artisans(ctx, "Tailleur", 1)
    assert result["artisans"] == [{"membre": "Val", "metier": "Tailleur", "niveau": 75}]
    assert [r["niveau"] for r in result["declarations_a_verifier"]] == [100, 85]
    assert result["total"] == 1 and result["total_declarations"] == 3
    assert "Ancien nom privé" not in json.dumps(result)
    aggregate = await tools.do_liste_metiers(ctx)
    assert aggregate["metiers"] == [{
        "metier": "Tailleur", "nombre_artisans": 1, "nombre_declarations": 3,
        "declarations_non_verifiees": 2, "niveau_max": 100,
    }]
    assert "Même pseudo" not in json.dumps(aggregate)


@pytest.mark.asyncio
async def test_unknown_numeric_owner_is_deduplicated_by_id_not_display_name():
    ctx, _ = directory({
        "101": {"name": "Artisan-Test", "jobs": {"Alchi": 70, "ALCHIMISTE": 100}},
        "0101": {"jobs": {"Alchimiste": 100}},
    })
    result = await EvoTools().do_artisans(ctx, "alchimistes", 1)
    assert result["total"] == 0 and result["total_declarations"] == 1
    assert result["declarations_non_verifiees"] == 1
    assert result["declarations_a_verifier"][0]["nom_declare"] == "Artisan-Test"
    assert result["declarations_a_verifier"][0]["niveau"] == 100


@pytest.mark.asyncio
async def test_only_unverified_jobs_remain_visible_on_every_catalogue_page():
    names = [f"Métier {index:02d} " + "é" * 60 for index in range(23)]
    ctx, cog = directory({"ancienne-fiche": {"name": "NOM_DU_REGISTRE", "jobs": dict.fromkeys(names, 100)}})
    tools = EvoTools()
    found = []
    for page in range(1, 4):
        result = await tools.execute("liste_metiers", json.dumps({"page": page}), ctx, {"liste_metiers"})
        assert result["total"] == 23 and result["pages"] == 3
        assert result["page_suivante"] == (page + 1 if page < 3 else None)
        assert "NOM_DU_REGISTRE" not in json.dumps(result)
        assert all(r["nombre_artisans"] == 0 and r["nombre_declarations"] == 1 for r in result["metiers"])
        found.extend(r["metier"] for r in result["metiers"])
    assert found == names
    cog.read_jobs_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_display_limit_counts_verified_and_pending_rows():
    source = {
        "2": {"jobs": {"Tailleur": 100}},
        "3": {"jobs": {"Tailleur": 90}},
        **{f"fiche-{n}": {"name": f"Artisan-Test-{n}", "jobs": {"Tailleur": 100}} for n in range(12)},
    }
    ctx, _ = directory(source)
    result = await EvoTools().execute(
        "artisans", '{"metier":"Tailleur","niveau_min":1}', ctx, {"artisans"},
    )
    assert result["total"] == 2 and result["total_declarations"] == 14
    assert len(result["artisans"]) == 2 and len(result["declarations_a_verifier"]) == 8
    assert result["declarations_non_verifiees"] == 12
    assert "10 sur 14 déclaration(s)" in render_artisans(result)


@pytest.mark.asyncio
async def test_long_unicode_labels_do_not_silently_drop_page_entries_or_leak_identifiers():
    job = "Métier " + "𐐀" * 90
    ctx, _ = directory({
        f"fiche-{n}": {"name": f"Artisan {n} " + "𐐀" * 90, "jobs": {job: 100}} for n in range(12)
    })
    result = await EvoTools().execute(
        "artisans", json.dumps({"metier": job, "niveau_min": 100}), ctx, {"artisans"},
    )
    assert result["total_declarations"] == 12
    assert len(result["declarations_a_verifier"]) == 10
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 12000
    assert "fiche-" not in json.dumps(result)


@pytest.mark.asyncio
async def test_declared_label_is_data_not_markdown_link_mention_or_secret():
    ctx, _ = directory({
        "ancienne-fiche": {
            "name": "**@everyone** [clic](https://evil.test) sk-abcdefghijklmnopqrstuvwxyz",
            "jobs": {"Tailleur": 100},
        },
    })
    result = await EvoTools().do_artisans(ctx, "Tailleur", 100)
    rendered = render_artisans(result)
    assert "@everyone" not in rendered and "[clic](https://evil.test)" not in rendered
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in json.dumps(result)
    assert not result["artisans"] and result["declarations_a_verifier"]
    assert "compte Discord non vérifié" in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["disabled", "other_guild", "multiple_guilds", "permission"])
async def test_unverified_declared_names_obey_the_same_access_controls(cause):
    ctx, cog = directory({"fiche": {"name": "NE_PAS_DIVULGUER", "jobs": {"Tailleur": 100}}})
    if cause == "disabled":
        ctx.config = config(public_job_data=False, public_member_data=False)
    elif cause == "other_guild":
        ctx.bot.guilds = [NS(id=77)]
    elif cause == "multiple_guilds":
        ctx.bot.guilds.append(NS(id=77))
    else:
        ctx.channel.denied.add(ctx.member.id)
    with pytest.raises(EvoError):
        await EvoTools().do_artisans(ctx, "Tailleur", 100)
    cog.read_jobs_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_departures_and_bots_do_not_reappear_as_unverified_declarations():
    ctx, _ = directory({
        "101": {"name": "DEPART_CONFIRME", "jobs": {"Tailleur": 100}},
        "99": {"name": "BOT", "jobs": {"Tailleur": 100}},
        "fiche": {"name": "DECLARATION_NON_CONFIRMEE", "jobs": {"Tailleur": 100}},
    })
    ctx.guild.fetch_member = AsyncMock(side_effect=discord.NotFound(
        NS(status=404, reason="Not Found"), {"code": 10007, "message": "Unknown Member"},
    ))
    result = await EvoTools().do_artisans(ctx, "Tailleur", 100)
    assert result["total_declarations"] == 1
    assert result["declarations_a_verifier"][0]["nom_declare"] == "DECLARATION_NON_CONFIRMEE"
    assert "DEPART_CONFIRME" not in json.dumps(result)
    assert result["absence_confirmee"] is False


@pytest.mark.asyncio
async def test_model_tool_path_receives_declared_names_and_distinct_evidence_status():
    ctx, _ = directory({"fiche": {"name": "Couturier-Test", "jobs": {"Tailleur": 100}}})
    transport = Transport([
        response([function("artisans", {"metier": "Tailleur", "niveau_min": 100})]),
        answer("Couturier-Test : 100 — déclaration enregistrée, compte Discord non vérifié."),
    ])
    budget = await create_budget(ctx.config)
    try:
        agent = EvoAgent(ctx.config, MeteredModel(ctx.config, budget, transport))
        reply = await agent.answer(ctx, "Pour préparer des crafts, cherche les tailleurs 100.", 801)
        outputs = [
            item for call in transport.calls for item in call["input"]
            if item.get("type") == "function_call_output"
        ]
        data = json.loads(outputs[-1]["output"])
        assert data["artisans"] == [] and data["total"] == 0
        assert data["total_declarations"] == 1
        assert data["declarations_a_verifier"][0]["nom_declare"] == "Couturier-Test"
        assert data["verification_complete"] is False
        assert "Couturier-Test" in reply
    finally:
        await budget.close()


@pytest.mark.asyncio
async def test_revoked_access_during_failed_lookup_does_not_disclose_fallback_name():
    ctx, _ = directory({"101": {"name": "NE_PAS_DIVULGUER", "jobs": {"Tailleur": 100}}})

    async def fail_after_revocation(owner):
        ctx.channel.denied.add(ctx.member.id)
        raise TimeoutError()

    ctx.guild.fetch_member = AsyncMock(side_effect=fail_after_revocation)
    result = await EvoTools().execute(
        "artisans", '{"metier":"Tailleur","niveau_min":100}', ctx, {"artisans"},
    )
    assert "erreur" in result and "NE_PAS_DIVULGUER" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("minimum", [1, 100])
@pytest.mark.parametrize("reverse", [False, True])
async def test_canonical_owner_keeps_known_label_when_highest_level_row_has_no_name(minimum, reverse):
    entries = [
        ("101", {"name": "Artisan-Test", "jobs": {"Mineur": 90}}),
        ("0101", {"jobs": {"Mineur": 100}}),
    ]
    ctx, _ = directory(dict(reversed(entries) if reverse else entries))
    result = await EvoTools().do_artisans(ctx, "Mineur", minimum)
    assert result["total_declarations"] == 1
    assert result["declarations_a_verifier"][0]["nom_declare"] == "Artisan-Test"
    assert result["declarations_a_verifier"][0]["niveau"] == 100
