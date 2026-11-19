"""Protect comparator semantics beyond the original single regression case."""
from axiomforge.issue_engine.shape_guard_repair import operator_shape_candidates


SOURCE='''def swap(segments):
    inverted = {'<':'>','>':'<'}
    if segments and segments[0].raw in inverted:
        return inverted[segments[0].raw] + ''.join(s.raw for s in segments[1:])
    return ''.join(s.raw for s in segments)
'''


def test_operator_guard_preserves_non_symmetric_multitoken_comparators():
    fixes=list(operator_shape_candidates(SOURCE))
    assert len(fixes)==1
    n={}
    exec(fixes[0].source,n)
    class P:
        def __init__(self,raw):self.raw=raw
    wrap=lambda s:[P(x) for x in s]
    assert n['swap'](wrap(['<']))=='>'
    assert n['swap'](wrap(['>']))=='<'
    assert n['swap'](wrap(['<','=']))=='>='
    assert n['swap'](wrap(['>','=']))=='<='
    assert n['swap'](wrap(['<','=','>']))=='<=>'
    assert n['swap'](wrap(['<','>']))=='<>'


def test_unrelated_first_element_code_is_not_given_comparator_rules():
    source='''def consume(xs):
    if xs and xs[0].raw:
        return xs[0].raw
'''
    assert list(operator_shape_candidates(source))==[]
