"""Reproducible bounded cancellation safety study, no web or secrets."""
from __future__ import annotations
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
SCENARIO = ROOT / 'examples' / 'cancellation_safety' / 'pool_scenarios.py'
spec = importlib.util.spec_from_file_location('pool_scenarios', SCENARIO)
models = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(models)
from axiomforge.issue_engine.cancellation_safety import explore, explore_nested_cancellation

async def go():
    results={}
    for name, fixed in [('historical_cancel_bug_model', False), ('fixed_cleanup_model', True)]:
        unit, factory = models.build(corrected=fixed)
        t0=perf_counter()
        receipt=await explore(factory, unit, max_sites=10, timeout=0.3)
        receipt['elapsed_wall_seconds']=round(perf_counter()-t0,6)
        results[name]=receipt
    for name, fixed in [('nested_cleanup_bug_model',False),('nested_cleanup_fixed_model',True)]:
        unit,factory=models.build_nested(corrected=fixed)
        t0=perf_counter()
        receipt=await explore_nested_cancellation(factory,unit,max_depth=2,max_runs=20,timeout=0.3)
        receipt['elapsed_wall_seconds']=round(perf_counter()-t0,6)
        results[name]=receipt
    results['study']={
        'model_provenance':'Independently authored minimal models of HTTPcore PR #305 (not upstream source execution)',
        'pr_url':'https://github.com/encode/httpcore/pull/305',
        'interpretation':'A counterexample on this model proves a safety violation in the model at a specific cancellation checkpoint, not APR success or upstream fix.',
        'comparison':'Same source structure, fault sites, scenario, safety invariant and bounded cancellation budget',
    }
    return results

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    result=asyncio.run(go())
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:{'tested':len(v.get('experiments',v.get('runs',[]))),
                         'violations':v['violations'],
                         'recovery_blocked':sum(t.get('recovery_status')=='BLOCKED'
                                for t in v.get('experiments',[]))}
                 for k,v in result.items() if k!='study'},indent=2))
