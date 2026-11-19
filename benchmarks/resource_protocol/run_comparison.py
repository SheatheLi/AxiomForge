"""Baseline all await checkpoints versus resource-obligation-risk filtering.

The heuristic selects fewer interventions only on this controlled model and
can miss bugs if resource probes are incomplete. Never claim universal speedup.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('risk_demo',ROOT/'examples/cancellation_safety/lifecycle_risk_demo.py')
model=importlib.util.module_from_spec(spec);spec.loader.exec_module(model)
from axiomforge.issue_engine.cancellation_safety import explore

async def run():
    unit,factory=model.setup()
    results={}
    for name, guided in [('all_sites',False),('obligation_guided',True)]:
        t0=perf_counter()
        output=await explore(factory,unit,max_sites=50,timeout=.3,risk_guided=guided)
        results[name]={ 'eligible_sites':output['eligible_sites'],
                        'tested_sites':output['tested_sites'],
                        'violations':output['violations'],
                        'risk_excluded_sites':output['risk_excluded_sites'],
                        'site_results':[{'site':e['site'],'status':e['status'],
                                         'passed':e['invariant_pass']} for e in output['experiments']],
                        'elapsed_seconds':round(perf_counter()-t0,6)}
    return {'study':'synthetic-risk-window', 'results':results,
            'limits':'Handwritten model and authoritative resource probe. Filter is NOT completeness preserving; time not benchmarked across projects.'}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    report=asyncio.run(run());args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:{x:v[x] for x in ('tested_sites','violations','risk_excluded_sites')}
                      for k,v in report['results'].items()},indent=2))
