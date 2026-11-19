"""Independently replayable v1.2 engineering metrics on installed upstream code.

This is NOT an APR win-rate or proof of bug-location correctness. Dataflow
measurements compare slices to unfiltered same-function CFG scope; Oracle
measurements compare strong vs intentionally weak normal controls on the
same genuine urllib3 source. No network, no API, no vendored third-party code.
"""
from __future__ import annotations
import argparse
import hashlib
import inspect
import json
import math
from pathlib import Path
import shutil
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.issue_engine.dataflow import backward_slice, interprocedural_slice
from axiomforge.issue_engine.oracle_audit import audit_oracle
from axiomforge.issue_engine.core import load_contract


def _percentile(values, p):
    return sorted(values)[min(len(values)-1, max(0, math.ceil(len(values)*p)-1))]


def dataflow_benchmark(reps=20):
    import urllib3, httpcore, sqlalchemy
    from urllib3.util.retry import Retry
    from httpcore._sync.connection_pool import ConnectionPool
    from sqlalchemy.engine.url import URL
    roster=[('urllib3', Retry.is_retry, 'return bool('),
            ('httpcore', ConnectionPool._assign_requests_to_connections, 'return closing_connections'),
            ('sqlalchemy', URL.difference_update_query, 'return URL(')]
    versions={'urllib3':urllib3.__version__,'httpcore':httpcore.__version__,'sqlalchemy':sqlalchemy.__version__}
    studies=[]
    for repo, method, marker in roster:
        file=Path(inspect.getsourcefile(method))
        source=file.read_text('utf8')
        statements=file.read_text('utf8').splitlines()
        first=inspect.getsourcelines(method)[1]
        scoped=inspect.getsourcelines(method)[0]
        seed=next((first+i for i,line in enumerate(scoped) if marker in line),None)
        if seed is None:
            raise RuntimeError(f'marker unavailable for {repo}/{method.__qualname__}')
        times=[]
        for _ in range(reps):
            t=time.perf_counter_ns()
            result=backward_slice(source, seed)
            times.append((time.perf_counter_ns()-t)/1e6)
        studies.append({'repository':repo,'library_version':versions[repo],
                        'function':method.__qualname__,'relative_source':file.name,
                        'source_sha256':hashlib.sha256(source.encode('utf8')).hexdigest(),
                        'seed_line':seed,'cfg_nodes':result['cfg_nodes'],
                        'cfg_edges':result['cfg_edges'],'worklist_iterations':result['worklist_iterations'],
                        'sliced_nodes':result['slice_nodes'],
                        'scope_reduction_fraction':round(1-result['slice_nodes']/result['cfg_nodes'],6),
                        'elapsed_ms_p50':round(statistics.median(times),4),
                        'elapsed_ms_p95':round(_percentile(times,.95),4),
                        'note':'Reduction is of CFG statement candidates in that one function; not verified bug-location accuracy.'})
    return studies



def crossmethod_case():
    from urllib3.util.retry import Retry
    path=Path(inspect.getsourcefile(Retry.is_retry))
    source=path.read_text('utf8')
    # A caller-only slice cannot reach the method used to check allowed_methods;
    # the constrained lexical call graph can expose the actual defect guard.
    before=backward_slice(source,420)
    after=interprocedural_slice(source,420,max_call_depth=2)
    if not after.get('call_graph_edges'):
        raise RuntimeError('expected lexical retry helper edge absent')
    return {'library':'urllib3', 'issue_url':'https://github.com/urllib3/urllib3/issues/5044',
            'source_sha256':hashlib.sha256(source.encode('utf8')).hexdigest(),
            'caller_seed_line':420,'helper_guard_line':407,
            'guard_in_intraprocedural_slice':407 in before['sliced_lines'],
            'guard_in_lexical_interprocedural_slice':407 in after['sliced_lines'],
            'resolved_method_count':after['method_count'],
            'lexical_call_edges':after['call_graph_edges'],
            'note':'Known historical issue used for development; static lexical call edge is a hypothesis, not runtime dispatch proof or new PR.'}


