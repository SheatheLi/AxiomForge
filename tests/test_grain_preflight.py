from __future__ import annotations
import csv
import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from axiomforge.semantics.grain_preflight import Contract, PreflightError, analyze, candidate_keys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / 'examples' / 'jaffle_shop'
needs_external = pytest.mark.skipif(
    not (FIX/'raw_products.csv').exists() or not (FIX/'raw_supplies.csv').exists(),
    reason='external upstream CSV not bundled; run scripts/fetch_jaffle_seeds.py')


def _source_sha(path: pathlib.Path):
    b = path.read_bytes()
    return hashlib.sha1(f'blob {len(b)}\0'.encode() + b).hexdigest()


@needs_external
def test_external_github_seed_bytes_identical():
    assert _source_sha(FIX / 'raw_supplies.csv') == '058c92bca08517e01fea23777593fb13e8ca0de5'
    assert _source_sha(FIX / 'raw_products.csv') == '125285432532ab6add35bd977330019670d8f996'


@needs_external
def test_upstream_seed_risk_quantified():
    r = analyze(FIX/'raw_products.csv',FIX/'raw_supplies.csv',Contract('sku','sku','sku','price'))
    st = r['statistics']
    assert r['status'] == 'BLOCK_GRAIN_REGRESSION'
    assert (st['fact_rows'],st['dimension_rows'],st['naive_join_rows']) == (10,65,65)
    assert (st['fact_measure_before'],st['naive_join_measure'],st['naive_measure_delta']) == (8800,62200,53400)
    assert st['unsafe_fact_rows'] == 10
    assert ['id','sku'] in r['observed_keys']['right']
    assert ['id'] not in r['observed_keys']['right']
    assert ['sku'] not in r['observed_keys']['right']
    assert st['independent_sql_execution'] == 'VERIFIED'


def _csv(path, fields, values):
    with path.open('w',encoding='utf-8',newline='') as f:
        w = csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(values)


@needs_external
def test_real_source_preaggregate_repairs_grain(tmp_path):
    with (FIX/'raw_supplies.csv').open(newline='',encoding='utf-8') as f:
        src=list(csv.DictReader(f))
    sums={}
    for r in src: sums[r['sku']]=sums.get(r['sku'],0)+int(r['cost'])
    right=tmp_path/'summary.csv'
    _csv(right,['sku','supply_cost'],[{'sku':k,'supply_cost':v} for k,v in sums.items()])
    r=analyze(FIX/'raw_products.csv',right,Contract('sku','sku','sku','price',True,True))
    assert r['status']=='PASS_GRAIN_CONTRACT'
    assert r['statistics']['naive_join_rows']==10
    assert r['statistics']['naive_join_measure']==8800
    assert r['statistics']['missing_reference_rows']==0


def test_missing_ref_is_not_ignored_as_join_multiplicity(tmp_path):
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','sku','price'],[{'id':'1','sku':'a','price':'10'},{'id':'2','sku':'b','price':'20'}])
    _csv(d,['sku','seg'],[{'sku':'a','seg':'x'}])
    plain=analyze(f,d,Contract('sku','sku','id','price'))
    assert plain['status']=='PASS_GRAIN_CONTRACT'
    assert plain['statistics']['missing_reference_rows']==1
    strict=analyze(f,d,Contract('sku','sku','id','price',True,True))
    assert strict['status']=='BLOCK_MISSING_REFERENCE'


def test_informational_for_legitimate_one_to_many(tmp_path):
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','k','v'],[{'id':'1','k':'a','v':'11'}])
    _csv(d,['k','t'],[{'k':'a','t':'1'},{'k':'a','t':'2'}])
    r=analyze(f,d,Contract('k','k','id','v',False))
    assert r['status']=='INFORMATIONAL_NO_GRAIN_CONTRACT'
    assert r['statistics']['naive_join_measure']==22


def test_invalid_grain_is_rejected(tmp_path):
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','k','v'],[{'id':'1','k':'a','v':'10'},{'id':'1','k':'b','v':'-10'}])
    _csv(d,['k','t'],[{'k':'a','t':'1'}])
    with pytest.raises(PreflightError,match='fact_id'):
        analyze(f,d,Contract('k','k','id','v'))


def test_float_inputs_are_rejected(tmp_path):
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','k','v'],[{'id':'1','k':'a','v':'1.5'}])
    _csv(d,['k','t'],[{'k':'a','t':'1'}])
    with pytest.raises(PreflightError,match='integer'):
        analyze(f,d,Contract('k','k','id','v'))


def test_missing_columns_fail_closed(tmp_path):
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','k','v'],[{'id':'1','k':'a','v':'10'}])
    _csv(d,['k','t'],[{'k':'a','t':'1'}])
    with pytest.raises(PreflightError,match='missing'):
        analyze(f,d,Contract('k','invalid','id','v'))


@needs_external
def test_cli_block_and_exit_code(tmp_path):
    cmd=[sys.executable,'-m','axiomforge.semantics.grain_preflight',
         '--left',str(FIX/'raw_products.csv'),'--right',str(FIX/'raw_supplies.csv'),
         '--out',str(tmp_path/'result.json')]
    proc=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
    assert proc.returncode==2,proc.stderr
    assert json.loads((tmp_path/'result.json').read_text())['status']=='BLOCK_GRAIN_REGRESSION'
    proc2=subprocess.run(cmd+['--informational'],capture_output=True,text=True,timeout=15)
    assert proc2.returncode==0,proc2.stderr


