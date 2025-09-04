import pytest
from cogs.profil import parse_stats_block, StatsParseError

def test_parse_ok():
    text = "Coca-Cola : Vitalité 101 (+1762), Sagesse 101 (+249), Force 389 (+135), Intelligence 101 (+129), Chance 101 (+335), Agilité 101 (+30) - Initiative 1400, PA 8, PM 4"
    stats, init, pa, pm = parse_stats_block(text)
    assert stats["vitalite"].base == 101 and stats["vitalite"].bonus == 1762
    assert stats["force"].base == 389 and stats["force"].bonus == 135
    assert init == 1400 and pa == 8 and pm == 4

def test_missing_field():
    text = "Vitalité 100 (+100), Force 200 (+0), Intelligence 100 (+0), Chance 100 (+0), Agilité 100 (+0) - Initiative 1000, PA 8, PM 4"
    with pytest.raises(StatsParseError):
        parse_stats_block(text)
