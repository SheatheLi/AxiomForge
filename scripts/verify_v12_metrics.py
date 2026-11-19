"""Validate *semantic* receipt integrity; never require machine-specific timings."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import sys

def verify(path: Path) -> None:
    obj=json.loads(path.read_text(encoding='utf8'))
    if obj.get('schema')!='axiomforge.v12-resume-evidence.v1':
        raise ValueError('not an AxiomForge v1.2 measurement')
    root=Path(__file__).resolve().parents[1]
    for name, digest in obj['engine_sha256'].items():
        if name not in ('dataflow.py','oracle_audit.py','core.py','synthesis.py'):
            raise ValueError('unknown metric engine component')
        source=root/'src/axiomforge/issue_engine'/name
        assert hashlib.sha256(source.read_bytes()).hexdigest()==digest, f'metric source hash changed: {name}'
    samples=obj['dataflow_benchmark']
    assert len(samples)==3 and {x['repository'] for x in samples}=={'urllib3','httpcore','sqlalchemy'}
    for x in samples:
        assert x['cfg_nodes']>=x['sliced_nodes']>=1
        assert 0<=x['scope_reduction_fraction']<1
        assert x['elapsed_ms_p50']>=0 and x['elapsed_ms_p95']>=0
        assert len(x['source_sha256'])==64
    inter=obj['cross_method_upstream_case']
    assert inter['library']=='urllib3'
    assert not inter['guard_in_intraprocedural_slice']
    assert inter['guard_in_lexical_interprocedural_slice']
    assert inter['resolved_method_count']>=2
    assert any(edge['callee']=='_is_method_retryable' for edge in inter['lexical_call_edges'])
    pressure={x['controls']:x for x in obj['oracle_pressure_benchmark']}
    assert set(pressure)=={'strong','weak'}
    for x in pressure.values():
        assert x['status']=='AUDITED'
        assert x['eligible_mutants']==2 and x['assessed']==2
        assert x['killed']+x['survived']==2
        assert len(x['source_sha256'])==64
    assert pressure['strong']['killed']==2 and pressure['weak']['killed']==0
    assert not any(m['candidate_passed_full_contract'] for m in pressure['strong']['raw_mutants'])
    assert any(m['candidate_passed_full_contract'] for m in pressure['weak']['raw_mutants'])
    print(json.dumps({'status':'PASSED', 'upstream_repositories':len(samples),
                      'strong_controls_killed':pressure['strong']['killed'],
                      'weak_controls_killed':pressure['weak']['killed']},ensure_ascii=False))

if __name__=='__main__':
    verify(Path(sys.argv[1] if len(sys.argv)>1 else 'artifacts/v12_resume_metrics.json'))
