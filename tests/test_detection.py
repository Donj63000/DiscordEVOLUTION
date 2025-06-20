import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import ia


def test_is_exact_match_variations():
    assert ia.is_exact_match("pute", "pute")
    assert ia.is_exact_match("pu te", "pute")
    assert ia.is_exact_match("pu!te", "pute")
    assert ia.is_exact_match("fil s de pu te", "fils de pute")
    assert ia.is_exact_match("pût3", "pute")
    assert ia.is_exact_match("filz de pute", "fils de pute")
    assert ia.is_exact_match("pu7e", "pute")
    assert ia.is_exact_match("pûtë", "pute")
    assert ia.is_exact_match("p\u0302u\u0308t\u0301e", "pute")
    assert not ia.is_exact_match("computers", "pute")
