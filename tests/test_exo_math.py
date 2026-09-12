import math

import pytest

from utils.exo_math import (
    Budget, expected_capped_attempts, geometric_quantile, no_success,
    probability, run_campaigns, success_within, wilson_interval,
)


@pytest.mark.parametrize("p,n,expected", [(0, 100, 0), (1, 0, 0), (1, 1, 1), (.01, 100, 1-.99**100)])
def test_probability_edges(p, n, expected):
    assert success_within(p, n) == pytest.approx(expected)
    assert success_within(p, n) + no_success(p, n) == pytest.approx(1)


@pytest.mark.parametrize("confidence,expected", [(.5,69),(.9,230),(.95,299),(.99,459)])
def test_known_one_percent_quantiles(confidence, expected):
    assert geometric_quantile(.01, confidence) == expected
    assert success_within(.01, expected) >= confidence
    assert success_within(.01, expected-1) < confidence


@pytest.mark.parametrize("value", [-1, 1.01, math.nan, math.inf, True, "1"])
def test_invalid_probabilities(value):
    with pytest.raises(ValueError):
        probability(value)


@pytest.mark.parametrize("value", [-1, 1.2, True, 1000001])
def test_invalid_attempt_counts(value):
    with pytest.raises(ValueError):
        success_within(.01, value)


def test_quantile_boundaries():
    assert geometric_quantile(0, .5) is None
    assert geometric_quantile(.01, 1) is None
    assert geometric_quantile(1, 1) == 1
    assert geometric_quantile(0, 0) == 0


def test_small_probability_numerical_stability():
    assert success_within(1e-12, 1000) == pytest.approx(9.999999995005e-10, rel=1e-12)


def test_budget_does_not_charge_last_rebuild():
    b = Budget(rune=100, rebuild=25, item=1000, preparation=50, limit=1400)
    assert b.cost(0) == 0
    assert b.cost(1) == 1150
    assert b.cost(3) == 1400
    assert b.affordable() == 3
    assert b.expected_until_success(1) == 1150
    assert b.expected_until_success(.01) == 13525
    assert b.expected_capped_cost(0, 3) == 1400
    assert b.expected_capped_cost(.01, 0) == 0


def test_budget_edges():
    assert Budget(rune=100,limit=99).affordable() == 0
    assert Budget().affordable() == 1000000
    assert Budget(item=10,limit=9).affordable() == 0
    assert Budget().expected_until_success(0) is None
    with pytest.raises(ValueError):
        Budget(rune=-1)
    with pytest.raises(ValueError):
        Budget().expected_capped_cost(.01, -1)


def test_capped_expectation_enumerated():
    p, n = .2, 20
    expected = sum((1-p)**(k-1) for k in range(1, n+1))
    assert expected_capped_attempts(p,n) == pytest.approx(expected)


def test_campaign_reproducibility_and_theory():
    args = (.01, 100, 30000, Budget(rune=100, rebuild=25), 42)
    a, b = run_campaigns(*args), run_campaigns(*args)
    assert a == b
    assert abs(a.successes/a.experiments - success_within(.01,100)) < .012
    assert abs(a.mean_used-expected_capped_attempts(.01,100)) < 1
    assert a.used_p95 == 100
    assert a.success_interval[0] <= a.successes/a.experiments <= a.success_interval[1]


@pytest.mark.parametrize("p,cap,success,used", [(0,10,0,10),(1,10,100,1),(1,0,0,0),(.01,0,0,0)])
def test_campaign_degenerate(p,cap,success,used):
    result=run_campaigns(p,cap,100,Budget(rune=5,rebuild=2),1)
    assert result.successes == success
    assert result.mean_used == used
    assert result.mean_cost == Budget(rune=5,rebuild=2).cost(used)


def test_wilson_edges():
    assert wilson_interval(0,0) == (0,1)
    assert 0 <= wilson_interval(0,100)[0] < 1e-10
    assert wilson_interval(100,100)[1] == 1


@pytest.mark.parametrize("p,confidence,expected", [
    (.5, .75, 2), (.25, .578125, 3),
    (.5, math.nextafter(.75, 0), 2), (.5, math.nextafter(.75, 1), 3),
    (.5, .5, 1), (.5, math.nextafter(.5, 1), 2),
    (math.nextafter(0.0, 1.0), math.nextafter(0.0, 1.0), 1),
])
def test_quantile_exact_threshold_and_neighbours(p, confidence, expected):
    assert geometric_quantile(p, confidence) == expected


@pytest.mark.parametrize("p", [1e-30, 1e-320, math.nextafter(0.0, 1.0)])
def test_tiny_probability_quantile_is_the_minimal_integer(p):
    from decimal import Decimal, localcontext

    n = geometric_quantile(p, .95)
    assert isinstance(n, int)
    with localcontext() as context:
        context.prec = 900
        failure = 1 - Decimal.from_float(p)
        target = 1 - Decimal.from_float(.95)
        assert failure ** n <= target
        assert failure ** (n - 1) > target
