from __future__ import annotations
import csv
import json
import random
import sqlite3
from pathlib import Path

import pytest
from axiomforge.semantics.pipeline_audit import AuditConfig, AuditError, audit_files, verify_receipt, write_receipt
from axiomforge.semantics.materialized import check_materialized
from axiomforge.semantics.factorized import Stage, factorized, evaluate_manifest
from axiomforge.semantics.factorized_cli import main as factorized_cli
from axiomforge.semantics.sql_compiler import compile_factorized_sql, execute_readonly, ident
from axiomforge.semantics.sql_cli import main as sql_cli


@pytest.fixture
def fixture_path():
    return Path(__file__).resolve().parents[1] / 'examples' / 'pipeline_audit'


def test_materialized_output_matches_faulty_query_but_contract_still_fails(fixture_path):
    p=fixture_path
    result=audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=p/'materialized.csv')
    assert result['materialized']['status']=='EXACT_QUERY_OUTPUT'
    assert result['verdict']=='CANCELLED_AT_GLOBAL_TOTAL'
    assert result['release_gate']=='FAIL'


def test_mismatched_materialized_result_detected(fixture_path,tmp_path):
    actual=tmp_path/'materialized.csv'
    actual.write_text('type,cost\njaffle,150\njaffle_relisted,100\nbeverage,10\nbeverage_relisted,0\n')
    p=fixture_path
    result=audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=actual)
    assert result['materialized']['status']=='MATERIALIZED_OUTPUT_DRIFT'
    assert result['materialized']['mismatched_groups']==1
    assert result['release_gate']=='FAIL'


def test_materialized_drift_detection_with_triple_executions(fixture_path,tmp_path):
    p=fixture_path
    actual=tmp_path/'output.csv'
    actual.write_text('type,cost\njaffle,100\njaffle_relisted,100\nbeverage,10\nbeverage_relisted,0\n')
    receipt=tmp_path/'evidence.json'
    write_receipt(receipt,audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=actual))
    assert verify_receipt(receipt,p/'facts.csv',p/'dimensions.csv',actual)['status']=='REAL_CSV_THREE_BACKENDS_REEXECUTED'
    actual.write_text(actual.read_text().replace('jaffle,100','jaffle,99'))
    with pytest.raises(AuditError,match='source changed'):
        verify_receipt(receipt,p/'facts.csv',p/'dimensions.csv',actual)
    with pytest.raises(AuditError,match='required exactly'):
        verify_receipt(receipt,p/'facts.csv',p/'dimensions.csv')


def test_materialized_duplicate_segment_rejected(fixture_path,tmp_path):
    actual=tmp_path/'output.csv'
    actual.write_text('type,cost\nx,10\nx,10\n')
    p=fixture_path
    with pytest.raises(AuditError,match='more than once'):
        audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=actual)


def test_two_stage_factorized_cross_engine_replay():
    facts=[{'sku':'A','cost':100},{'sku':'B','cost':100},{'sku':'C','cost':0}]
    stages=[Stage('region','r',True,[{'sku':'A','r':'x'},{'sku':'A','r':'y'},{'sku':'B','r':None}]),
            Stage('tag','t',True,[{'sku':'A','t':'green'},{'sku':'B','t':'green'}, {'sku':'C','t':'blue'}])]
    result=factorized(facts,stages)
    assert result['mode']=='MODEL_AND_REAL_BACKENDS'
    assert result['model_residual']==0 and result['abnormal_fact_rows']==3
    assert result['validation']['status']=='PANDAS_AND_SQLITE_MATERIALIZED_AGREEMENT'


