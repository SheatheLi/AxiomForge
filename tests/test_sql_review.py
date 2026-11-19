"""Black-box and negative-path tests for operator-facing real-SQL regression."""
from __future__ import annotations
import json
import pathlib
import sqlite3
import subprocess
import sys

import pytest
from axiomforge.sql_review import ReviewError, inspect, save_receipt, verify, make_html

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / 'examples' / 'sql_review'


def check(db, files, **kwargs):
    return inspect(db, 'orders', 'id', 'price', files, **kwargs)


def test_sample_real_sql_detects_hidden_offsetting_error():
    report = check(FIX/'orders.sqlite', [FIX/'before.sql', FIX/'after.sql'])
    assert report['status'] == 'REGRESSION'
    before, after = report['evaluations']
    assert before['status'] == 'PASS'
    assert after['counts']['duplicated_facts'] == 2
    assert after['counts']['result_rows'] == 5
    assert after['measures']['total_delta'] == 0
    assert after['measures']['sum_abs_per_id_delta'] == 200
    assert {a['fact_id'] for a in after['affected']} == {'order-1','order-3'}


def test_fixed_candidate_passes_and_proves_improvement():
    assert check(FIX/'orders.sqlite', [FIX/'after.sql', FIX/'fixed.sql'])['status'] == 'IMPROVED'
    assert check(FIX/'orders.sqlite', [FIX/'fixed.sql'])['status'] == 'PASS'


def test_receipt_reexecutes_and_tamper_is_rejected(tmp_path):
    report = check(FIX/'orders.sqlite', [FIX/'before.sql', FIX/'after.sql'])
    path = tmp_path/'review.json'
    save_receipt(path, report)
    assert verify(path)['status'] == 'REPLAY_VERIFIED'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['payload']['status'] = 'PASS'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ReviewError,match='digest'):
        verify(path)


def test_sql_change_detected_even_if_receipt_digest_replaced(tmp_path):
    db = tmp_path/'work.db'
    db.write_bytes((FIX/'orders.sqlite').read_bytes())
    sql = tmp_path/'query.sql'
    sql.write_text((FIX/'fixed.sql').read_text())
    report = check(db, [sql])
    receipt = tmp_path/'receipt.json'
    save_receipt(receipt, report)
    sql.write_text((FIX/'after.sql').read_text())
    with pytest.raises(ReviewError,match='mismatch'):
        verify(receipt)


def test_actual_sql_read_only_cannot_mutate(tmp_path):
    query = tmp_path/'bad.sql'
    for sql in ('DELETE FROM orders', 'DROP TABLE orders',
                'WITH changed AS (DELETE FROM orders RETURNING *) SELECT * FROM changed',
                'SELECT id AS fact_id, price AS amount FROM orders; DELETE FROM orders'):
        query.write_text(sql)
        with pytest.raises(ReviewError):
            check(FIX/'orders.sqlite', [query])
    assert check(FIX/'orders.sqlite', [FIX/'fixed.sql'])['status'] == 'PASS'


def test_rejects_unexpected_unknown_identity(tmp_path):
    query = tmp_path/'unseen.sql'
    query.write_text("SELECT id AS fact_id, price AS amount FROM orders UNION ALL SELECT 'ghost', 10")
    result = check(FIX/'orders.sqlite',[query])['evaluations'][0]
    assert result['counts']['unexpected_result_ids'] == 1
    assert result['status'] == 'BLOCK'


def test_rejects_untraceable_aggregation(tmp_path):
    query = tmp_path/'grouped.sql'
    query.write_text('SELECT sku, SUM(price) AS amount FROM orders GROUP BY sku')
    with pytest.raises(ReviewError,match='emit exactly one'):
        check(FIX/'orders.sqlite',[query])


def test_rejects_non_integer_money_without_implicit_rounding(tmp_path):
    query = tmp_path/'decimal.sql'
    query.write_text('SELECT id AS fact_id, price / 2.0 AS amount FROM orders')
    with pytest.raises(ReviewError,match='INTEGER'):
        check(FIX/'orders.sqlite',[query])


def test_rejects_non_unique_source_id(tmp_path):
    db = tmp_path/'duplicates.db'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE orders(id TEXT,price INTEGER)')
        con.execute("INSERT INTO orders VALUES ('x',1),('x',2)")
    query = tmp_path/'query.sql'
    query.write_text('SELECT id AS fact_id,price AS amount FROM orders')
    with pytest.raises(ReviewError,match='not unique'):
        check(db,[query])


def test_report_escapes_untrusted_text(tmp_path):
    db = tmp_path/'hostile.db'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE orders(id TEXT,price INTEGER)')
        con.execute('INSERT INTO orders VALUES (?,?)',('<script>alert(1)</script>',4))
    query = tmp_path/'query.sql'
    query.write_text('SELECT id AS fact_id, price * 2 AS amount FROM orders')
    report = check(db,[query])
    html = tmp_path/'review.html'
    make_html(html, report)
    data = html.read_text()
    assert '&lt;script&gt;' in data and '<script>alert(1)</script>' not in data


def test_cli_exit_status_and_replay(tmp_path):
    receipt = tmp_path/'receipt.json'
    html = tmp_path/'report.html'
    command = [sys.executable,'-m','axiomforge.sql_review','compare',
               '--database',str(FIX/'orders.sqlite'),
               '--fact-table','orders','--fact-id','id','--measure','price',
               '--before',str(FIX/'before.sql'),'--after',str(FIX/'after.sql'),
               '--out',str(receipt),'--html',str(html)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2, result.stderr
    assert receipt.is_file() and html.is_file()
    verify_command = subprocess.run([sys.executable,'-m','axiomforge.sql_review','verify',
                                     '--receipt',str(receipt)],capture_output=True,text=True,timeout=15)
    assert verify_command.returncode == 0,verify_command.stderr


def test_bounded_queries_cannot_silently_pass(tmp_path):
    query = tmp_path/'cartesian.sql'
    query.write_text('SELECT o.id AS fact_id, o.price AS amount FROM orders o CROSS JOIN supplies s')
    with pytest.raises(ReviewError,match='max-rows'):
        check(FIX/'orders.sqlite',[query],max_rows=3)


def test_missing_result_is_detected(tmp_path):
    query = tmp_path/'drop.sql'
    query.write_text("SELECT id AS fact_id,price AS amount FROM orders WHERE sku <> 'B'")
    report = check(FIX/'orders.sqlite',[query])
    assert report['evaluations'][0]['counts']['missing_facts'] == 1


def test_sql_identifier_injection_blocked():
    with pytest.raises(ReviewError,match='identifier'):
        inspect(FIX/'orders.sqlite','orders;DROP TABLE orders','id','price',[FIX/'fixed.sql'])


def test_double_click_script_demo_creates_real_report():
    proc = subprocess.run([sys.executable, str(ROOT/'启动审查.py'), '--demo'],
                          cwd=ROOT, capture_output=True,text=True,timeout=15)
    assert proc.returncode == 0, proc.stderr
    assert 'Demo: REGRESSION' in proc.stdout
    path = ROOT/'results/sample.html'
    assert path.is_file()
    assert 'REGRESSION' in path.read_text(encoding='utf-8')
