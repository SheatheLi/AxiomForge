"""Differential actual HTTPX/httpcore execution, no outside network.

Run with PYTHONPATH=src python benchmarks/reliability/run_live_comparison.py.
Requires exactly the inspected HTTPcore 1.0.9 and HTTPX 0.28.1 versions.
"""
from __future__ import annotations
import argparse,asyncio,importlib.util,json,hashlib,sys
from pathlib import Path
from importlib.metadata import version
from axiomforge.issue_engine.transactional_cleanup import instrument_candidate,candidate_patch_diff
from axiomforge.issue_engine.cleanup_model import verify_bounded

ROOT=Path(__file__).resolve().parents[2]

def load_benchmark():
    path=ROOT/'benchmarks/resource_protocol/httpx_double_cancel_local.py'
    spec=importlib.util.spec_from_file_location('httpx_live_verified',path)
    module=importlib.util.module_from_spec(spec);assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

async def measure(repetitions):
    import httpx,httpcore
    if httpx.__version__!='0.28.1' or httpcore.__version__!='1.0.9':
        raise RuntimeError(f'Pinned experiments require httpx 0.28.1/httpcore 1.0.9, got {httpx.__version__}/{httpcore.__version__}')
    mod=load_benchmark()
    results=[]
    for strategy in ('baseline','shield-only','rearm-after-cancellation','defer-commit','singleflight-commit'):
        with instrument_candidate(strategy):
            experiment=await mod.main(repetitions)
        for mode in ('single','double'):
            rows=experiment[mode]['rows']
            results.append({'strategy':strategy,'mode':mode,
                'rows':[{'iteration':row['iteration'],'active':row.get('active_pool_connections'),
                         'pending':row.get('pending_requests'),'error':row.get('inconclusive_or_blocked')}
                        for row in rows],
                'observed_bad_snapshots':sum(row.get('active_pool_connections',0)>0 for row in rows),
                'blocked_runs':sum(bool(row.get('inconclusive_or_blocked')) for row in rows),
                'trace_interruptions':sum(e['event']=='CANCELLED_DURING_CLEANUP' for e in experiment[mode]['cleanup_events'])})
    return {'schema':'axiomforge.live-differential.v1',
            'versions':{'python':sys.version.split()[0],'httpx':version('httpx'),
                        'httpcore':version('httpcore'),'anyio':version('anyio'),'h11':version('h11')},
            'setting':{'pool_max_connections':3,'local_tcp_server':True,'repetitions':repetitions},
            'strategies':results,'model_check':{s:verify_bounded(s,attempts_limit=2)
                 for s in ('early-commit','defer-commit')},
            'candidate_patch_sha256':hashlib.sha256(candidate_patch_diff().encode()).hexdigest(),
            'warnings':['Candidate monkeypatch changes private HTTPcore internals, not for production',
                        'Local timing-dependent reproduction only; not an upstream regression test result',
                        'Bounded model has deliberately simplified scheduler and assumes caller retry']}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=ROOT/'benchmarks/reliability/live_results.json')
    ap.add_argument('--repetitions',type=int,default=4)
    args=ap.parse_args();report=asyncio.run(measure(args.repetitions))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'strategies':[(s['strategy'],s['mode'],s['rows']) for s in report['strategies']]},ensure_ascii=False,indent=2))