def test_large_factorized_plan_is_never_claimed_as_real_backend_experiment():
    facts=[{'sku':'A','cost':1}]
    stages=[Stage(f'stage{i}',f'label{i}',False,[{'sku':'A',f'label{i}':str(j)} for j in range(50)]) for i in range(5)]
    result=factorized(facts,stages,materialize_limit=10000)
    assert result['would_materialize_rows']==50**5
    assert result['model_after']==50**5
    assert result['mode']=='FACTORIZED_MODEL_ONLY'
    assert result['validation']['status']=='NOT_MATERIALIZED_BUDGET'


@pytest.mark.parametrize('operation',['sum','count'])
def test_factorized_random_small_programs_against_two_real_engines(operation):
    rng=random.Random(0x5AFEA)
    keys=['A','B','C']
    for trial in range(70):
        facts=[{'sku':rng.choice(keys),'cost':rng.randrange(-5,6)} for _ in range(rng.randrange(1,6))]
        stages=[]
        for i in range(rng.randrange(1,5)):
            col=f'label{i}'
            dims=[{'sku':rng.choice(keys),col:rng.choice(['x','y',None])} for _ in range(rng.randrange(1,8))]
            stages.append(Stage(f's{i}',col,bool(rng.getrandbits(1)),dims))
        result=factorized(facts,stages,operation=operation,materialize_limit=30000)
        assert result['mode']=='MODEL_AND_REAL_BACKENDS'
        assert result['validation']['executions']['pandas']==result['validation']['executions']['sqlite']


def test_external_manifest_and_cli(capsys,tmp_path):
    base=Path(__file__).resolve().parents[1] / 'examples' / 'factorized'
    manifest=base/'pipeline.json'
    result=evaluate_manifest(manifest)
    assert result['verdict']=='HIDDEN_AT_GLOBAL_TOTAL'
    assert factorized_cli(['--manifest',str(manifest),'--gate'])==3
    assert factorized_cli(['--manifest',str(manifest)])==0


def test_sql_compiler_executes_on_real_sqlite_db(tmp_path):
    facts=[{'sku':'A','cost':100},{'sku':'B','cost':100},{'sku':'C','cost':0}]
    stages=[Stage('s0','r',True,[{'sku':'A','r':'x'},{'sku':'A','r':'y'},{'sku':'B','r':None}]),
            Stage('s1','t',True,[{'sku':'A','t':'x'}, {'sku':'B','t':'x'}, {'sku':'C','t':'z'}])]
    db=tmp_path/'actual.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE facts (sku TEXT, cost INTEGER)')
        conn.execute('CREATE TABLE regions (sku TEXT, r TEXT)')
        conn.execute('CREATE TABLE tags (sku TEXT, t TEXT)')
        conn.executemany('INSERT INTO facts VALUES (?,?)',[(x['sku'],x['cost']) for x in facts])
        conn.executemany('INSERT INTO regions VALUES (?,?)',[(x['sku'],x['r']) for x in stages[0].dims])
        conn.executemany('INSERT INTO tags VALUES (?,?)',[(x['sku'],x['t']) for x in stages[1].dims])
    sql=compile_factorized_sql(facts_table='facts',dimension_tables=['regions','tags'],
                               key='sku',measure='cost',segments=['r','t'],drops=[True,True])
    result=execute_readonly(db,sql)
    reference=factorized(facts,stages)
    assert result['global_residual']==reference['model_residual']==0
    assert result['affected_entities']==3
    assert result['status']=='READONLY_SQLITE_QUERY_EXECUTED'
    # No mutations: the database is opened read-only.
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM facts').fetchone()[0] == 3
    with pytest.raises(AuditError):
        compile_factorized_sql(facts_table='facts;DROP',dimension_tables=['regions'],key='sku',measure='cost',segments=['r'],drops=[True])


def test_sql_cli_generates_runable_query(tmp_path):
    base=Path(__file__).resolve().parents[1] / 'examples' / 'factorized'
    path=tmp_path/'generated.sql'
    assert sql_cli(['--manifest',str(base/'pipeline.json'),'--fact-table','facts',
                    '--dimension-tables','regions','tags','--sql-out',str(path)])==0
    assert 'JOIN' in path.read_text()
    assert 'SELECT' in path.read_text()
    assert 'INSERT' not in path.read_text()


