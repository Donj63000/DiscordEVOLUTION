import ia


def test_is_exact_match_variations():
    assert ia.is_exact_match("pute", "pute")
    assert ia.is_exact_match("pu te", "pute")
    assert ia.is_exact_match("pu!te", "pute")
    assert ia.is_exact_match("fil s de pu te", "fils de pute")
    assert not ia.is_exact_match("computers", "pute")
