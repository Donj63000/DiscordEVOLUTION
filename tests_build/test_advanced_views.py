"""Recette hors réseau des parcours privés et des limites Discord."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from build import BuildCog
from utils.build.advanced_views import (
    SimulatorView, SpellSearchModal, TargetModal, AdvancedOptimizerView,
    OptimizerModal, BudgetModal, PriceBookView, PriceModal, PriceListView,
    ExoSinkModal, ExoTransferView, parse_resistances, result_text,
)
from utils.build.calculator import calculate
from utils.build.combat import AttackLine, freeze_attack, simulate_attack, CombatScenario
from utils.build.models import equip, BuildError, canonical, revised
from utils.build.prices import quote_for
from utils.build.ai_tools import dispatch


@pytest.fixture
def cog(actor, build, catalog, rules):
    tracked = set()
    return SimpleNamespace(track=tracked.add, untrack=tracked.discard, ensure_ready=lambda: None,
        actor=lambda i, g=None: actor, error=AsyncMock(), send_view=AsyncMock(),
        service=SimpleNamespace(inspect=AsyncMock(return_value=(build, calculate(build, catalog, rules), catalog))),
        repository=SimpleNamespace(set_price=AsyncMock(), delete_price=AsyncMock()), tracked=tracked)


@pytest.mark.asyncio
async def test_all_new_widgets_fit_discord(cog, actor, build, catalog):
    build = build.with_slot("coiffe", equip(catalog.items[0]))
    sim = SimulatorView(cog, actor, build)
    optimizer = AdvancedOptimizerView(cog, actor, build)
    book = PriceBookView(cog, actor, build, catalog)
    quote = quote_for(build.in_slot("coiffe"), catalog.items[0], "Serveur", 123)
    output = {"reference": "item:1", "jets_declares": {"fo": 30}}
    views = [sim, optimizer, book, PriceListView(cog, actor, [quote]), ExoTransferView(cog, actor, output)]
    modals = [SpellSearchModal(sim), TargetModal(sim), OptimizerModal(optimizer), BudgetModal(optimizer),
              PriceModal(book, "coiffe"), ExoSinkModal(cog, actor, output)]
    for view in views:
        rows = view.to_components()
        assert len(rows) <= 5
        for row in rows:
            assert len(row["components"]) <= 5
            for component in row["components"]:
                assert len(component.get("options", [])) <= 25
        view.stop()
    for modal in modals:
        assert len(modal.children) <= 5
        assert all(len(child.label) <= 45 for child in modal.children)
        modal.stop()
    assert not cog.tracked


def test_target_resistances_preserve_pvp_distinction():
    rows = parse_resistances("terre 12 25 3 4\nfeu 0 -10")
    assert rows[0].flat == 12 and rows[0].pvp_flat == 3 and rows[0].pvp_percent == 4
    assert rows[1].percent == -10
    with pytest.raises(BuildError):
        parse_resistances("terre 20")


def test_unsupported_attack_reason_visible(build, catalog, rules):
    attack = freeze_attack(id="spell:1", name="Effet spécial", normal_lines=(AttackLine(element="te", minimum=1, maximum=2),),
        unsupported_effects=("Repousse la cible",), critical_denominator=0)
    result = simulate_attack(calculate(build, catalog, rules), attack, CombatScenario())
    text = result_text(result)
    assert "Repousse" in text and "non calculable" in text and "Xixou" in text
    assert canonical({"result": result, "attack": attack})


@pytest.mark.asyncio
async def test_price_saved_only_after_explicit_confirmation(cog, actor, build, catalog):
    build = build.with_slot("coiffe", equip(catalog.items[0]))
    view = PriceBookView(cog, actor, build, catalog)
    modal = PriceModal(view, "coiffe")
    modal.server._value = "Test"
    modal.amount._value = "12345"
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), user=SimpleNamespace(id=actor.user_id))
    await modal.on_submit(interaction)
    cog.repository.set_price.assert_not_awaited()
    assert "Force: 30" in cog.send_view.await_args.kwargs["content"]
    confirm = cog.send_view.await_args.kwargs["view"]
    interaction.edit_original_response = AsyncMock()
    await confirm.confirm.callback(interaction)
    cog.repository.set_price.assert_awaited_once()
    assert cog.repository.set_price.await_args.args[1]["kamas"] == 12345
    for item in list(cog.tracked):
        item.stop()


@pytest.mark.asyncio
async def test_damage_without_manual_values_opens_simulator(cog, actor, build):
    cog.begin = AsyncMock(return_value=actor)
    interaction = SimpleNamespace()
    await BuildCog.damage.callback(cog, interaction, build.id)
    assert isinstance(cog.send_view.await_args.kwargs["view"], SimulatorView)
    with pytest.raises(BuildError, match="ensemble"):
        await BuildCog.damage.callback(cog, interaction, build.id, element="te")
    for item in list(cog.tracked):
        item.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["build_simulateur", "build_prix", "build_optimisation_avancee"])
async def test_luna_opens_private_workflows_without_mutation(cog, actor, build, monkeypatch, name):
    monkeypatch.setenv("BUILD_ENABLED", "true")
    monkeypatch.setenv("BUILD_AI_ENABLED", "true")
    member = SimpleNamespace(id=actor.user_id, send=AsyncMock())
    ctx = SimpleNamespace(ensure_access=AsyncMock(), guild=SimpleNamespace(id=actor.guild_id),
        member=member, bot=SimpleNamespace(get_cog=lambda n: cog))
    result = await dispatch(name, ctx, build=build.id)
    member.send.assert_awaited_once()
    assert result["contenu_prive_non_expose"] and not result["modification_effectuee"]
    cog.repository.set_price.assert_not_awaited()
    assert "view" in member.send.await_args.kwargs
    for item in list(cog.tracked):
        item.stop()


def bounded_tracking(cog):
    cog.views = {}
    cog.config = SimpleNamespace(max_views=20)
    cog.track = lambda view: BuildCog.track(cog, view)
    cog.untrack = lambda view: BuildCog.untrack(cog, view)


@pytest.mark.asyncio
async def test_two_spell_searches_can_return_to_an_active_simulator(cog, actor, build):
    bounded_tracking(cog)
    attack = freeze_attack(id="spell:1", name="Flamme", level=1, critical_denominator=0,
                           normal_lines=(AttackLine(element="fe", minimum=2, maximum=4),))
    cog.spells = SimpleNamespace(ensure=AsyncMock(return_value=SimpleNamespace(id="catalog", attacks=[attack])),
                                attack=AsyncMock(return_value=attack))
    parent = SimulatorView(cog, actor, build)
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
    for _ in range(2):
        modal = SpellSearchModal(parent)
        modal.query._value = "Flamme"
        modal.level._value = "1"
        await modal.on_submit(interaction)
    assert parent.is_finished()
    selection = cog.send_view.await_args.kwargs["view"]
    selection.children[0]._values = [attack.id]
    await selection.children[0].callback(interaction)
    replacement = cog.send_view.await_args.kwargs["view"]
    assert isinstance(replacement, SimulatorView) and replacement is not parent
    assert not replacement.is_finished() and replacement.attack == attack
    assert selection.is_finished()
    for view in tuple(cog.views):
        view.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["target", "optimizer", "budget"])
async def test_modal_replaces_expired_parent_instead_of_resending_it(cog, actor, build, kind):
    if kind == "target":
        parent = SimulatorView(cog, actor, build)
        modal = TargetModal(parent)
        modal.context._value, modal.mastery._value = "pvp", "0/100"
        modal.resistances._value = modal.buffs._value = modal.health._value = ""
    else:
        parent = AdvancedOptimizerView(cog, actor, build)
        if kind == "optimizer":
            modal = OptimizerModal(parent)
            modal.objective._value = "Force"
            modal.minimums._value = modal.locked._value = modal.owned._value = ""
        else:
            modal = BudgetModal(parent)
            modal.server._value, modal.amount._value = "Serveur", "123"
    parent.stop()
    await modal.on_submit(SimpleNamespace(response=SimpleNamespace(defer=AsyncMock())))
    replacement = cog.send_view.await_args.kwargs["view"]
    assert replacement is not parent and not replacement.is_finished()
    if kind == "target":
        assert replacement.scenario.mode == "pvp" and parent.scenario.mode == "pvm"
    elif kind == "optimizer":
        assert replacement.constraints.objective == "fo" and parent.constraints.objective == "pp"
    else:
        assert replacement.constraints.budget == 123 and parent.constraints.budget is None
    for view in tuple(cog.tracked):
        view.stop()


@pytest.mark.asyncio
async def test_simulation_exports_the_same_inputs_used_during_concurrent_change(cog, actor, build, monkeypatch):
    from utils.build import advanced_views
    first = freeze_attack(id="spell:1", name="Premier", critical_denominator=0,
                          normal_lines=(AttackLine(element="te", minimum=10, maximum=10),))
    second = freeze_attack(id="spell:2", name="Second", critical_denominator=0,
                           normal_lines=(AttackLine(element="te", minimum=50, maximum=50),))
    original_scenario = CombatScenario()
    parent = SimulatorView(cog, actor, build, first, original_scenario)
    async def concurrent_compute(function, report, attack, scenario):
        parent.attack = second
        parent.scenario = CombatScenario(mode="pvp")
        return function(report, attack, scenario)
    monkeypatch.setattr(advanced_views.asyncio, "to_thread", concurrent_compute)
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                  followup=SimpleNamespace(send=AsyncMock()))
    await parent.compute.callback(interaction)
    attachment = interaction.followup.send.await_args.kwargs["file"]
    output = json.loads(attachment.fp.getvalue())
    assert output["attack"]["revision"] == first.revision == output["result"]["attack_revision"]
    assert output["scenario"]["mode"] == "pvm"
    assert output["build_revision"] == build.revision
    assert parent.attack == second and parent.scenario.mode == "pvp"
    attachment.close()
    parent.stop()


@pytest.mark.asyncio
async def test_comparison_keeps_left_revision_from_menu_creation(cog, actor, build, monkeypatch):
    attack = freeze_attack(id="spell:1", name="Premier", critical_denominator=0,
                          normal_lines=(AttackLine(element="te", minimum=10, maximum=10),))
    parent = SimulatorView(cog, actor, build, attack)
    other = revised(build, id="00000000-0000-4000-8000-000000000123")
    cog.repository.list = AsyncMock(return_value=[build, other])
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
    await parent.compare.callback(interaction)
    selection = cog.send_view.await_args.kwargs["view"]
    selection.children[0]._values = [other.id]
    changed = revised(build, revision=build.revision + 1)
    parent.build = changed
    _, report, catalog = await cog.service.inspect(actor, build.id)
    cog.service.inspect.return_value = (changed, report, catalog)
    with pytest.raises(BuildError, match="changé"):
        await selection.children[0].callback(interaction)
    for view in tuple(cog.tracked):
        view.stop()


@pytest.mark.asyncio
async def test_price_details_are_readable_before_deletion(cog, actor, build, catalog):
    instance = equip(catalog.items[0])
    quote = quote_for(instance, catalog.items[0], "Serveur de test", 123)
    view = PriceListView(cog, actor, [quote])
    selection = next(child for child in view.children if hasattr(child, "options"))
    selection._values = [quote.id]
    interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
    await selection.callback(interaction)
    content = cog.send_view.await_args.kwargs["content"]
    assert "Force: 30" in content and "Serveur de test" in content and "123" in content
    cog.repository.delete_price.assert_not_awaited()
    assert cog.send_view.await_args.kwargs["ephemeral"] is True
    for item in tuple(cog.tracked):
        item.stop()
