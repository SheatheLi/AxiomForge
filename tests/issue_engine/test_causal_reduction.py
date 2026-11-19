from axiomforge.issue_engine.causal_reduction import Event,closed,minimize,leak

def test_reduce_extraneous_events_preserving_causal_parents():
    h=[Event('a','x','ALLOC'),Event('b','y','ALLOC'),Event('c','y','RELEASE',('b',)),Event('d','x','SCOPE_EXIT',('a',)),Event('e','z','ALLOC')]
    reduced,n=minimize(h,leak)
    assert closed(reduced) and leak(reduced)
    assert {x.key for x in reduced}=={'a','d'}
    assert n>0

def test_validity_check():
    h=[Event('release','x','RELEASE',('missing',))]
    assert not closed(h)
