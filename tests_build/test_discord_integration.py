"""Utilise la VRAIE discord.py, sans connexion ni token. Sinon tests ignorés."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
pytest.importorskip("discord", reason="discord.py absent ; tests de transport à lancer dans la CI.")
import discord
from discord import app_commands
from build import BuildCog
from utils.build.views import BuildView, ConfirmView, ProfileModal, OwnerView
from utils.build.embeds import card
from utils.build.calculator import calculate
from utils.build.ai_tools import NAMES
from utils.evo_tools import BY_NAME, schemas_for


def test_real_command_tree_serializes():
    client=discord.Client(intents=discord.Intents.none())
    tree=app_commands.CommandTree(client)
    tree.add_command(BuildCog.group)
    data=BuildCog.group.to_dict(tree)
    assert data["name"]=="build" and len(data["options"])==25
    for command in data["options"]:
        assert 1<=len(command["description"])<=100
        assert len(command.get("options",[]))<=25


@pytest.mark.asyncio
async def test_real_views_limits_and_modal_response(build,actor,monkeypatch):
    tracked=set()
    cog=SimpleNamespace(track=tracked.add,untrack=tracked.discard,ensure_ready=lambda:None,
                        actor=lambda i,g=None: actor,error=AsyncMock())
    view=BuildView(cog,actor,build)
    assert len(view.children)<=25 and view in tracked
    interaction=SimpleNamespace(response=SimpleNamespace(send_modal=AsyncMock(),defer=AsyncMock()))
    await view.profile.callback(interaction)
    interaction.response.send_modal.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()
    modal=interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal,ProfileModal) and len(modal.children)==5
    assert all(len(child.label)<=45 for child in modal.children)
    view.stop();modal.stop()
    assert view not in tracked


@pytest.mark.asyncio
async def test_view_denies_other_owner(build,actor):
    from utils.build.models import Actor
    cog=SimpleNamespace(track=lambda v:None,untrack=lambda v:None,ensure_ready=lambda:None,
                        actor=lambda i,g=None: Actor(guild_id=actor.guild_id,user_id=actor.user_id+1),error=AsyncMock())
    view=OwnerView(cog,actor)
    interaction=SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    assert await view.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once()
    view.stop()


def test_real_embed_limits(build,catalog,rules):
    embed=card(build,calculate(build,catalog,rules),catalog,False)
    assert len(embed)<=6000 and len(embed.fields)<=25
    assert all(len(f.value)<=1024 for f in embed.fields)


def test_ai_schemas_disabled_by_default_and_have_no_owner_override(monkeypatch):
    monkeypatch.setenv("BUILD_ENABLED","false");monkeypatch.setenv("BUILD_AI_ENABLED","false")
    assert not ({s["name"] for s in schemas_for("mon build")} & NAMES)
    monkeypatch.setenv("BUILD_ENABLED","true");monkeypatch.setenv("BUILD_AI_ENABLED","true")
    selected={s["name"] for s in schemas_for("mon build")}
    assert NAMES<=selected
    for name in NAMES:
        spec=BY_NAME[name]["schema"]
        assert spec["strict"] and spec["parameters"]["additionalProperties"] is False
        assert not ({"owner_id","guild_id","user_id"} & spec["parameters"]["properties"].keys())


@pytest.mark.asyncio
async def test_ai_draft_waits_for_owner_confirmation(service, actor, profile, monkeypatch):
    from utils.build.ai_tools import dispatch

    monkeypatch.setenv("BUILD_ENABLED", "true")
    monkeypatch.setenv("BUILD_AI_ENABLED", "true")
    cog = SimpleNamespace(ensure_ready=lambda: None, service=service, dm_preview=AsyncMock())
    member = SimpleNamespace(id=actor.user_id)
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=actor.guild_id), member=member,
        bot=SimpleNamespace(get_cog=lambda name: cog), ensure_access=AsyncMock(),
    )

    result = await dispatch(
        "build_creer_brouillon", ctx, nom="Build privé", classe=profile.classe,
        niveau=profile.level,
    )

    ctx.ensure_access.assert_awaited_once()
    assert await service.repository.list(actor) == ()
    cog.dm_preview.assert_awaited_once()
    recipient, owner, preview = cog.dm_preview.await_args.args
    assert recipient is member and owner == actor
    assert preview.candidate.owner_id == actor.user_id
    assert preview.candidate.guild_id == actor.guild_id
    assert result["modification_effectuee"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("dm_open", [True, False])
async def test_ai_private_list_never_falls_back_to_public_channel(
    service, actor, profile, monkeypatch, dm_open,
):
    from unittest.mock import Mock
    from utils.build.ai_tools import dispatch
    from utils.evo_config import EvoError

    monkeypatch.setenv("BUILD_ENABLED", "true")
    monkeypatch.setenv("BUILD_AI_ENABLED", "true")
    saved, _, _ = await service.new(actor, "Nom confidentiel", profile, operation="create")
    member = SimpleNamespace(id=actor.user_id, send=AsyncMock())
    if not dm_open:
        member.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Cannot send messages",
        )
    cog = SimpleNamespace(
        ensure_ready=lambda: None, repository=service.repository, remember=Mock(),
    )
    ctx = SimpleNamespace(
        guild=SimpleNamespace(id=actor.guild_id), member=member,
        bot=SimpleNamespace(get_cog=lambda name: cog), ensure_access=AsyncMock(),
        channel=SimpleNamespace(send=AsyncMock()),
    )

    if dm_open:
        result = await dispatch("build_lister", ctx)
        assert saved.name not in str(result) and saved.id not in str(result)
        assert result["contenu_prive_non_expose"] is True
    else:
        with pytest.raises(EvoError, match="message privé") as exc:
            await dispatch("build_lister", ctx)
        assert saved.name not in str(exc.value)
    member.send.assert_awaited_once()
    attachment = member.send.await_args.kwargs["file"]
    try:
        assert saved.name in attachment.fp.read().decode("utf-8")
    finally:
        attachment.close()
    ctx.channel.send.assert_not_awaited()