def oracle_benchmark():
    import urllib3
    # Copy real installed source instead of reimplementing the library behavior.
    source_dir=Path(urllib3.__file__).parent
    results=[]
    with tempfile.TemporaryDirectory(prefix='axiom-v12-real-urllib3-') as d:
        root=Path(d)
        repo=root/'checkout';repo.mkdir()
        shutil.copytree(source_dir,repo/'urllib3',ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.so'))
        checks=root/'scripts';checks.mkdir()
        (repo/'repro.py').write_text('''from urllib3.util.retry import Retry
r=Retry(status_forcelist=[500],allowed_methods=set())
assert not r.is_retry("GET",status_code=500), "V12-REAL-UPSTREAM-BUG"
''',encoding='utf8')
        (repo/'strong.py').write_text('''from urllib3.util.retry import Retry
mk=lambda methods: Retry(status_forcelist=[500],allowed_methods=methods)
assert mk(None).is_retry("POST", status_code=500)
assert mk({"GET"}).is_retry("GET", status_code=500)
assert not mk({"GET"}).is_retry("POST", status_code=500)
''',encoding='utf8')
        (repo/'weak.py').write_text('''from urllib3.util.retry import Retry
assert Retry(status_forcelist=[500],allowed_methods=None) is not None
''',encoding='utf8')
        for mode in ('strong','weak'):
            cfg={'schema':'axiomforge.issue-contract.v1','issue_url':'https://github.com/urllib3/urllib3/issues/5044',
                 'checkout':'../checkout','reproduce':['${PYTHON}','repro.py'],
                 'control':['${PYTHON}',mode+'.py'],
                 'editable_files':['urllib3/util/retry.py'],
                 'failure_fingerprint':'V12-REAL-UPSTREAM-BUG',
                 'repetitions':1,'timeout_seconds':6,'isolated_checks':True}
            p=checks/(mode+'.json');p.write_text(json.dumps(cfg),encoding='utf8')
            report=audit_oracle(load_contract(p),file='urllib3/util/retry.py',
                                max_mutants=10,max_seconds=80,seed=2026,
                                line_start=403,line_end=410)
            results.append({'controls':mode,'status':report['status'],
                            'eligible_mutants':report['total_eligible_mutations'],
                            'assessed':report['assessed_mutations'],
                            'killed':report['killed_by_normal_controls'],
                            'survived':report['survived_normal_controls'],
                            'control_kill_fraction':report['control_kill_fraction'],
                            'seconds':report['seconds'],
                            'source_sha256':report['source_sha256'],
                            'raw_mutants':report['mutations']})
        return results


def main():
    a=argparse.ArgumentParser()
    a.add_argument('--out',type=Path,default=ROOT/'artifacts/v12_resume_metrics.json')
    a.add_argument('--repetitions',type=int,default=20)
    args=a.parse_args()
    if not 1<=args.repetitions<=100:
        a.error('--repetitions must be 1..100')
    data={'schema':'axiomforge.v12-resume-evidence.v1',
          'analysis_scope':'installed real upstream Python production code',
          'environment': {'python':sys.version.split()[0], 'platform':platform.platform()},
          'engine_sha256': {name:hashlib.sha256((ROOT/'src/axiomforge/issue_engine'/name).read_bytes()).hexdigest()
                            for name in ('dataflow.py','oracle_audit.py','core.py','synthesis.py')},
          'measurement_type':'intraprocedural static slice size + local time; actual-code mutation sensitivity',
          'dataflow_benchmark':dataflow_benchmark(args.repetitions),
          'cross_method_upstream_case':crossmethod_case(),
          'oracle_pressure_benchmark':oracle_benchmark(),
          'claim_boundary':'Not unseen-Issue APR, PR submission, performance leadership, or proof of causal correctness.'}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print(json.dumps({'out':str(args.out),'functions':len(data['dataflow_benchmark']),
                      'scopes':[{'repo':x['repository'],'reduction':x['scope_reduction_fraction'],
                                 'p95_ms':x['elapsed_ms_p95']} for x in data['dataflow_benchmark']],
                      'mutation':data['oracle_pressure_benchmark']},ensure_ascii=False)[:4000])
if __name__=='__main__':main()
