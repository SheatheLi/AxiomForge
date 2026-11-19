"""End-to-end, independent and adversarial reducer regression tests."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import zipfile

import pytest
from axiomforge.reduction import reduce, verify_bundle, ReviewError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/sql_review'


def invoke(tmp_path, **opts):
    output = tmp_path/'witness.zip'
    return reduce(EXAMPLE/'orders.sqlite', EXAMPLE/'before.sql', EXAMPLE/'after.sql',
                  'orders', 'id', 'price', tables=['orders','supplies'],
                  output=output, **opts)


def test_reduces_complex_balanced_case_to_three_rows(tmp_path):
    x = invoke(tmp_path)
    assert x['reduction']['source_reducible_rows'] == 8
    assert x['reduction']['remaining_reducible_rows'] == 3
    assert x['reduction']['one_minimal']
    assert x['reduction']['oracle_calls'] >= 3
    assert verify_bundle(tmp_path/'witness.zip')['status'] == 'REPLAY_VERIFIED'
    independent = tmp_path/'standalone'
    independent.mkdir()
    with zipfile.ZipFile(tmp_path/'witness.zip') as z:
        z.extractall(independent)
    completed=subprocess.run([sys.executable,str(independent/'replay.py')],cwd=independent,
                             text=True,capture_output=True,timeout=20)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['status']=='REPRODUCED'
    assert verify_bundle(tmp_path/'witness.zip', audit_minimal=True)['independent_minimality_audit']['one_minimal_under_declared_scope']
    with zipfile.ZipFile(tmp_path/'witness.zip') as z:
        assert sorted(z.namelist()) == ['after.sql','before.sql','replay.py','reproducer.sqlite','witness.json']
        metadata = json.loads(z.read('witness.json'))
        assert metadata['witness']['flag'] == 'DUPLICATE'
        assert metadata['witness']['before_query_globally_passes']


def test_greedy_reference_agrees_on_failure_and_cardinality(tmp_path):
    a = tmp_path/'greedy'; a.mkdir()
    result = invoke(a, mode='greedy')
    assert result['reduction']['one_minimal']
    assert result['reduction']['remaining_reducible_rows'] == 3


def test_reduction_is_deterministic_in_data_and_manifest(tmp_path):
    a, b = tmp_path/'a', tmp_path/'b'; a.mkdir(); b.mkdir()
    x, y = invoke(a), invoke(b)
    assert x['reduction']['remaining_table_rows'] == y['reduction']['remaining_table_rows']
    with zipfile.ZipFile(a/'witness.zip') as za, zipfile.ZipFile(b/'witness.zip') as zb:
        for name in ('after.sql','before.sql','reproducer.sqlite','replay.py'):
            # SQLite backup files might contain nondeterministic header counters.
            if name.endswith('.sql'):
                assert za.read(name) == zb.read(name)


def test_verifier_rejects_tampered_sql_even_with_original_manifest(tmp_path):
    invoke(tmp_path)
    original = tmp_path/'witness.zip'
    forged = tmp_path/'forged.zip'
    with zipfile.ZipFile(original) as z, zipfile.ZipFile(forged, 'w') as out:
        for name in z.namelist():
            data = z.read(name)
            out.writestr(name, b'SELECT 42 AS fact_id, 1 AS amount' if name=='after.sql' else data)
    with pytest.raises(ReviewError, match='hash mismatch'):
        verify_bundle(forged)


def test_verifier_rejects_zip_traversal(tmp_path):
    bundle = tmp_path/'malicious.zip'
    with zipfile.ZipFile(bundle, 'w') as z:
        z.writestr('../escape.py', b'evil')
    with pytest.raises(ReviewError, match='unexpected/missing'):
        verify_bundle(bundle)


def test_budget_exhaustion_is_not_reported_as_one_minimal(tmp_path):
    result = invoke(tmp_path, max_calls=1)
    assert result['reduction']['budget_exhausted']
    assert not result['reduction']['one_minimal']
    assert verify_bundle(tmp_path/'witness.zip')['status'] == 'REPLAY_VERIFIED'


def test_rejects_nonexistent_table_and_source_without_failure(tmp_path):
    with pytest.raises(ReviewError):
        reduce(EXAMPLE/'orders.sqlite', EXAMPLE/'before.sql', EXAMPLE/'fixed.sql',
               'orders','id','price', tables=['orders'],output=tmp_path/'none.zip')
    with pytest.raises(ReviewError,match='not an ordinary'):
        reduce(EXAMPLE/'orders.sqlite', EXAMPLE/'before.sql', EXAMPLE/'after.sql',
               'orders','id','price', tables=['ghost'],output=tmp_path/'none.zip')


def test_rejects_wrong_target_and_improper_duplicate_outputs(tmp_path):
    with pytest.raises(ReviewError,match='(?:no visible source fact|requested target|target fact)'):
        invoke(tmp_path, target_id='not-an-order')
    with pytest.raises(ReviewError,match='(?:no visible source fact|requested target|target fact)'):
        invoke(tmp_path, failure_flag='MISSING')


def test_preserves_declared_foreign_keys(tmp_path):
    db = tmp_path/'fk.sqlite'; old=tmp_path/'old.sql'; new=tmp_path/'new.sql'
    with sqlite3.connect(db) as cx:
        cx.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE orders(id INTEGER PRIMARY KEY, price INTEGER);
        CREATE TABLE supplies(id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id));
        INSERT INTO orders VALUES (1,10),(2,20),(3,30);
        INSERT INTO supplies VALUES (1,1),(2,1),(3,2),(4,3);
        ''')
    old.write_text('SELECT id AS fact_id, price AS amount FROM orders')
    new.write_text('SELECT orders.id AS fact_id,orders.price AS amount FROM orders LEFT JOIN supplies ON supplies.order_id=orders.id')
    r = reduce(db,old,new,'orders','id','price',tables=['orders','supplies'],output=tmp_path/'fk.zip')
    assert r['reduction']['one_minimal']
    assert r['reduction']['remaining_reducible_rows'] == 3
    assert verify_bundle(tmp_path/'fk.zip')['status'] == 'REPLAY_VERIFIED'


def test_sql_modification_is_not_executed_on_source(tmp_path):
    db = tmp_path/'source.sqlite'; db.write_bytes((EXAMPLE/'orders.sqlite').read_bytes())
    malicious = tmp_path/'bad.sql'
    malicious.write_text('WITH x AS (SELECT id FROM orders) DELETE FROM orders RETURNING id AS fact_id,price AS amount')
    with pytest.raises(ReviewError):
        reduce(db,EXAMPLE/'before.sql',malicious,'orders','id','price',tables=['orders'],output=tmp_path/'bad.zip')
    with sqlite3.connect(db) as cx:
        assert cx.execute('SELECT count(*) FROM orders').fetchone()[0] == 3


def test_cli_and_bundle_verify_subprocess(tmp_path):
    out = tmp_path/'cli.zip'
    cmd = [sys.executable,'-m','axiomforge.reduction','reduce', '--database',str(EXAMPLE/'orders.sqlite'),
           '--before',str(EXAMPLE/'before.sql'),'--after',str(EXAMPLE/'after.sql'),
           '--fact-table','orders','--fact-id','id','--measure','price','--tables','orders,supplies','--out',str(out)]
    proc = subprocess.run(cmd,text=True,capture_output=True,timeout=40)
    assert proc.returncode == 0,proc.stderr
    replay = subprocess.run([sys.executable,'-m','axiomforge.reduction','verify','--bundle',str(out)],
                            text=True,capture_output=True,timeout=20)
    assert replay.returncode == 0,replay.stderr


def test_large_target_outside_displayed_500_issues(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('reduction_benchmark',ROOT/'scripts/benchmark_reduction.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    create_case=module.create_case
    db,old,new = create_case(tmp_path, 510)
    out=tmp_path/'large.zip'
    r=reduce(db,old,new,'orders','id','price',tables=['orders','supplies'],
             output=out,target_id='order-000000',max_calls=500)
    assert r['reduction']['one_minimal']
    assert verify_bundle(out,audit_minimal=True)['fact_id']=='order-000000'


def test_minimality_audit_catches_unreduced_artifact(tmp_path):
    # With only one Oracle call, the archive may still reproduce the bug,
    # but must not claim that deletion possibilities have been exhausted.
    report=invoke(tmp_path,max_calls=1)
    assert not report['reduction']['one_minimal']
    with pytest.raises(ReviewError,match='one-minimality falsified'):
        verify_bundle(tmp_path/'witness.zip',audit_minimal=True)


def test_shadowed_rowid_does_not_delete_wrong_records(tmp_path):
    db=tmp_path/'rowids.sqlite'; old=tmp_path/'old.sql'; new=tmp_path/'new.sql'
    with sqlite3.connect(db) as cx:
        cx.executescript('''CREATE TABLE orders (id TEXT PRIMARY KEY, price INTEGER);
                            CREATE TABLE dim (_rowid_ INTEGER, sku TEXT);
                            INSERT INTO orders VALUES ('a',3);
                            INSERT INTO dim VALUES (1,'x'),(1,'x');''')
    old.write_text('SELECT id AS fact_id,price AS amount FROM orders')
    new.write_text('SELECT o.id AS fact_id,o.price AS amount FROM orders o CROSS JOIN dim')
    with pytest.raises(ReviewError,match='shadowed _rowid_'):
        reduce(db,old,new,'orders','id','price',tables=['orders','dim'],output=tmp_path/'invalid.zip')


def test_independent_oracle_checks_multiple_failure_types(tmp_path):
    db=tmp_path/'multiple.sqlite'; old=tmp_path/'old.sql'; new=tmp_path/'new.sql'
    with sqlite3.connect(db) as cx:
        cx.executescript("""CREATE TABLE orders(id TEXT PRIMARY KEY,price INTEGER,sku TEXT);
        INSERT INTO orders VALUES ('a',10,'x'),('b',20,'y'),('c',30,'z');
        CREATE TABLE dim(id INTEGER PRIMARY KEY,sku TEXT);
        INSERT INTO dim VALUES(1,'x'),(2,'x'),(3,'z');""")
    old.write_text('SELECT id AS fact_id,price AS amount FROM orders')
    new.write_text("""SELECT o.id AS fact_id,o.price AS amount FROM orders o
    INNER JOIN dim AS d ON d.sku=o.sku""")
    for target,flag in [('a','DUPLICATE'),('b','MISSING')]:
        out=tmp_path/f'{target}.zip'
        result=reduce(db,old,new,'orders','id','price',tables=['orders','dim'],
                      output=out,target_id=target,failure_flag=flag)
        assert result['reduction']['one_minimal']
        assert verify_bundle(out,audit_minimal=True)['flag']==flag


def test_randomized_multitable_witness_replay(tmp_path):
    import random
    for seed in range(10):
        rng=random.Random(seed)
        path=tmp_path/f'case{seed}'
        path.mkdir()
        db=path/'db.sqlite'; old=path/'before.sql'; new=path/'after.sql'
        extras=rng.randint(2,12)
        with sqlite3.connect(db) as cx:
            cx.executescript('CREATE TABLE orders(id TEXT PRIMARY KEY,price INTEGER,sku TEXT);'
                            'CREATE TABLE dim(id INTEGER PRIMARY KEY,sku TEXT);')
            cx.executemany('INSERT INTO orders VALUES (?,?,?)',
                           [(f'k{i}',rng.randint(1,100),f's{i}') for i in range(extras)])
            entries=[(f's{i}',) for i in range(extras)] + [('s0',)]
            rng.shuffle(entries)
            cx.executemany('INSERT INTO dim(sku) VALUES (?)',entries)
        old.write_text('SELECT id AS fact_id,price AS amount FROM orders')
        new.write_text('SELECT o.id AS fact_id,o.price AS amount FROM orders o LEFT JOIN dim d ON o.sku=d.sku')
        result=reduce(db,old,new,'orders','id','price',tables=['orders','dim'],
                      output=path/'witness.zip',target_id='k0',max_calls=500)
        assert result['reduction']['one_minimal']
        assert result['reduction']['remaining_reducible_rows']==3
        assert verify_bundle(path/'witness.zip',audit_minimal=True)['status']=='REPLAY_VERIFIED'


def test_multihop_cte_join_witness(tmp_path):
    db=tmp_path/'db.sqlite'; old=tmp_path/'old.sql'; new=tmp_path/'new.sql'
    with sqlite3.connect(db) as cx:
        cx.executescript('''
        CREATE TABLE orders(id TEXT PRIMARY KEY,sku TEXT,price INTEGER);
        CREATE TABLE supplies(id INTEGER PRIMARY KEY,sku TEXT);
        CREATE TABLE tags(id INTEGER PRIMARY KEY,supply_id INTEGER);
        INSERT INTO orders VALUES ('a','x',7),('b','y',11),('c','z',19);
        INSERT INTO supplies VALUES (1,'x'),(2,'y'),(3,'z');
        INSERT INTO tags VALUES (1,1),(2,1),(3,2),(4,3);
        ''')
    old.write_text('SELECT id AS fact_id,price AS amount FROM orders')
    new.write_text('''WITH base AS (SELECT o.id,o.price,s.id AS sid FROM orders o
        LEFT JOIN supplies s ON o.sku=s.sku),
        expanded AS (SELECT b.id,b.price,t.id AS tid FROM base b LEFT JOIN tags t ON t.supply_id=b.sid)
        SELECT id AS fact_id,price AS amount FROM expanded''')
    output=tmp_path/'cte.zip'
    report=reduce(db,old,new,'orders','id','price',
                  tables=['orders','supplies','tags'],output=output,
                  target_id='a',failure_flag='DUPLICATE')
    assert report['reduction']['one_minimal']
    assert report['reduction']['remaining_reducible_rows']==4
    assert verify_bundle(output,audit_minimal=True)['status']=='REPLAY_VERIFIED'


def test_independently_bruteforced_small_fixture_global_lower_bound():
    # Stronger external test than 1-minimality: exhaustively check every database
    # subset with one or two rows. This proves a 3-row lower bound only for
    # this finite 8-row source and fixed regression predicate.
    from axiomforge.reduction import _snapshot,_rowids,_oracle
    from contextlib import closing
    from itertools import combinations
    source=EXAMPLE/'orders.sqlite'
    with closing(_snapshot(source)) as db:
        rows=_rowids(db,['orders','supplies'])
        flat=[(t,r) for t,x in rows.items() for r in x]
        with sqlite3.connect(source) as con:
            original=con.execute("SELECT id FROM orders WHERE id='order-1'").fetchone()
        assert original
        old=(EXAMPLE/'before.sql').read_text()
        new=(EXAMPLE/'after.sql').read_text()
        checked=0
        for retained_size in (0,1,2):
            for indices in combinations(range(len(flat)),retained_size):
                saved={flat[i] for i in indices}
                db.execute('SAVEPOINT attempt')
                try:
                    for table,rowid in flat:
                        if (table,rowid) not in saved:
                            db.execute(f'DELETE FROM "{table}" WHERE _rowid_=?',(rowid,))
                    assert not _oracle(db,'orders','id','price',old,new,'order-1','DUPLICATE',max_rows=500)
                    checked+=1
                finally:
                    db.execute('ROLLBACK TO attempt')
                    db.execute('RELEASE attempt')
        assert checked==1+8+28


def test_delete_trigger_rejected_for_causal_integrity(tmp_path):
    db=tmp_path/'trigger.sqlite'; old=tmp_path/'old.sql'; new=tmp_path/'new.sql'
    with sqlite3.connect(db) as cx:
        cx.executescript('''CREATE TABLE orders(id TEXT PRIMARY KEY,price INTEGER);
        CREATE TABLE dim(id INTEGER PRIMARY KEY);
        INSERT INTO orders VALUES('a',3);
        INSERT INTO dim VALUES(1),(2);
        CREATE TRIGGER unsafe AFTER DELETE ON dim BEGIN DELETE FROM orders; END;''')
    old.write_text('SELECT id AS fact_id,price AS amount FROM orders')
    new.write_text('SELECT o.id AS fact_id,o.price AS amount FROM orders o CROSS JOIN dim')
    with pytest.raises(ReviewError,match='triggers'):
        reduce(db,old,new,'orders','id','price',tables=['orders','dim'],output=tmp_path/'invalid.zip')
