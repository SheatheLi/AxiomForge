import asyncio
import sys
from pathlib import Path

import pytest
from axiomforge.issue_engine.cancellation_safety import Ledger, Scenario, explore, instrument_source

EXAMPLES=Path(__file__).resolve().parents[2] / 'examples' / 'cancellation_safety'
sys.path.insert(0,str(EXAMPLES))
from pool_scenarios import build


def run(fixed):
    instrumented, factory=build(fixed)
    return asyncio.run(explore(factory,instrumented,timeout=0.2))


def test_real_bug_class_detected_and_recovery_blocked():
    receipt=run(False)
    assert receipt['baseline']['invariant_pass']
    assert receipt['violations'] >= 1
    assert any(x['recovery_status']=='BLOCKED' for x in receipt['experiments'] if not x['invariant_pass'])
    assert all(x['gate_reached'] for x in receipt['experiments'])


def test_repaired_exception_handler_passes_identical_cancellation_schedule():
    broken=run(False)
    repaired=run(True)
    assert repaired['violations']==0
    assert repaired['tested_sites']==broken['tested_sites']
    assert all(x['recovery_status']=='OK' for x in repaired['experiments'])


def test_instrumentation_preserves_result_and_expression_order():
    source='''
async def f():
    return 1 + (await g())
'''
    async def g():return 6
    instrumented=instrument_source(source,namespace={'g':g})
    assert asyncio.run(instrumented.namespace['f']())==7
    assert len(instrumented.sites)==1


def test_instrumentation_supports_try_finally_and_expression_await():
    source='''
async def f():
    try:
        x = await g()
        return x
    finally:
        await h()
'''
    async def g(): return 3
    hits=[]
    async def h():hits.append('h')
    mod=instrument_source(source, namespace={'g':g,'h':h})
    assert asyncio.run(mod.namespace['f']())==3
    assert hits==['h']
    assert len(mod.sites)==2


def test_protocol_ledger_rejects_double_release_and_duplicate_acquire():
    ledger=Ledger()
    ledger.acquire('r');ledger.acquire('r');ledger.release('r');ledger.release('r')
    assert 'duplicate-acquire:r' in ledger.violations
    assert 'invalid-release:r' in ledger.violations


def test_unreached_site_is_not_misclassified_as_violation():
    u=instrument_source('''
import asyncio
async def f():
    if False:
        await asyncio.sleep(0)
    return 1
''')
    assert asyncio.run(explore(lambda:Scenario(operation=u.namespace['f'],observe=lambda:{'held':[]}),u))['tested_sites']==0


def test_bounded_inputs_checked():
    u, fac=build(True)
    with pytest.raises(ValueError):
        asyncio.run(explore(fac,u,max_sites=0))


def test_nested_cancellation_reveals_cleanup_only_failure():
    from pool_scenarios import build_nested
    from axiomforge.issue_engine.cancellation_safety import explore_nested_cancellation
    u,fac=build_nested(False)
    receipt=asyncio.run(explore_nested_cancellation(fac,u,max_depth=2,timeout=0.2))
    assert any(len(x['schedule'])==2 and not x['invariant_pass'] for x in receipt['runs'])
    assert receipt['violations']>0


def test_nested_cancellation_fixed_cleanup_order():
    from pool_scenarios import build_nested
    from axiomforge.issue_engine.cancellation_safety import explore_nested_cancellation
    u,fac=build_nested(True)
    receipt=asyncio.run(explore_nested_cancellation(fac,u,max_depth=2,timeout=0.2))
    assert receipt['violations']==0


def test_real_httpcore_source_mutation_differential():
    import importlib.util
    path=Path(__file__).resolve().parents[2] / 'benchmarks' / 'cancellation_safety' / 'httpcore_integration.py'
    spec=importlib.util.spec_from_file_location('real_pool_study',path)
    mod=importlib.util.module_from_spec(spec)
    try:
        import httpcore  # noqa: F401
    except ImportError:
        pytest.skip('httpcore optional integration is not installed')
    spec.loader.exec_module(mod)
    result=asyncio.run(mod.main())
    assert result['shipped']['queue_invariant']
    assert not result['cancellation_handler_mutant']['queue_invariant']
    assert result['differential_detects_mutant']


def test_cli_refuses_untrusted_default(tmp_path):
    from axiomforge.issue_engine.cancel_cli import main
    with pytest.raises(SystemExit) as exc:
        main(['--scenario','bad.py:build','--out',str(tmp_path/'out.json')])
    assert exc.value.code == 2


def test_requested_cancellation_immediate_await_semantics_are_documented():
    # Cancellation may be recorded on a Task even when the awaited value is
    # immediately available. We report the actual task status, not the
    # existence of a synthetic blocking Future().
    source='''
class Immediate:
    def __await__(self):
        if False:
            yield None
        return 7
async def func():
    return await Immediate()
'''
    mod=instrument_source(source)
    unit=lambda:Scenario(operation=mod.namespace['func'],observe=lambda:{'held':[]})
    report=asyncio.run(explore(unit,mod))
    assert report['tested_sites']==1
    assert report['experiments'][0]['gate_reached']
    assert report['experiments'][0]['status']=='CANCELLED'
    assert report['violations']==0


def test_real_sqlalchemy_connect_event_exception_closure_gap():
    import importlib.util
    path=Path(__file__).resolve().parents[2] / 'benchmarks' / 'cancellation_safety' / 'sqlalchemy_event_integration.py'
    try: import sqlalchemy  # noqa: F401
    except ImportError:pytest.skip('SQLAlchemy optional integration is not installed')
    spec=importlib.util.spec_from_file_location('sa_event_study',path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    r=mod.main()
    assert r['pre_fix_behavior']['dbapi_created_calls']==5
    assert r['pre_fix_behavior']['formal_pool_close_calls']==0
    assert r['application_workaround']['manual_dbapi_close_calls']==5


def test_ownership_oracle_distinguishes_reused_object_ids():
    from axiomforge.issue_engine.ownership_oracle import OwnershipOracle
    recorder=OwnershipOracle()
    item=object()
    recorder.created_resource(item)
    recorder.created_resource(item) # emulate identity reuse/overlap
    recorder.retired_resource(item)
    assert recorder.snapshot()['not_retired']==1
    recorder.retired_resource(item)
    assert recorder.snapshot()['not_retired']==0
    recorder.retired_resource(item)
    assert recorder.snapshot()['violations']
