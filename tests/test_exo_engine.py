import copy
import json

import pytest

from utils.exo_data import demo_item, parse_effects
from utils.exo_engine import (
    D, Item, Rates, Rune, State, attempt, eligibility, fixed_exo, observe,
    parse_jets, rates_for, risk, surplus,
)
from utils.exo_session import Session, export_session, import_session


def test_native_pa_is_not_an_exo():
    item = demo_item()
    state = State({"pa":0}, D(0))
    assert not fixed_exo(item,state,Rune("pa"))
    rates = rates_for(item,state,Rune("pa"),None)
    assert rates.sc > .01
    assert "non calibrée" in rates.source
    assert fixed_exo(item,state,Rune("pm"))


def test_heavy_exo_uses_preset():
    item=demo_item()
    assert rates_for(item,State.initial(item),Rune("pm"),Rates(1,0)).sc == .01


def test_nominal_gelano_pm_failure():
    item = demo_item()
    state = State.initial(item)
    row = attempt(item,state,Rune("pm"),None,1)
    assert row["outcome"] == "EC"
    assert state.jets["pa"] == 0
    assert state.sink == 10
    assert row["losses"] == {"pa":1}


def test_critical_success_keeps_sink():
    item = Item("Test","test",{"fo":(1,10)},"fixture")
    state = State({"fo":1,"pa":1},D(17))
    row=attempt(item,state,Rune("fo"),Rates(1,0),1)
    assert row["outcome"] == "SC"
    assert state.sink == 17
    assert state.jets == {"fo":2,"pa":1}


def test_neutral_success_debits_sink():
    item=Item("Test","test",{"fo":(1,10),"vi":(1,100)},"fixture")
    state=State({"fo":1,"vi":10},D(5))
    row=attempt(item,state,Rune("fo"),Rates(0,1),1)
    assert row["outcome"] == "SN"
    assert state.sink == 4
    assert state.jets == {"fo":2,"vi":10}


def test_surplus_other_lines_precedes_sink():
    item=Item("Test","test",{"fo":(1,10)},"fixture")
    state=State({"fo":1,"vi":4},D(100))
    row=attempt(item,state,Rune("fo"),Rates(0,0),1)
    assert row["losses"] == {"vi":4}
    assert state.sink == 100


def test_decimal_accounting_no_float_drift():
    item=Item("Test","test",{"ini":(1,100)},"fixture")
    state=State({"ini":1},D("5"))
    for _ in range(5):
        attempt(item,state,Rune("ini"),Rates(0,0),1)
    assert state.sink == 0


def test_caps_native_not_counted_against_exo_budget():
    item=demo_item()
    state=State.initial(item)
    assert eligibility(item,state,Rune("pm"))[0]
    assert not eligibility(item,state,Rune("pa"))[0]
    state.jets["pm"]=1
    assert surplus(item,state.jets) == 90
    assert not eligibility(item,state,Rune("pm"))[0]
    assert not eligibility(item,state,Rune("po"))[0]


def test_line_cap_counts_total_line_not_only_over():
    item=Item("Test","test",{"sa":(1,30)},"fixture")
    assert eligibility(item,State({"sa":32}),Rune("sa"))[0]
    assert not eligibility(item,State({"sa":33}),Rune("sa"))[0]


def test_natural_line_above_101_can_be_restored():
    item=Item("Test","test",{"vi":(400,500)},"fixture")
    assert eligibility(item,State({"vi":497}),Rune("vi"))[0]
    assert not eligibility(item,State({"vi":500}),Rune("vi"))[0]


def test_unknown_and_malus_block_automatic():
    for item in [
        Item("Test","test",{"fo":(1,10)},"fixture",("effet inconnu",)),
        Item("Test","test",{"fo":(-10,-1)},"fixture"),
    ]:
        with pytest.raises(ValueError):
            attempt(item,State.initial(item),Rune("pm"),None,1)


def test_unknown_sink_remains_unknown_in_observations():
    item=demo_item()
    state=State({"pa":1},None)
    observe(item,state,Rune("pm"),"EC",{"pa":1})
    assert state.sink is None
    assert state.jets["pa"] == 0


def test_observed_loss_accounting():
    item=demo_item()
    state=State({"pa":1},D(0))
    row=observe(item,state,Rune("sa"),"SN",{"pa":1},100)
    assert state.sink == 97
    assert state.jets == {"pa":0,"sa":1}
    assert row["mode"] == "observation"
    assert state.spent == 100


def test_invalid_observation_is_atomic():
    item=demo_item()
    state=State.initial(item)
    original=copy.deepcopy(state)
    with pytest.raises(ValueError):
        observe(item,state,Rune("pm"),"SN",{"pa":2})
    assert state == original
    with pytest.raises(ValueError):
        observe(item,state,Rune("pm"),"SC",{"pa":1})
    assert state == original


def test_risk_does_not_mutate_live_state():
    item=demo_item()
    state=State.initial(item)
    original=copy.deepcopy(state)
    a=risk(item,state,Rune("pm"),None,5,1000)
    b=risk(item,state,Rune("pm"),None,5,1000)
    assert a == b
    assert state == original
    assert .97 < a["loss_probability"]["pa"] < 1


def test_journal_preserves_all_150_events():
    item=Item("Test","test",{"fo":(1,10)},"fixture")
    state=State({"fo":1},D(1000))
    for _ in range(150):
        attempt(item,state,Rune("fo"),Rates(0,0),3)
    assert state.attempts == 150
    assert len(state.journal) == 150
    assert state.journal[0]["n"] == 1


@pytest.mark.parametrize("text,expected", [
    ("force=10; vita=200\npa=1",{"fo":10,"vi":200,"pa":1}),
    ("",{}),("po=0",{"po":0}),
])
def test_parse_jets(text,expected):
    assert parse_jets(text) == expected


@pytest.mark.parametrize("text", ["fo=1;force=2","foobar=1","fo=1.5","fo","fo=999999"])
def test_parse_jets_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_jets(text)


def test_export_import_replays_next_draw():
    s=Session.create(demo_item())
    s.seed=1234
    attempt(s.item,s.sim,s.rune,None,s.seed)
    other=import_session(export_session(s))
    assert other.sim == s.sim
    assert "déclaratif" in other.item.source
    assert attempt(s.item,s.sim,s.rune,None,s.seed) == attempt(other.item,other.sim,other.rune,None,other.seed)


@pytest.mark.parametrize("mutation", [
    lambda x:x.update(schema=4),
    lambda x:x.update(seed=True),
    lambda x:x["math"].update(p=float("nan")),
    lambda x:x["simulation"].update(sink="-1"),
    lambda x:x["simulation"]["jets"].update(fo=10001),
    lambda x:x["rune"].update(tier=4),
    lambda x:x["rates"].update({"vi:0":{"sc":.8,"sn":.3}}),
])
def test_import_rejects_invalid(mutation):
    data=json.loads(export_session(Session.create(demo_item())))
    mutation(data)
    with pytest.raises(ValueError):
        import_session(json.dumps(data).encode())


def test_import_rejects_big_and_non_json():
    for raw in (b"x"*131073,b"not json",b"\xff",b'{"schema":1}'):
        with pytest.raises(ValueError):
            import_session(raw)


def test_no_game_urls_secrets_or_executable_state_in_export():
    raw=export_session(Session.create(demo_item()))
    assert b"api_key" not in raw
    assert b"pickle" not in raw
    assert b"http" not in raw
