"""Fair visible failure of novelty claim: standard SQL uniqueness detects same issue."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.semantics.grain_preflight import Contract,analyze


def run(left,right,fact_id):
    with Path(left).open(encoding='utf-8-sig',newline='') as f:
        facts=list(csv.DictReader(f))
    with Path(right).open(encoding='utf-8-sig',newline='') as f:
        dims=list(csv.DictReader(f))
    db=sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE fact(fid TEXT, k TEXT, price INTEGER)')
        db.execute('CREATE TABLE dim(k TEXT)')
        db.executemany('INSERT INTO fact VALUES(?,?,?)',[(row[fact_id],row['sku'],int(row['price'])) for row in facts])
        db.executemany('INSERT INTO dim VALUES (?)',[(r['sku'],) for r in dims])
        # Equivalent to a conventional dbt `unique` test on joined fact ID.
        ids=[row[0] for row in db.execute('''SELECT fid FROM (
            SELECT fact.fid FROM fact LEFT JOIN dim ON fact.k=dim.k)
            GROUP BY fid HAVING COUNT(*)>1 ORDER BY fid''')]
    finally:db.close()
    tool=analyze(left,right,Contract('sku','sku',fact_id,'price',True))
    ours=sorted(x['fact_id'] for x in tool['affected'])
    if ids!=ours:raise AssertionError('strong baseline and candidate tool disagree')
    return {'strong_baseline':'SQL SELECT ... GROUP BY fact_id HAVING COUNT(*)>1',
            'baseline_affected_keys':len(ids),'axiomforge_affected_keys':tool['full_affected_count'],
            'detection_gain_over_baseline':0,
            'incremental_output':'impact-ranking, duplicate-contribution trace, templated preaggregation and replay receipts',
            'statement':'No detection superiority demonstrated; baseline detects same affected keys.'}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--external',action='store_true')
    p.add_argument('--out')
    ns=p.parse_args()
    base=ROOT/'examples'/('jaffle_shop' if ns.external else 'grain_demo')
    result=run(base/('raw_products.csv' if ns.external else 'facts.csv'),
               base/('raw_supplies.csv' if ns.external else 'supplies.csv'),
               'sku' if ns.external else 'id')
    content=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if ns.out:
        output=Path(ns.out);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(content,encoding='utf-8')
    print(content)

if __name__=='__main__':main()