def test_sql_identifier_validation():
    for bad in ('schema.name.x;',';drop table','foo bar','x\" OR 1=1',''):
        with pytest.raises(AuditError):
            ident(bad,dotted=True)


def test_real_corrected_fixture_preserves_semantics_and_unused_duplicate(fixture_path):
    p=fixture_path
    result=audit_files(p/'facts.csv',p/'dimensions_corrected.csv',AuditConfig(),
                       actual_path=p/'materialized_corrected.csv')
    assert result['verdict']=='EXACTLY_ONCE_PRESERVED'
    assert result['release_gate']=='PASS'
    assert result['materialized']['status']=='EXACT_QUERY_OUTPUT'
    assert result['counts']['unreferenced_duplicate_dimension_keys']==1


def test_materialized_duplicate_header_rejected(fixture_path,tmp_path):
    actual=tmp_path/'broken.csv'
    actual.write_text('type,cost,cost\nfoo,1,1\n')
    p=fixture_path
    with pytest.raises(AuditError,match='exactly segment'):
        audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=actual)


def test_hidden_segment_swap_with_preserved_global_sum_detected(fixture_path,tmp_path):
    p=fixture_path
    actual=tmp_path/'swapped.csv'
    # Aggregate value is unchanged (210), but downstream segment report is wrong.
    actual.write_text('type,cost\njaffle,110\njaffle_relisted,90\nbeverage,10\nbeverage_relisted,0\n')
    result=audit_files(p/'facts.csv',p/'dimensions.csv',AuditConfig(),actual_path=actual)
    assert result['materialized']['mismatched_groups']==2
    assert sum(int(x['cost']) for x in csv.DictReader(actual.open())) == 210


def test_readonly_sql_preflight_rejects_null_business_key(tmp_path):
    db=tmp_path/'unsafe.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE facts (sku TEXT, cost INTEGER)')
        conn.execute('CREATE TABLE regions (sku TEXT, region TEXT)')
        conn.execute('INSERT INTO facts VALUES (NULL,1)')
        conn.execute('INSERT INTO regions VALUES ("A","x")')
    sql=compile_factorized_sql(facts_table='facts',dimension_tables=['regions'],
                               key='sku',measure='cost',segments=['region'],drops=[False])
    with pytest.raises(AuditError,match='FACT DOMAIN REJECTED'):
        execute_readonly(db,sql,preflight=('facts',['regions'],'sku','cost',['region']))


def test_readonly_sql_preflight_rejects_null_dimension_business_key(tmp_path):
    db=tmp_path/'unsafe.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE facts (sku TEXT, cost INTEGER)')
        conn.execute('CREATE TABLE regions (sku TEXT, region TEXT)')
        conn.execute('INSERT INTO facts VALUES ("A",100)')
        conn.execute('INSERT INTO regions VALUES (NULL,"x")')
    sql=compile_factorized_sql(facts_table='facts',dimension_tables=['regions'],
                               key='sku',measure='cost',segments=['region'],drops=[False])
    with pytest.raises(AuditError,match='DIMENSION DOMAIN REJECTED'):
        execute_readonly(db,sql,preflight=('facts',['regions'],'sku','cost',['region']))


def test_factorized_refuses_to_mistake_cheap_final_result_for_safe_intermediate():
    f=[{'sku':'A','cost':10}]
    first=Stage('fanout','segment',False,[{'sku':'A','segment':'x'} for _ in range(100)])
    second=Stage('filter','group',True,[{'sku':'A','group':None}])
    a=factorized(f,[first,second],materialize_limit=20)
    assert a['would_materialize_rows']==0
    assert a['unfiltered_join_rows_upper_bound']==100
    assert a['mode']=='FACTORIZED_MODEL_ONLY'
