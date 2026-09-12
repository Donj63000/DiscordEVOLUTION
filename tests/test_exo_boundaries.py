import copy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from utils.exo_data import demo_item, parse_effects
from utils.exo_embeds import build_embed, number
from utils.exo_engine import D, Item, Rates, Rune, State, attempt, observe
from utils.exo_math import Budget, geometric_quantile, run_campaigns
from utils.exo_session import Session, export_session, import_session


def test_extremely_small_probability_does_not_overflow():
    p=1e-320
    n=geometric_quantile(p,.95)
    assert isinstance(n,int) and n>10**300
    assert len(number(n)) > 300
    result=run_campaigns(p,1000000,1000,Budget(),1)
    assert result.successes == 0
    assert result.mean_used == 1000000
    assert Budget().expected_until_success(p) == 0


def test_extreme_imported_math_still_renders():
    data=json.loads(export_session(Session.create(demo_item())))
    data["math"]["p"]=1e-320
    s=import_session(json.dumps(data).encode())
    s.tab="maths"
    assert len(build_embed(s)) <= 6000


@pytest.mark.parametrize("field,value",[("sequence",10**9),("attempts",10**9),("spent",10**18)])
def test_session_counter_limit_is_atomic(field,value):
    item=demo_item()
    state=State.initial(item)
    setattr(state,field,value)
    before=copy.deepcopy(state)
    with pytest.raises(ValueError,match="Limite de session"):
        attempt(item,state,Rune("pm"),None,1,1)
    assert state == before


def test_unsupported_effects_invalidate_computed_observed_sink():
    item=Item("Test","test",{"pa":(1,1)},"fixture",("Effet inconnu",))
    state=State({"pa":1},D(100))
    observe(item,state,Rune("pm"),"EC",{"pa":1})
    assert state.sink is None


def test_common_weapon_damage_is_not_treated_as_mageable_bonus():
    bounds,unknown,immutable=parse_effects(["11 à 20 (dommages neutre)","+6 à 10 de dommages"])
    assert bounds == {"do":(6,10)}
    assert not unknown
    assert immutable == ("11 à 20 (dommages neutre)",)


def test_common_legacy_damage_percent_is_parsed():
    bounds,unknown,_=parse_effects(["Augmente les dommages de 11 à 20%"])
    assert bounds == {"pui":(11,20)}
    assert not unknown


@pytest.mark.parametrize("p", [0, 1, .01, 1e-30, 1e-320, math.nextafter(0.0, 1.0)])
def test_imported_probability_render_has_a_bounded_runtime(p):
    data = json.loads(export_session(Session.create(demo_item())))
    data["math"]["p"] = p
    script = """
import sys
from utils.exo_session import import_session
from utils.exo_embeds import build_embed
session = import_session(sys.stdin.buffer.read())
session.tab = 'maths'
embed = build_embed(session)
assert len(embed) <= 6000
print('rendered')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        input=json.dumps(data).encode(), capture_output=True, timeout=8,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"rendered"
