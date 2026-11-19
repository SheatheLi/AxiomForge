"""Public GitHub issue mechanisms, real SQLite execution, negative controls and adversarial inputs."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

import pytest

from axiomforge.incremental import inspect_database, IncrementalError
from axiomforge.incremental_reduction import reduce_issue,emit_bundle

ROOT=Path(__file__).resolve().parents[1]
CASES=ROOT/'examples'/'external_issues'


def assess(name, keys, sql, grain='row', null_safe=False):
    return inspect_database(CASES/(name+'.sqlite'),target='target',stage='stage',
           keys=keys,delete_sql=(CASES/(sql+'.sql')).read_text('utf8'),
           grain=grain,null_safe=null_safe)


def test_public_issue_11799_composite_cross_product_false_deletes_six_rows():
    r=assess('composite_key',['num_column','str_column'],'composite_bug')
    assert r['status']=='RISK'
    assert r['counts']['actual_deletes']==13
    assert r['counts']['expected_deletes']==7
    assert r['counts']['unintended_deletes']==6
    assert 'UNINTENDED_DELETE' in r['flags']
    assert not r['counts']['duplicate_stage_keys']
    assert not r['counts']['duplicate_target_keys']


def test_issue_11799_real_tuple_fixed_control_passes():
    r=assess('composite_key',['num_column','str_column'],'composite_fixed')
    assert r['status']=='PASS' and r['counts']['unintended_deletes']==0
    assert r['counts']['actual_deletes']==r['counts']['expected_deletes']==7


def test_public_issue_5691_nullable_key_matching_gap():
    r=assess('nullable_key',['type','name'],'nullable_bug')
    assert 'NULL_KEY' in r['flags'] and 'NULL_MATCH_GAP' in r['flags']
    assert r['counts']['null_stage_rows']==1
    strict=assess('nullable_key',['type','name'],'nullable_bug',null_safe=True)
    assert 'MISSED_DELETE' in strict['flags']
    assert strict['counts']['missed_deletes']==1


def test_issue_5691_null_safe_opt_in_control_passes():
    r=assess('nullable_key',['type','name'],'nullable_fixed',null_safe=True)
    assert r['status']=='PASS'
    assert r['counts']['expected_deletes']==r['counts']['actual_deletes']==1


def test_issue_10432_duplicate_merge_source_mechanism_matches_target():
    r=assess('source_collision',['key'],'source_collision')
    assert 'MULTIPLE_SOURCE_MATCH' in r['flags']
    assert r['counts']['duplicate_stage_keys']==1
    assert r['examples']['multiple_source_matches']==[{'key':[9],'source_rows':2,'target_rows':1}]


def test_issue_11397_partition_replacement_is_valid_despite_nonunique_partition():
    good=assess('partition_key',['date_day'],'partition_replace',grain='partition')
    assert good['status']=='PASS'
    assert good['counts']['actual_deletes']==2
    strict=assess('partition_key',['date_day'],'partition_replace',grain='row')
    assert 'STAGE_KEY_COLLISION' in strict['flags']
    assert 'TARGET_KEY_COLLISION' in strict['flags']


def test_original_files_never_mutated():
    db=CASES/'composite_key.sqlite'
    original=hashlib.sha256(db.read_bytes()).digest()
    assess('composite_key',['num_column','str_column'],'composite_bug')
    assert hashlib.sha256(db.read_bytes()).digest()==original


@pytest.mark.parametrize('sql',[
    'DELETE FROM stage',
    'DROP TABLE target',
    'UPDATE target SET num_column=999',
    'DELETE FROM target; DELETE FROM stage',
    'DELETE FROM target WHERE 1=1 RETURNING num_column',
    "DELETE FROM target WHERE 1=1; ATTACH DATABASE '/tmp/x' AS private",
])
def test_untrusted_or_unscoped_SQL_is_refused(sql):
    with pytest.raises(IncrementalError):
        inspect_database(CASES/'composite_key.sqlite',target='target',stage='stage',
                         keys=['num_column','str_column'],delete_sql=sql)


@pytest.mark.parametrize('name', ['other; DROP TABLE target','sqlite_master','x-y'])
def test_invalid_table_identifier_refused(name):
    with pytest.raises(IncrementalError):
        inspect_database(CASES/'composite_key.sqlite',target=name,stage='stage',
                         keys=['num_column','str_column'],delete_sql='DELETE FROM target WHERE 1=1')


def test_one_minimal_public_issue_11799_and_independent_no_library_replay(tmp_path):
    sql=(CASES/'composite_bug.sql').read_text('utf8')
    db,receipt=reduce_issue(CASES/'composite_key.sqlite',target='target',stage='stage',
        keys=['num_column','str_column'],delete_sql=sql,flag='UNINTENDED_DELETE')
    assert receipt['reduction']['retained_rows']==3
    assert receipt['reduction']['independent_1_minimal'] is True
    assert receipt['failure']=={'flag':'UNINTENDED_DELETE','key':[8,'tst']}
    try:emit_bundle(db,receipt,candidate_sql=sql,out=tmp_path/'issue.zip',
        replay_script=ROOT/'scripts'/'replay_incremental.py')
    finally:db.close()
    with zipfile.ZipFile(tmp_path/'issue.zip') as z:
        assert set(z.namelist())=={'scenario.sqlite','candidate.sql','witness.json','replay.py'}
        z.extractall(tmp_path/'independent')
    result=subprocess.run([sys.executable,'-I','replay.py'],cwd=tmp_path/'independent',
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert result.stdout.startswith('REPRODUCED UNINTENDED_DELETE')


def test_more_than_one_external_fingerprint_yields_real_standalone_repros(tmp_path):
    for name,keys,query,flag,expected in [
        ('nullable_key',['type','name'],'nullable_bug','NULL_MATCH_GAP',2),
        ('source_collision',['key'],'source_collision','MULTIPLE_SOURCE_MATCH',3),
    ]:
        sql=(CASES/(query+'.sql')).read_text('utf8')
        db,r=reduce_issue(CASES/(name+'.sqlite'),target='target',stage='stage',keys=keys,
                          delete_sql=sql,flag=flag)
        assert r['reduction']['retained_rows']==expected
        assert r['reduction']['independent_1_minimal'] is True
        target=tmp_path/(name+'.zip')
        try:emit_bundle(db,r,candidate_sql=sql,out=target,replay_script=ROOT/'scripts'/'replay_incremental.py')
        finally:db.close()
        folder=tmp_path/name
        with zipfile.ZipFile(target) as z:z.extractall(folder)
        result=subprocess.run([sys.executable,'-I','replay.py'],cwd=folder,
                              capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr


def test_tamper_standalone_witness_detected(tmp_path):
    sql=(CASES/'composite_bug.sql').read_text('utf8')
    db,r=reduce_issue(CASES/'composite_key.sqlite',target='target',stage='stage',
        keys=['num_column','str_column'],delete_sql=sql,flag='UNINTENDED_DELETE')
    try:emit_bundle(db,r,candidate_sql=sql,out=tmp_path/'s.zip',replay_script=ROOT/'scripts'/'replay_incremental.py')
    finally:db.close()
    with zipfile.ZipFile(tmp_path/'s.zip') as z:z.extractall(tmp_path/'dir')
    with open(tmp_path/'dir'/'candidate.sql','a') as f:f.write(' -- tamper')
    process=subprocess.run([sys.executable,'-I','replay.py'],cwd=tmp_path/'dir',
                            text=True,capture_output=True,timeout=20)
    assert process.returncode!=0 and 'integrity failed' in process.stderr


def test_budget_exhaustion_does_not_claim_one_minimal():
    sql=(CASES/'composite_bug.sql').read_text('utf8')
    db,r=reduce_issue(CASES/'composite_key.sqlite',target='target',stage='stage',
        keys=['num_column','str_column'],delete_sql=sql,flag='UNINTENDED_DELETE',max_calls=1)
    db.close()
    assert r['reduction']['budget_exhausted']
    assert not r['reduction']['algorithm_1_minimal']
    assert not r['reduction']['independent_1_minimal']


@pytest.mark.parametrize('seed', range(16))
def test_composite_key_metamorphic_cross_product_oracle(seed,tmp_path):
    """Different concrete keys always preserve the same relational error class."""
    a=seed*10+1;b=seed*10+2
    db=tmp_path/'metamorphic.sqlite'
    conn=sqlite3.connect(db)
    with conn:
        conn.executescript('CREATE TABLE target (a INTEGER,b TEXT); CREATE TABLE stage (a INTEGER,b TEXT);')
        conn.executemany('INSERT INTO target VALUES (?,?)',[(a,'x'),(b,'y'),(a,'y')])
        conn.executemany('INSERT INTO stage VALUES (?,?)',[(a,'x'),(b,'y')])
    conn.close()
    before=hashlib.sha256(db.read_bytes()).digest()
    sql='DELETE FROM target WHERE a IN (SELECT a FROM stage) AND b IN (SELECT b FROM stage)'
    wrong=inspect_database(db,target='target',stage='stage',keys=['a','b'],delete_sql=sql)
    assert wrong['counts']['unintended_deletes']==1
    assert wrong['examples']['unintended'][0]['key']==[a,'y']
    fixed=inspect_database(db,target='target',stage='stage',keys=['a','b'],
      delete_sql='DELETE FROM target WHERE (a,b) IN (SELECT a,b FROM stage)')
    assert fixed['status']=='PASS'
    assert hashlib.sha256(db.read_bytes()).digest()==before