@needs_external
def test_real_upstream_style_preaggregation_is_not_flagged():
    r=analyze(FIX/'raw_products.csv',FIX/'raw_supplies.csv',
              Contract('sku','sku','sku','price',True,True,'preaggregated_join'))
    assert r['status']=='PASS_GRAIN_CONTRACT'
    assert r['statistics']['candidate_join_rows']==10
    assert r['statistics']['candidate_join_measure']==8800
    assert r['statistics']['raw_source_fanout_rows']==10
    assert r['statistics']['unsafe_fact_rows']==0
    assert r['affected']==[]


@needs_external
def test_candidate_sql_selection_changes_verdict_not_underlying_data():
    a=analyze(FIX/'raw_products.csv',FIX/'raw_supplies.csv',
              Contract('sku','sku','sku','price',True,False,'raw_join'))
    b=analyze(FIX/'raw_products.csv',FIX/'raw_supplies.csv',
              Contract('sku','sku','sku','price',True,False,'preaggregated_join'))
    assert a['source']==b['source']
    assert a['status']=='BLOCK_GRAIN_REGRESSION'
    assert b['status']=='PASS_GRAIN_CONTRACT'
    assert a['statistics']['candidate_join_measure']==62200
    assert b['statistics']['candidate_join_measure']==8800


def test_offline_self_owned_example():
    src=ROOT/'examples'/'grain_demo'
    r=analyze(src/'facts.csv',src/'supplies.csv',Contract('sku','sku','id','price'))
    assert r['status']=='BLOCK_GRAIN_REGRESSION'
    assert r['statistics']['naive_join_measure']==400
    assert r['statistics']['fact_measure_before']==300


def test_executable_readonly_sql_against_database(tmp_path):
    import sqlite3
    from axiomforge.semantics.grain_preflight import diagnostic_sql, verify_readonly_database
    f=ROOT/'examples/grain_demo/facts.csv'
    d=ROOT/'examples/grain_demo/supplies.csv'
    contract=Contract('sku','sku','id','price')
    database=tmp_path/'outside.sqlite'
    conn=sqlite3.connect(database)
    conn.execute('CREATE TABLE raw_products(id TEXT,sku TEXT,price INTEGER)')
    conn.execute('CREATE TABLE raw_supplies(sku TEXT,cost INTEGER)')
    conn.executemany('INSERT INTO raw_products VALUES (?,?,?)',[('order-1','A',100),('order-2','B',200)])
    conn.executemany('INSERT INTO raw_supplies VALUES (?,?)',[('A',10),('A',20),('B',30)])
    conn.commit();conn.close()
    result=analyze(f,d,contract)
    check=verify_readonly_database(database,'raw_products','raw_supplies',contract,result)
    assert check['status']=='READONLY_DATABASE_AGREES'
    assert check['candidate_measure']==400
    assert 'WITH matches AS' in check['diagnostic_sql']
    c2=Contract('sku','sku','id','price',True,False,'preaggregated_join')
    r2=analyze(f,d,c2)
    verify_readonly_database(database,'raw_products','raw_supplies',c2,r2)
    assert diagnostic_sql('raw_products','raw_supplies',c2)
    conn=sqlite3.connect(database);conn.execute('UPDATE raw_products SET price=500 WHERE id="order-1"');conn.commit();conn.close()
    with pytest.raises(PreflightError,match='mismatch'):
        verify_readonly_database(database,'raw_products','raw_supplies',contract,result)


def test_identifier_rejects_injection():
    from axiomforge.semantics.grain_preflight import diagnostic_sql
    with pytest.raises(PreflightError,match='unsafe identifier'):
        diagnostic_sql('raw_products; DROP TABLE raw_products','raw_supplies',Contract('sku','sku','id','price'))


def test_evidence_verifier_reexecutes_and_rejects_statistics_mutation(tmp_path):
    from axiomforge.semantics.grain_verify import verify_receipt
    f=ROOT/'examples/grain_demo/facts.csv';d=ROOT/'examples/grain_demo/supplies.csv'
    result=analyze(f,d,Contract('sku','sku','id','price'))
    receipt=tmp_path/'receipt.json'
    receipt.write_text(json.dumps(result),encoding='utf-8')
    assert verify_receipt(receipt,f,d)['status']=='EXTERNAL_GRAIN_EVIDENCE_REPLAY_VERIFIED'
    result['statistics']['naive_join_measure']=99999
    receipt.write_text(json.dumps(result),encoding='utf-8')
    with pytest.raises(PreflightError,match='mismatch'):
        verify_receipt(receipt,f,d)


def test_evidence_verifier_rejects_changed_source(tmp_path):
    from axiomforge.semantics.grain_verify import verify_receipt
    f=tmp_path/'facts.csv';d=tmp_path/'dim.csv'
    _csv(f,['id','sku','price'],[{'id':'1','sku':'x','price':'7'}])
    _csv(d,['sku','cost'],[{'sku':'x','cost':'11'}])
    result=analyze(f,d,Contract('sku','sku','id','price'))
    receipt=tmp_path/'receipt.json'
    receipt.write_text(json.dumps(result),encoding='utf-8')
    f.write_text(f.read_text().replace('7','8'))
    with pytest.raises(PreflightError,match='mismatch'):
        verify_receipt(receipt,f,d)


def test_strong_sql_unique_baseline_ties_for_detection():
    import importlib.util
    spec=importlib.util.spec_from_file_location('baseline',ROOT/'scripts/compare_grain_baseline.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    d=ROOT/'examples/grain_demo'
    result=module.run(d/'facts.csv',d/'supplies.csv','id')
    assert result['detection_gain_over_baseline']==0
    assert result['baseline_affected_keys']==1
