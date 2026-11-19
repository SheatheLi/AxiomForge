"""Controlled scenario calibration, not a real-world unknown-bug benchmark.

All 8 scenario families and labels are declared before execution. Compares
aggregate-only, raw dimension uniqueness, their union, and a strong direct
referenced-key cardinality baseline against the multi-grain evidence engine.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import pathlib
import random
import sys
import time

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.semantics.pipeline_audit import AuditConfig,audit

FAMILIES=('clean','unreferenced_duplicate','fanout','null_drop','cross_entity_cancellation',
          'within_entity_cancellation','zero_weight_duplicate','unmatched_key_drop')


def scenario(family:str,seed:int):
    rng=random.Random(34119+seed)
    v=rng.randint(1,1000)
    f=[{'sku':'A','cost':v},{'sku':'B','cost':v},{'sku':'C','cost':rng.randint(1,19)}]
    d=[{'sku':'A','type':'red'},{'sku':'B','type':'blue'},{'sku':'C','type':'green'}]
    if family=='unreferenced_duplicate':
        d.extend(({'sku':'Z','type':'orphan'}, {'sku':'Z','type':'orphan'}))
    elif family=='fanout':d.append({'sku':'A','type':'red'})
    elif family=='null_drop':d[1]['type']=None
    elif family=='cross_entity_cancellation':
        d[1]['type']=None;d.append({'sku':'A','type':'red'})
    elif family=='within_entity_cancellation':
        f=[{'sku':'A','cost':v},{'sku':'A','cost':-v},{'sku':'C','cost':rng.randint(1,19)}]
        d.append({'sku':'A','type':'red'})
    elif family=='zero_weight_duplicate':
        f[0]['cost']=0;d.append({'sku':'A','type':'red'})
    elif family=='unmatched_key_drop':
        f.append({'sku':'Q','cost':v})
    elif family!='clean':raise ValueError(family)
    return f,d


def metrics(rows,method):
    tp=sum(x['gold_violation'] and x[method] for x in rows)
    fp=sum(not x['gold_violation'] and x[method] for x in rows)
    fn=sum(x['gold_violation'] and not x[method] for x in rows)
    tn=sum(not x['gold_violation'] and not x[method] for x in rows)
    return {'true_positive':tp,'false_positive':fp,'false_negative':fn,'true_negative':tn}


def run(seeds:int=24):
    if not 1<=seeds<=100:raise ValueError('seed bound 1..100')
    rows=[]
    start=time.perf_counter()
    for family in FAMILIES:
        for i in range(seeds):
            f,d=scenario(family,i)
            report=audit(f,d,AuditConfig(),top=15)
            dims_counts=collections.Counter(x['sku'] for x in d)
            survivors={x['sku']:sum(v['type'] is not None for v in d if v['sku']==x['sku']) for x in f}
            observed={'family':family,'seed':i,'gold_violation':family not in ('clean','unreferenced_duplicate'),
                      'aggregate_only':not report['invariant_levels']['global_pass'],
                      'dimension_unique_only':any(n>1 for n in dims_counts.values()),
                      'aggregate_or_unique':not report['invariant_levels']['global_pass'] or any(n>1 for n in dims_counts.values()),
                      'referenced_cardinality':any(survivors[x['sku']]!=1 for x in f),
                      'multigrain':report['verdict']!='EXACTLY_ONCE_PRESERVED',
                      'verdict':report['verdict'],
                      'affected_fact_rows':report['counts']['affected_fact_rows'],
                      'three_backend_agreement':report['backend_cross_check']['status']=='THREE_EXECUTION_PATHS_AGREE'}
            rows.append(observed)
    methods=('aggregate_only','dimension_unique_only','aggregate_or_unique','referenced_cardinality','multigrain')
    import platform, pandas, sqlalchemy
    return {'environment':{'python':platform.python_version(), 'pandas':pandas.__version__, 'sqlalchemy':sqlalchemy.__version__},
            'schema':'axiomforge.pipeline_calibration.v1','study_kind':'controlled_stratified_calibration_not_unknown_bug_discovery',
            'seed_count':seeds,'scenario_families':list(FAMILIES), 'execution_worlds':len(rows),
            'real_backend_executions':len(rows)*3,
            'elapsed_s_observed':round(time.perf_counter()-start,3),
            'calibration':{method:metrics(rows,method) for method in methods},
            'all_backend_checks_pass':all(x['three_backend_agreement'] for x in rows),
            'global_total_masked_worlds':sum(x['gold_violation'] and not x['aggregate_only'] for x in rows),
            'explicit_limitation':'referenced-cardinality baseline is optimal on this restricted contract; no detector superiority claimed over it',
            'trials':rows}


def write(path,payload):
    data=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
    receipt={'sha256':hashlib.sha256(data).hexdigest(),'result':payload}
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n',encoding='utf8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seeds',type=int,default=24)
    parser.add_argument('--out',default='artifacts/pipeline_audit/study.json')
    args=parser.parse_args()
    r=run(args.seeds)
    write(args.out,r)
    print(json.dumps({k:v for k,v in r.items() if k!='trials'},indent=2,ensure_ascii=False))

if __name__=='__main__':main()
