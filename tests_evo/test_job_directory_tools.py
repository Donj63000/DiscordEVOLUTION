"""Annuaire paginé des métiers déclarés, sans profils privés ni écritures."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests_evo.helpers import config, context
from utils.evo_config import EvoError
from utils.evo_tools import BY_NAME, EvoTools, schemas_for


def directory_context(**settings):
    ctx = context(config(**settings))
    ctx.bot.cogs["JobCog"] = SimpleNamespace(initialized=True, jobs_data={
        "2": {"name": "NOM_PRIVE", "jobs": {"Tailleur": 75, "Bûcheron": 100}},
        "3": {"name": "AUTRE_NOM_PRIVE", "jobs": {"Tailleur": 100}},
    })
    ctx.bot.cogs["ProfilCog"] = SimpleNamespace(store=SimpleNamespace(get_by_owner=AsyncMock()))
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [{"public_job_data": True}, {"public_member_data": True}])
async def test_job_directory_aggregates_actual_jobs_without_names_profiles_or_writes(settings):
    ctx = directory_context(**settings)
    data = ctx.bot.cogs["JobCog"].jobs_data
    before = copy.deepcopy(data)
    result = await EvoTools().execute("liste_metiers", '{"page":1}', ctx, {"liste_metiers"})

    assert result["metiers"] == [
        {"metier": "Bûcheron", "nombre_artisans": 1, "niveau_max": 100},
        {"metier": "Tailleur", "nombre_artisans": 2, "niveau_max": 100},
    ]
    assert (result["total"], result["page"], result["pages"], result["page_suivante"]) == (2, 1, 1, None)
    assert "PRIVE" not in json.dumps(result)
    assert data == before
    ctx.bot.cogs["ProfilCog"].store.get_by_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_job_directory_requires_explicit_opt_in():
    ctx = directory_context()
    with pytest.raises(EvoError, match="pas autorisé"):
        await EvoTools().do_liste_metiers(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("guilds", [[SimpleNamespace(id=7)], [SimpleNamespace(id=1), SimpleNamespace(id=7)]])
async def test_job_directory_never_reads_legacy_data_in_another_or_multiple_guilds(guilds):
    ctx = directory_context(public_job_data=True)
    ctx.bot.guilds = guilds
    with pytest.raises(EvoError, match="pas autorisé"):
        await EvoTools().do_liste_metiers(ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["absent", "initializing", "invalid"])
async def test_job_directory_refuses_missing_uninitialized_or_invalid_data(state):
    ctx = directory_context(public_job_data=True)
    if state == "absent":
        ctx.bot.cogs.pop("JobCog")
    elif state == "initializing":
        ctx.bot.cogs["JobCog"].initialized = False
    else:
        ctx.bot.cogs["JobCog"].jobs_data = []
    with pytest.raises(EvoError):
        await EvoTools().do_liste_metiers(ctx)


@pytest.mark.asyncio
async def test_job_directory_excludes_departed_members_bots_and_invalid_levels():
    ctx = directory_context(public_job_data=True)
    ctx.bot.cogs["JobCog"].jobs_data = {
        "2": {"jobs": {"Tailleur": 75, "tailleur": 80, "Paysan": True, "Mineur": 0,
                       "Alchimiste": 101, "Pêcheur": "100", "Chasseur": 50.5, "": 100}},
        "02": {"jobs": {"TAILLEUR": 90}},
        "3": {"jobs": {"Tailleur": 100}},
        "99": {"jobs": {"Métier du bot": 100}},
        "777": {"jobs": {"Métier du membre parti": 100}},
        "ancien pseudo": {"jobs": {"Métier ancien": 100}},
        "²": {"jobs": {"Identifiant invalide": 100}},
    }
    ctx.guild.members.remove(ctx.guild.get_member(3))
    result = await EvoTools().do_liste_metiers(ctx)
    assert result["metiers"] == [{"metier": "Tailleur", "nombre_artisans": 1, "niveau_max": 90}]


@pytest.mark.asyncio
async def test_job_directory_pages_cover_every_declared_job_without_truncation():
    ctx = directory_context(public_job_data=True)
    names = [f"Métier {index:02d} " + "long " * 15 for index in range(23)]
    ctx.bot.cogs["JobCog"].jobs_data = {"2": {"jobs": {name: 100 for name in reversed(names)}}}
    tools = EvoTools()
    found = []
    for page in range(1, 4):
        result = await tools.execute("liste_metiers", json.dumps({"page": page}), ctx, {"liste_metiers"})
        assert result["total"] == 23
        assert result["page"] == page
        assert result["pages"] == 3
        assert result["page_suivante"] == (page + 1 if page < 3 else None)
        assert len(result["metiers"]) == (10 if page < 3 else 3)
        found.extend(row["metier"] for row in result["metiers"])
    assert found == [name.strip() for name in names]
    with pytest.raises(EvoError, match="3 page"):
        await tools.do_liste_metiers(ctx, 4)


@pytest.mark.asyncio
async def test_empty_job_directory_has_one_explicit_empty_page():
    ctx = directory_context(public_job_data=True)
    ctx.bot.cogs["JobCog"].jobs_data = {}
    result = await EvoTools().do_liste_metiers(ctx)
    assert result["metiers"] == []
    assert (result["total"], result["page"], result["pages"], result["page_suivante"]) == (0, 1, 1, None)


@pytest.mark.parametrize("question", [
    "La liste des métiers", "Qui est tailleur ?", "Quels jobs sont déclarés ?",
    "Liste des artisans", "liste_metiers page 2",
])
def test_job_directory_schema_is_selected_with_job_and_artisan_topics(question):
    schemas = {row["name"]: row for row in schemas_for(question)}
    assert schemas["liste_metiers"] == BY_NAME["liste_metiers"]["schema"]
    assert schemas["liste_metiers"]["parameters"]["required"] == ["page"]
    assert schemas["liste_metiers"]["parameters"]["additionalProperties"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [0, -1, 1.5, True, "1"])
async def test_job_directory_dispatch_rejects_invalid_pages(page):
    result = await EvoTools().execute("liste_metiers", json.dumps({"page": page}),
                                     directory_context(public_job_data=True), {"liste_metiers"})
    assert "erreur" in result
