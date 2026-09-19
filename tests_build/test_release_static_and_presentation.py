"""Contrôles statiques/présentation : ne remplacent pas les tests discord.py réels."""
import ast
from pathlib import Path
from io import BytesIO
import pytest
from utils.build.calculator import calculate
from utils.build.models import revised, SlotItem, EffectValue, equip, values
from utils.build.renderer import text_report
from utils.build.comparison import comparison_text

ROOT = Path(__file__).resolve().parents[1]


def test_static_slash_group_remains_at_twenty_five():
    tree = ast.parse((ROOT / 'build.py').read_text(encoding='utf-8'))
    names = [keyword.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
             and node.func.value.id == 'group' and node.func.attr == 'command'
             for keyword in node.keywords if keyword.arg == 'name']
    assert len(names) == len(set(names)) == 25


@pytest.mark.parametrize('path', ['utils/build/views.py', 'utils/build/panels.py', 'utils/build/sharing.py'])
def test_static_component_labels_fit_transport_limits(path):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call): continue
        func = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
        for kw in node.keywords:
            if not isinstance(kw.value, ast.Constant) or not isinstance(kw.value.value, str): continue
            if kw.arg == 'placeholder': assert len(kw.value.value) <= 150
            if kw.arg == 'label': assert len(kw.value.value) <= (45 if func == 'TextInput' else 80)
            if kw.arg == 'custom_id': assert len(kw.value.value) <= 100


def test_text_report_shows_actual_over_and_extra(build, catalog, rules):
    template = catalog.items[0]
    instance = revised(equip(template), mode='declared_fm',
                       final_values=(EffectValue(ref='e0', value=50),), extras=values({'pm': 1}))
    current = revised(build, slots=(SlotItem(slot='coiffe', item=instance),))
    report = calculate(current, catalog, rules)
    text = text_report(current, report, catalog, True)
    assert 'valeur utilisée 50' in text and 'Exo déclaré : PM +1' in text
    assert 'Dofus 6 : —' in text
    assert 'déclarés' in text and 'declared_simulation' not in text


def test_comparison_readable_and_warns_profile_changes(build, catalog, rules):
    second = revised(build, name='Deuxième', profile=revised(build.profile, classe='cra'))
    text = comparison_text(build, calculate(build, catalog, rules), second, calculate(second, catalog, rules))
    assert 'profils de personnages sont différents' in text
    assert 'Aucun écart' in text and 'Deuxième' in text
    assert 'avant' in text and 'après' in text


@pytest.mark.parametrize('raw,kind,stat,low,high,element', [
    ('+30 en vie', 'stat', 'pv', 30, 30, None),
    ('-30 en vie', 'stat', 'pv', -30, -30, None),
    ('Augmente le poids portable de 1 à 500 pods', 'stat', 'pod', 1, 500, None),
    ('Vole 3 à 5 PDV (eau)', 'steal', None, 3, 5, 'ea'),
    ('Dommages : 4 à 6 (neutre)', 'damage', None, 4, 6, 'ne'),
])
def test_life_pods_and_weapon_lines_are_distinct(raw, kind, stat, low, high, element):
    from utils.build.effects import parse_line
    effect = parse_line(raw, 0)
    assert (effect.kind, effect.stat, effect.low, effect.high, effect.element) == (kind, stat, low, high, element)
