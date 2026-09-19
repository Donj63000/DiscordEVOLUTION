"""Recherche sur petites instances synthétiques, comparée à un oracle exhaustif."""
import itertools
import pytest
from utils.build.models import *
from utils.build.catalog import freeze_catalog
from utils.build.optimizer import optimize, Constraints, Limits, Price, CORE
from utils.build.optimizer_worker import OptimizerWorker
from utils.build.calculator import calculate


@pytest.fixture
def search_case(item_factory,build):
    items=[item_factory(n,slot,{"fo":1}) for n,slot in enumerate(CORE,1) if not slot.startswith("anneau")]
    items+=[item_factory(30,"anneau_1",{"fo":1},unique=False)]
    # Deux pièces faibles isolées gagnent une synergie PA qui satisfait le seuil.
    items+=[item_factory(40,"coiffe",{"fo":0},set_ref="combo"),item_factory(41,"cape",{"fo":0},set_ref="combo")]
    pano=SetDefinition(ref="combo",name="Synergie synthétique",source="fixture://synthetic",tiers=(
        SetTier(pieces=1,effects=()),SetTier(pieces=2,effects=(Effect(ref="bonus",kind="stat",stat="pa",low=3,high=3),))))
    cat=freeze_catalog(items,[pano]);return revised(build,catalog_id=cat.id),cat


def test_optimizer_keeps_set_synergy_and_hard_constraints(search_case,rules):
    b,cat=search_case
    r=optimize(b,cat,rules,Constraints(objective="fo",minimums=values({"pa":10,"pm":3})),Limits(beam=64,candidates=32))
    assert r["status"]=="FOUND" and not r["global_optimum"]
    for solution in r["solutions"]:
        candidate=Build.model_validate(solution["build"]);report=calculate(candidate,cat,rules)
        assert report.totals["pa"]>=10 and report.totals["pm"]>=3
        assert solution["constraints_verified"] and report.equipability=="valid"
        assert candidate.in_slot("coiffe").template_ref=="item:40"
        assert candidate.in_slot("cape").template_ref=="item:41"


def test_optimizer_matches_exhaustive_small_oracle(search_case,rules):
    b,cat=search_case
    options=[[i for i in cat.items if slot in i.allowed_slots] for slot in CORE]
    scores=[]
    for choices in itertools.product(*options):
        candidate=b
        for slot,item in zip(CORE,choices):candidate=candidate.with_slot(slot,equip(item))
        report=calculate(candidate,cat,rules)
        if report.equipability=="valid" and report.totals["pa"]>=10:scores.append(report.totals["fo"])
    r=optimize(b,cat,rules,Constraints(objective="fo",minimums=values({"pa":10})),Limits(beam=128,candidates=64))
    assert r["solutions"][0]["score"]==max(scores)


def test_budget_unknown_not_zero(search_case,rules):
    b,cat=search_case
    result=optimize(b,cat,rules,Constraints(objective="fo",budget=100))
    assert result["status"]=="DATA_INCOMPLETE" and result["solutions"]==[]
    assert result["tentative"] and all(r["cost"] is None for r in result["tentative"])


def test_known_budget_is_hard_constraint(search_case,rules):
    b,cat=search_case
    prices=tuple(Price(reference=i.ref,kamas=100) for i in cat.items)
    result=optimize(b,cat,rules,Constraints(objective="fo",budget=1,prices=prices,price_context="synthetic server, date, natural jets"))
    assert not result["solutions"] and not result["tentative"]
    assert result["status"]=="NO_SOLUTION_FOUND"


def test_resource_limit_not_unsatisfiable(search_case,rules):
    b,cat=search_case
    result=optimize(b,cat,rules,Constraints(objective="fo"),Limits(expansions=1))
    assert result["status"]=="RESOURCE_LIMIT" and not result["solutions"]
    assert result["global_optimum"] is False


def test_missing_category_is_not_fabricated(build,catalog,rules):
    result=optimize(build,catalog,rules,Constraints())
    assert result["status"]=="DATA_INCOMPLETE" and not result["solutions"]


def test_locked_item_and_no_exos_conflict(search_case,rules):
    b,cat=search_case;item=cat.by_ref["item:40"]
    b=b.with_slot("coiffe",Instance(template_ref=item.ref,template_revision=item.revision,mode="declared_fm",final_values=[{"ref":"e0","value":0}],extras=values({"pm":1})))
    with pytest.raises(BuildError):
        optimize(b,cat,rules,Constraints(locked=("coiffe",),allow_exos=False))


def test_empty_locked_slot_stays_empty(search_case,rules):
    b,cat=search_case
    result=optimize(b,cat,rules,Constraints(locked=("coiffe",)))
    rows=result["solutions"]+result["tentative"]
    assert rows and all(Build.model_validate(r["build"]).in_slot("coiffe") is None for r in rows)


def test_deterministic_equipment_choices(search_case,rules):
    b,cat=search_case
    def choices():
        result=optimize(b,cat,rules,Constraints())
        return [[(s["slot"],s["item"]["template_ref"]) for s in r["build"]["slots"]] for r in result["solutions"]]
    assert choices()==choices()


@pytest.mark.asyncio
async def test_real_subprocess_worker(search_case,rules):
    b,cat=search_case;worker=OptimizerWorker()
    try:
        result=await worker.run(b,cat,rules,Constraints(objective="fo",minimums=values({"pa":10})))
        assert result["status"]=="FOUND" and result["solutions"]
        assert worker.process is None
    finally:await worker.close()
    with pytest.raises(BuildError):await worker.run(b,cat,rules,Constraints())


@pytest.mark.asyncio
async def test_worker_close_during_search_cleans_up(search_case,rules):
    import asyncio
    b,cat=search_case;worker=OptimizerWorker()
    task=asyncio.create_task(worker.run(b,cat,rules,Constraints()))
    while worker.process is None and not task.done():await asyncio.sleep(0)
    await worker.close()
    result=await asyncio.gather(task,return_exceptions=True)
    assert worker.process is None
    assert isinstance(result[0],(dict,BuildError))
