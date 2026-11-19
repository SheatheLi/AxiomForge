"""Bounded model, optimization oracle and live candidate regression."""
from __future__ import annotations
import asyncio, itertools, json, subprocess, sys
from pathlib import Path
import pytest

from axiomforge.issue_engine.cleanup_model import verify_bounded
from axiomforge.issue_engine.fault_optimization import optimal_fault_plan
from axiomforge.issue_engine.transactional_cleanup import instrument_candidate, candidate_patch_diff

ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('interruptions,expected_early,expected_deferred',[(0,0,0),(1,1,0),(2,1,1)])
def test_bounded_cleanup_commits(interruptions,expected_early,expected_deferred):
    early=verify_bounded('early-commit',attempts_limit=2,max_interruptions=interruptions)
    deferred=verify_bounded('defer-commit',attempts_limit=2,max_interruptions=interruptions)
    assert early['bad_terminal_count']==expected_early
    assert deferred['bad_terminal_count']==expected_deferred
    if expected_early:
        assert early['shortest_counterexample']['actions']


def test_model_parameter_validation():
    with pytest.raises(ValueError):verify_bounded('early-commit',attempts_limit=0)
    with pytest.raises(ValueError):verify_bounded('early-commit',attempts_limit=2,max_interruptions=3)
    with pytest.raises(ValueError):verify_bounded('not-a-model')

@pytest.mark.parametrize('paths,costs',[
    ([['A','B'],['B','C']], {'A':2,'B':4,'C':2}),
    ([['A','B'],['B','C']], {'A':2,'B':3,'C':2}),
    ([['A'],['B','C']], {'A':1,'B':1,'C':3}),
    ([['A','B','C'],['A','D'],['C','D']], {'A':3,'B':1,'C':2,'D':2}),
    ([['A','B'],['C','D'],['B','D']], {'A':8,'B':2,'C':2,'D':3}),
])
def test_milp_matches_bruteforce(paths,costs):
    solution=optimal_fault_plan(paths,costs)
    assert solution['status']=='optimal'
    candidates=list(costs)
    brute=min(sum(costs[x] for x in subset)
              for count in range(1,len(candidates)+1)
              for subset in itertools.combinations(candidates,count)
              if all(set(path).intersection(subset) for path in paths))
    assert solution['cost']==pytest.approx(brute)


def test_milp_invalid_and_missing_dependencies():
    with pytest.raises(ValueError): optimal_fault_plan([['A']],{'A':0})
    with pytest.raises(ValueError): optimal_fault_plan([['A','B']],{'A':1})
    with pytest.raises(ValueError): optimal_fault_plan([[]],{'A':1})
    assert 'self._closed = True' in candidate_patch_diff()


def test_research_cli(tmp_path,capsys):
    from axiomforge.issue_engine.reliability_cli import main
    assert main(['model-check','--attempts','2','--max-interruptions','1'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['early-commit']['bad_terminal_count']>0
    assert result['defer-commit']['bad_terminal_count']==0
    path=tmp_path/'problem.json'
    path.write_text(json.dumps({'paths':[['A','B'],['B','C']],'costs':{'A':1,'B':4,'C':1}}))
    assert main(['plan',str(path)])==0
    assert json.loads(capsys.readouterr().out)['cost']==2


def test_in_memory_candidate_restoration():
    pytest.importorskip('httpcore')
    from httpcore._async.http11 import HTTP11ConnectionByteStream
    original=HTTP11ConnectionByteStream.aclose
    with instrument_candidate('defer-commit'):
        assert HTTP11ConnectionByteStream.aclose is not original
    assert HTTP11ConnectionByteStream.aclose is original
    with pytest.raises(RuntimeError):
        with instrument_candidate('rearm-after-cancellation'):
            raise RuntimeError('abort')
    assert HTTP11ConnectionByteStream.aclose is original


@pytest.mark.slow
def test_real_httpx_deferred_commit_vs_negative_baseline():
    httpx=pytest.importorskip('httpx');httpcore=pytest.importorskip('httpcore')
    if (httpx.__version__,httpcore.__version__)!=('0.28.1','1.0.9'):
        pytest.skip('Pinned real-world comparison is version-sensitive')
    import importlib.util
    spec=importlib.util.spec_from_file_location('live_repair',ROOT/'benchmarks/reliability/run_live_comparison.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data=asyncio.run(module.measure(4))
    pairs={(x['strategy'],x['mode']):x for x in data['strategies']}
    assert [x['active'] for x in pairs[('baseline','double')]['rows'][:3]]==[1,2,3]
    assert pairs[('baseline','double')]['blocked_runs']==1
    assert pairs[('shield-only','double')]['blocked_runs']==1
    for strategy in ('defer-commit','rearm-after-cancellation','singleflight-commit'):
        assert all(x['active']==0 for x in pairs[(strategy,'double')]['rows'])
        assert pairs[(strategy,'double')]['blocked_runs']==0
    assert pairs[('defer-commit','single')]['blocked_runs']==0

@pytest.mark.parametrize('strategy,expected', [('baseline',1),('defer-commit',2),('singleflight-commit',1)])
def test_concurrent_close_at_most_once(strategy,expected):
    """Discover that naive defer-commit introduces a concurrent double-close."""
    from httpcore._async.http11 import HTTP11ConnectionByteStream
    from httpcore._models import Request

    class FakeConnection:
        def __init__(self):self.calls=0
        async def _response_closed(self):
            self.calls+=1
            await asyncio.sleep(.002)

    fake=HTTP11ConnectionByteStream.__new__(HTTP11ConnectionByteStream)
    fake._connection=FakeConnection()
    fake._closed=False
    fake._request=Request('GET', 'http://127.0.0.1/')
    async def race():
        await asyncio.gather(fake.aclose(),fake.aclose())
    with instrument_candidate(strategy):
        asyncio.run(race())
    assert fake._connection.calls==expected
    assert fake._closed is True


def test_singleflight_reentry_after_caller_cancel():
    from httpcore._async.http11 import HTTP11ConnectionByteStream
    from httpcore._models import Request
    class FakeConnection:
        def __init__(self):self.calls=0
        async def _response_closed(self):
            self.calls+=1
            await asyncio.sleep(.01)
    stream=HTTP11ConnectionByteStream.__new__(HTTP11ConnectionByteStream)
    stream._closed=False
    stream._connection=FakeConnection()
    stream._request=Request('GET','http://127.0.0.1/')
    async def run():
        task=asyncio.create_task(stream.aclose())
        await asyncio.sleep(0)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        await stream.aclose()
        return stream._connection.calls,stream._closed
    with instrument_candidate('singleflight-commit'):
        calls,closed=asyncio.run(run())
    assert (calls,closed)==(1,True)
