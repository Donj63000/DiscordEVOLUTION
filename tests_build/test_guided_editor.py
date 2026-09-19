"""Saisies humaines : tests sans transport Discord, données synthétiques."""
import pytest
from pydantic import ValidationError
from utils.build.editor import parse_stats, stat_name, integer, format_stats, update_capital, edit_jets
from utils.build.models import Profile, as_stats, equip, revised, BuildError, values
from utils.build.effects import parse_lines, resolve_values


@pytest.mark.parametrize('text,expected', [
    ('Chance: 300\nSagesse: 90', {'cha':300,'sa':90}),
    ('Force = 30; Portée: 4', {'fo':30,'po':4}),
    ('Agilité: 50, PM: 5', {'age':50,'pm':5}),
    ('{"cha":300,"sa":90}', {'cha':300,'sa':90}),
    ('Dommages %: 20\nRésistance eau %: 7', {'pui':20,'rp_ea':7}),
    ('Force: -20', {'fo':-20}), (' ',{}),
])
def test_readable_and_legacy_stats(text, expected):
    assert as_stats(parse_stats(text)) == expected
    assert as_stats(parse_stats(format_stats(parse_stats(text)))) == expected


@pytest.mark.parametrize('text', [
    'Force: 1; fo: 2', '{"fo":true}', '{"fo":1.5}', 'Force: 1e3', 'Force: 2+2',
    'Niveau: 100', 'Force: 10001', '__import__("os"): 1', '[1,2]', 'x'*4001,
    'Force: 10 pommes', '{"fo":1,"fo":2}',
])
def test_bad_stats_rejected(text):
    with pytest.raises((BuildError, ValidationError)):
        parse_stats(text)


@pytest.mark.parametrize('name,key', [('Vita','vi'),('Chance','cha'),('agi','age'),('cc','cc'),('vie','pv')])
def test_aliases(name,key):
    assert stat_name(name)==key


@pytest.mark.parametrize('text,value',[('101',101),('  200 ',200),('+1',1),('1\u202f000',1000)])
def test_integer(text,value):
    assert integer(text,'Test',0,1000)==value


@pytest.mark.parametrize('text',['-1','1001','1.0','true','2 * 4','99999999',''])
def test_integer_rejects(text):
    with pytest.raises(BuildError):integer(text,'Test',0,1000)


def test_capital_is_copy_and_bounded():
    p=Profile(classe='enutrof',level=200)
    q=update_capital(p,'cha',500,101)
    assert p.allocated==() and as_stats(q.allocated)=={'cha':500}
    assert as_stats(q.scrolled)=={'cha':101}
    with pytest.raises(ValidationError):update_capital(q,'sa',500,0)
    with pytest.raises(BuildError):update_capital(p,'pa',1,0)
    with pytest.raises(BuildError):update_capital(revised(p,mode='declared'),'cha',1,0)


def test_natural_editor_completes_all_lines_and_keeps_signed_malus(item_factory):
    template=item_factory(effects=parse_lines(['+10 à 30 en force','-40 à -20 en chance']))
    instance=edit_jets(template,equip(template),{'e0':17})
    assert {v.ref:v.value for v in instance.final_values}=={'e0':17,'e1':-20}
    rows,_=resolve_values(template,instance,'coiffe')
    assert sum(v.value for v in rows if v.stat=='fo')==17
    with pytest.raises(BuildError):edit_jets(template,instance,{'e0':31})
    with pytest.raises(BuildError):edit_jets(template,instance,{'e9':5})


def test_fm_over_replaces_and_exo_only_adds_new_stat(item_factory):
    template=item_factory(stats={'fo':30})
    instance=revised(equip(template),mode='declared_fm',extras=values({'pm':1}))
    instance=edit_jets(template,instance,{'e0':40})
    rows,_=resolve_values(template,instance,'coiffe')
    assert [(r.stat,r.value) for r in rows]==[('fo',40),('pm',1)]
    with pytest.raises(BuildError):resolve_values(template,revised(instance,extras=values({'fo':10})),'coiffe')


def test_restricted_profile_keys():
    with pytest.raises(BuildError):parse_stats('PA:1',allowed={'fo'})
    with pytest.raises(BuildError):parse_stats('Force:-1',low=0)
