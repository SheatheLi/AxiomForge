"""Repeat the fixed live comparison and expose per-repetition outcomes (not a random population study)."""
from __future__ import annotations
import argparse,asyncio,json,importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def get_experiment():
    p=ROOT/'benchmarks/reliability/run_live_comparison.py'
    s=importlib.util.spec_from_file_location('reliability_live_compare',p)
    mod=importlib.util.module_from_spec(s);s.loader.exec_module(mod)
    return mod.measure

async def run(repeats:int=3,steps:int=4):
    measure=get_experiment()
    studies=[]
    for index in range(repeats):
        one=await measure(steps)
        studies.append({'replication':index+1,'strategies':[
            {'strategy':p['strategy'],'mode':p['mode'],
             'max_active':max((e['active'] for e in p['rows'] if e['active'] is not None),default=None),
             'bad_snapshots':p['observed_bad_snapshots'],'blocked':p['blocked_runs'],
             'completed_clean':sum(e['active']==0 for e in p['rows'])}
            for p in one['strategies']]})
    return {'replications':repeats,'steps_per_variant':steps,'fixed_schedule_not_random_sample':True,
            'data':studies}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--replications',type=int,default=3)
    args=parser.parse_args();report=asyncio.run(run(args.replications))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    for row in report['data']:
        print(row['replication'],[(p['strategy'],p['max_active'],p['blocked'])
              for p in row['strategies'] if p['mode']=='double'])
