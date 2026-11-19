"""Reproducible actual-SQL reduction comparison: hierarchical vs row-wise baseline.

Times are environment-dependent; oracle calls and row counts are deterministic.
The regression is seeded by a genuine many-to-many data-shape defect, not by
mocking the reducer's predicate. Does NOT imply novelty over SQLancer reducers.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT/'src'))
from axiomforge.reduction import reduce, verify_bundle

BEFORE = 'SELECT id AS fact_id, price AS amount FROM orders'
AFTER = 'SELECT o.id AS fact_id,o.price AS amount FROM orders AS o LEFT JOIN supplies AS s ON s.sku=o.sku'


def create_case(directory: Path, n: int):
    db = directory/'work.sqlite'; old = directory/'before.sql'; new = directory/'after.sql'
    old.write_text(BEFORE, encoding='utf-8'); new.write_text(AFTER, encoding='utf-8')
    with sqlite3.connect(db) as cx:
        cx.executescript('''CREATE TABLE orders(id TEXT PRIMARY KEY,sku TEXT NOT NULL,price INTEGER NOT NULL);
                            CREATE TABLE supplies(id INTEGER PRIMARY KEY,sku TEXT NOT NULL);''')
        cx.executemany('INSERT INTO orders VALUES (?,?,?)',
                       [(f'order-{i:06d}', f'sku-{i:06d}', (i%101)+1) for i in range(n)])
        cx.executemany('INSERT INTO supplies(sku) VALUES (?)',
                       [(f'sku-{i:06d}',) for i in range(n)] + [(f'sku-{i:06d}',) for i in range(n)])
    return db,old,new


def run(n: int, modes=('hierarchical','greedy')):
    with tempfile.TemporaryDirectory(prefix='axiomforge-benchmark-') as d:
        db, old, new = create_case(Path(d), n)
        results = {}
        for mode in modes:
            bundle = Path(d)/f'{mode}.zip'
            t0 = time.monotonic()
            run = reduce(db,old,new,'orders','id','price',tables=['orders','supplies'],
                         output=bundle,target_id='order-000000',mode=mode,
                         timeout_seconds=600,max_calls=50_000,max_rows=50000)
            ver = verify_bundle(bundle)
            r = run['reduction']
            results[mode] = {'source_rows':r['source_reducible_rows'],
                             'remaining_rows':r['remaining_reducible_rows'],
                             'removed_pct':round(100*r['removed_rows']/r['source_reducible_rows'],2),
                             'oracle_calls':r['oracle_calls'],
                             'elapsed_seconds':round(time.monotonic()-t0,4),
                             'one_minimal':r['one_minimal'],
                             'replay':ver['status'], 'file_bytes':bundle.stat().st_size}
        if len(results)==2:
            left,right = results['hierarchical'],results['greedy']
            results['comparison']={'oracle_calls_saved_pct':round(100*(1-left['oracle_calls']/right['oracle_calls']),2),
                                   'oracle_calls_ratio':round(right['oracle_calls']/left['oracle_calls'],3),
                                   'equal_failure_and_row_minimality':left['one_minimal'] and right['one_minimal'] and left['remaining_rows']==right['remaining_rows']}
        if len(results)==2 and (not results['comparison']['equal_failure_and_row_minimality'] or
                                results['hierarchical']['oracle_calls'] >= results['greedy']['oracle_calls']):
            raise AssertionError('reducer regression: non-equivalent witness or no Oracle-call improvement')
        return {'benchmark':'single-DB deterministic fanout witness; same regression oracle and row-removal scope',
                'sqlite_version':sqlite3.sqlite_version,'python':sys.version.split()[0],
                'n_orders':n,'results':results}


if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--orders',type=int,default=100)
    p.add_argument('--out')
    p.add_argument('--strategy',choices=['both','hierarchical','greedy'],default='both')
    args=p.parse_args()
    result=run(args.orders, modes=('hierarchical','greedy') if args.strategy=='both' else (args.strategy,))
    text=json.dumps(result,indent=2,ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True,exist_ok=True)
        Path(args.out).write_text(text+'\n',encoding='utf-8')
