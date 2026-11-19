import pytest
from axiomforge.resource_lifecycle import analyze,ResourceTraceError

def trace(*pairs):
    return {'clock_id':'steady','events':[
        dict(action=action,resource_id=resource,timestamp=float(i)) for i,(action,resource) in enumerate(pairs)]}

def test_full_release_and_terminal():
    t=trace(('BORROW','conn'),('CLOSE_BEGIN','conn'),('RELEASE','conn'),('CLOSE_END','conn'),('POOL_TERMINATED',None))
    x=analyze(t)
    assert x.status=='RELEASED'
    assert x.terminal_observed

def test_canceled_close_is_not_claimed_as_resource_release():
    t=trace(('BORROW','conn'),('CLOSE_BEGIN','conn'),('CLOSE_CANCEL','conn'),('POOL_TERMINATED',None))
    x=analyze(t)
    assert x.status=='OPEN_AT_TERMINAL'
    assert x.findings[0].status=='OPEN_AT_TERMINAL'

def test_open_trace_without_terminal_is_inconclusive():
    x=analyze(trace(('BORROW','conn'),('CLOSE_BEGIN','conn')))
    assert x.status=='INDETERMINATE'

def test_duplicate_release_is_violation():
    x=analyze(trace(('BORROW','x'),('RELEASE','x'),('RELEASE','x')))
    assert x.status=='PROTOCOL_VIOLATION'
    assert 'double release' in x.findings[0].reasons

@pytest.mark.parametrize('broken',[
    {'clock_id':'steady','events':[]},
    {'clock_id':'steady','events':[{'action':'RELEASE','resource_id':'x','timestamp':float('nan')}]},
    {'clock_id':'steady','events':[{'action':'HACK','resource_id':'x','timestamp':0}]},
    trace(('POOL_TERMINATED',None),('BORROW','x')),
])
def test_bad_trace_rejected(broken):
    with pytest.raises(ResourceTraceError):analyze(broken)
