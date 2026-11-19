"""Protocol model validity, precise failure classification and execution adapter."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import random
from pathlib import Path
import subprocess
import sys
import pytest

from axiomforge.issue_engine.resource_protocol import ProtocolChecker
from axiomforge.issue_engine.cancellation_safety import explore

ROOT=Path(__file__).resolve().parents[2]


def checker(events):
    return ProtocolChecker.replay(events)[1]


def kinds(events):
    return checker(events)['finding_kinds']


def test_orphan_after_scope_abort():
    obj=checker([{'kind':'allocate','resource':'db','owner':'connect:init'},
                 {'kind':'scope_abort','scope':'connect:init'},
                 {'kind':'pool_observe','pool':'main','available':1}])
    assert obj['finding_kinds']==['ORPHANED_RESOURCE']
    assert [e['kind'] for e in obj['findings'][0]['causal_witness']]==['allocate','scope_abort']


def test_safe_idle_pool_resource():
    assert not kinds([{'kind':'allocate','resource':'c','owner':'connect:init'},
                      {'kind':'transfer','resource':'c','owner':'pool:main'},
                      {'kind':'scope_finish','scope':'connect:init'}])


def test_proper_cleanup_and_generation_reuse():
    _,report=ProtocolChecker.replay([
        {'kind':'allocate','resource':'physical-object','owner':'a'},
        {'kind':'retire','resource':'physical-object'},
        {'kind':'scope_abort','scope':'a'},
        {'kind':'allocate','resource':'physical-object','owner':'b'},
        {'kind':'retire','resource':'physical-object'},
        {'kind':'scope_finish','scope':'b'}])
    assert not report['findings']


def test_dirty_pool_return_even_if_capacity_normal():
    assert kinds([{'kind':'allocate','resource':'c','owner':'pool'},
                  {'kind':'checkout','resource':'c','owner':'worker'},
                  {'kind':'tx_begin','resource':'c'},
                  {'kind':'checkin','resource':'c','owner':'pool'},
                  {'kind':'pool_observe','pool':'pool','available':1}]) == ['DIRTY_POOL_RETURN']


def test_clean_transaction_return():
    assert not kinds([{'kind':'allocate','resource':'c','owner':'pool'},
                      {'kind':'checkout','resource':'c','owner':'worker'},
                      {'kind':'tx_begin','resource':'c'},
                      {'kind':'tx_end','resource':'c'},
                      {'kind':'checkin','resource':'c','owner':'pool'}])


def test_invalid_event_and_missing_resource_rejected():
    obj=ProtocolChecker()
    with pytest.raises(ValueError):obj.emit('not-a-valid-action')
    with pytest.raises(ValueError):obj.emit('transfer',resource='c')
    obj.emit('retire',resource='absent')
    assert obj.finalize()['finding_kinds']==['UNKNOWN_RESOURCE']


def test_duplicate_generation_collision_is_detected():
    assert kinds([{'kind':'allocate','resource':'c','owner':'s'},
                  {'kind':'allocate','resource':'c','owner':'s'}]) == ['DUPLICATE_ALLOCATION']


def test_transaction_underflow_is_detected():
    assert kinds([{'kind':'allocate','resource':'c','owner':'pool'},
                  {'kind':'tx_end','resource':'c'}]) == ['TRANSACTION_UNDERFLOW']


def test_randomized_valid_resource_histories_have_no_findings():
    for seed in range(100):
        rng=random.Random(seed)
        events=[]
        for resource in range(5):
            token=f'conn:{resource}'
            events.append({'kind':'allocate','resource':token,'owner':f'scope:{resource}'})
            for _ in range(rng.randrange(4)):
                events.append({'kind':'transfer','resource':token,'owner':f'pool:{resource}'})
            events.append({'kind':'retire','resource':token})
            events.append({'kind':'scope_abort','scope':f'scope:{resource}'})
        assert not checker(events)['findings'],seed


def test_fault_mutation_marks_at_least_one_orphan():
    for idx in range(4):
        trace=[{'kind':'allocate','resource':f'c{i}','owner':f's{i}'} for i in range(4)]
        trace += [{'kind':'retire','resource':f'c{i}'} for i in range(4) if i!=idx]
        trace += [{'kind':'scope_abort','scope':f's{i}'} for i in range(4)]
        obj=checker(trace)
        assert len(obj['findings'])==1
        assert obj['findings'][0]['resource']==f'c{idx}'


def test_cli_replay_exit_codes(tmp_path):
    from axiomforge.issue_engine.protocol_cli import main
    src=tmp_path/'trace.json'
    src.write_text(json.dumps({'events':[{'kind':'allocate','resource':'r','owner':'s'},
                                         {'kind':'scope_abort','scope':'s'}]}))
    assert main([str(src),'--out',str(tmp_path/'report.json')])==2
    assert json.loads((tmp_path/'report.json').read_text())['finding_kinds']==['ORPHANED_RESOURCE']
    assert main([str(tmp_path/'missing.json')])==1


def test_sqlalchemy_real_queuepool_integration():
    sqlalchemy=pytest.importorskip('sqlalchemy')
    script=ROOT/'benchmarks/cancellation_safety/protocol_sqlalchemy_integration.py'
    spec=importlib.util.spec_from_file_location('protocol_sa',script)
    module=importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    if sqlalchemy.__version__!='2.0.50':
        pytest.skip('Pinned pre-fix integration designed for SQLAlchemy 2.0.50')
    broken=module.run_case('connect-failure',attempts=3)
    patched=module.run_case('manual-close',attempts=3)
    healthy=module.run_case('healthy',attempts=1)
    assert len(broken['protocol_before_dispose']['findings'])==3
    assert not patched['protocol_before_dispose']['findings']
    assert not healthy['protocol_before_dispose']['findings']
    assert healthy['legacy_created_minus_retired_before_dispose']==1


def test_risk_guided_reduces_intervention_positions_in_controlled_model():
    spec=importlib.util.spec_from_file_location('risk_demo',ROOT/'examples/cancellation_safety/lifecycle_risk_demo.py')
    mod=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(mod)
    unit,factory=mod.setup()
    async def run():
        all_sites=await explore(factory,unit,max_sites=50,timeout=.4)
        guided=await explore(factory,unit,max_sites=50,timeout=.4,risk_guided=True)
        return all_sites,guided
    all_sites,guided=asyncio.run(run())
    assert all_sites['tested_sites']==8
    assert guided['tested_sites']==3
    assert guided['violations']==all_sites['violations']==3
    assert guided['risk_excluded_sites']==5


def test_risk_mode_requires_probe():
    from axiomforge.issue_engine.cancellation_safety import Scenario,instrument_source
    unit=instrument_source('import asyncio\nasync def work():\n await asyncio.sleep(0)\n')
    def factory():return Scenario(operation=unit.namespace['work'],observe=lambda:{'held':[]})
    with pytest.raises(ValueError,match='risk_probe'):
        asyncio.run(explore(factory,unit,risk_guided=True))


def test_quiescent_pool_can_distinguish_active_vs_idle():
    assert kinds([{'kind':'pool_quiescence','resource':'c','pool':'main',
                  'active':True,'pending_requests':0}]) == ['STRANDED_ACTIVE_POOL_CONNECTION']
    assert not kinds([{'kind':'pool_quiescence','resource':'c','pool':'main',
                      'active':False,'pending_requests':0}])
    assert not kinds([{'kind':'pool_quiescence','resource':'c','pool':'main',
                      'active':True,'pending_requests':1}])


def test_httpx_live_local_loopback_issue_3782_diagnostic():
    import httpx
    if httpx.__version__!='0.28.1':
        pytest.skip('Environment-specific HTTPX regression, recorded version 0.28.1')
    spec=importlib.util.spec_from_file_location('httpx_live',ROOT/'benchmarks/resource_protocol/httpx_double_cancel_local.py')
    mod=importlib.util.module_from_spec(spec);assert spec.loader;spec.loader.exec_module(mod)
    report=asyncio.run(mod.main(3))
    assert len(report['single']['rows'])==3
    assert all(row.get('active_pool_connections')==0 for row in report['single']['rows'])
    # This machine reproduces open issue #3782; avoid failing the whole suite
    # on platforms/schedulers where the timing-dependent bug doesn't reproduce.
    double_findings=report['double']['protocol_summary']['findings']
    if double_findings:
        assert all(f['kind']=='STRANDED_ACTIVE_POOL_CONNECTION' for f in double_findings)
    else:
        pytest.skip('Timing-dependent upstream failure not reproduced on this run')
