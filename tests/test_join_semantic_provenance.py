"""Join multiplicity, typed parser, null non-equivalence, external evidence."""
from __future__ import annotations
import json
import pytest
from axiomforge.semantics.compiler import CompileError
from axiomforge.semantics.algebra import DomainError
from axiomforge.semantics.join_provenance import JOIN_PROGRAMS,compile_join,prove_join,inspect_join,execute_join
from axiomforge.semantics.experiment import run_experiment,save,verify

@pytest.mark.parametrize('name',list(JOIN_PROGRAMS))
def test_compiler_and_sympy_join_proof(name):
    pair=compile_join(*JOIN_PROGRAMS[name]);cert=prove_join(pair)
    assert cert['status']=='ALGEBRAIC_JOIN_IR_CERTIFIED_NOT_SUT_PROVED'
    assert cert['fingerprint']==pair.fingerprint()

@pytest.mark.parametrize('wrong',[
 ('df["amount"].sum()','df.merge(dim,on="group",how="inner")["amount"].sum()'),
 ('df["amount"].sum()','df.merge(dim,on="group",how="left",validate="many_to_one")["amount"].sum()'),
 ('df["amount"].sum()','df.merge(dim,on=key,how="left")["amount"].sum()'),
 ('df["amount"].sum()','df.merge(dim,on="group",how="left")["different"].sum()'),
 ('df["amount"].sum()','df.merge(dim,on="group",how="left")["amount"].mean()'),
 ('df["amount"].sum()','other.merge(dim,on="group",how="left")["amount"].sum()'),
 ('df["amount"].sum()','df.merge(df,on="group",how="left")["amount"].sum()'),
])
def test_join_ast_nonwhitelist_rejected(wrong):
    with pytest.raises(CompileError):compile_join(*wrong)

def test_cardinality_guard_detects_fanout_and_avoids_unnecessary_abstention():
    p=compile_join(*JOIN_PROGRAMS['sum_fanout'])
    dim=[{'group':'A','label':'x'},{'group':'A','label':'y'}, {'group':'B','label':'z'}]
    fact=[{'group':'A','amount':10},{'group':'A','amount':-10}, {'group':'B','amount':4}]
    proof=inspect_join(p,fact,dim)
    assert proof['guard'] and not proof['simple_unique_key_guard']
    assert [x['right_multiplicity'] for x in proof['trace']]==[2,2,1]
    for name in ('pandas','sqlite','sqlalchemy'):
        assert execute_join(p,fact,dim,name)==(4,4)
    fact[1]['amount']=-9
    proof=inspect_join(p,fact,dim)
    assert not proof['guard'] and proof['residual']==-1
    for name in ('pandas','sqlite','sqlalchemy'):
        assert execute_join(p,fact,dim,name)==(5,6)

def test_count_join_cannot_cancel_extra_rows():
    p=compile_join(*JOIN_PROGRAMS['count_fanout'])
    facts=[{'group':'A','amount':0},{'group':'A','amount':0}]
    dims=[{'group':'A','label':'a'},{'group':'A','label':'b'}]
    assert inspect_join(p,facts,dims)['residual']==-2
    for backend in ('pandas','sqlite','sqlalchemy'):
        assert execute_join(p,facts,dims,backend)==(2,4)

def test_dimension_duplicates_without_matching_facts_are_not_bugs():
    p=compile_join(*JOIN_PROGRAMS['count_fanout'])
    facts=[{'group':'B','amount':1}]
    dims=[{'group':'A','label':'x'},{'group':'A','label':'y'}]
    r=inspect_join(p,facts,dims)
    assert r['guard'] and not r['simple_unique_key_guard']
    for backend in ('pandas','sqlite','sqlalchemy'):
        assert execute_join(p,facts,dims,backend)==(1,1)

def test_sql_and_pandas_null_key_divergence_is_explicitly_outside_contract():
    p=compile_join(*JOIN_PROGRAMS['sum_fanout'])
    facts=[{'group':None,'amount':5}]
    dims=[{'group':None,'label':'x'},{'group':None,'label':'y'}]
    with pytest.raises(DomainError):inspect_join(p,facts,dims)
    with pytest.raises(DomainError):execute_join(p,facts,dims,'pandas')
    # Reproduction of the documented semantic mismatch, not part of the certified domain.
    import pandas as pd,sqlite3
    observed=pd.DataFrame(facts).merge(pd.DataFrame(dims),on='group',how='left')['amount'].sum()
    with sqlite3.connect(':memory:') as c:
        c.execute('CREATE TABLE f(k TEXT,amount INT)');c.execute('CREATE TABLE d(k TEXT,label TEXT)')
        c.execute('INSERT INTO f VALUES(NULL,5)')
        c.executemany('INSERT INTO d VALUES(NULL,?)',[('x',),('y',)])
        sql=c.execute('SELECT SUM(f.amount) FROM f LEFT JOIN d ON f.k=d.k').fetchone()[0]
    assert int(observed)==10 and sql==5

def test_joint_experiment_replay_includes_two_join_families(tmp_path):
    payload=run_experiment(seeds=8)
    assert len(payload['results'])==6
    entries={x['case']:x for x in payload['results']}
    assert entries['sum_fanout']['summary']['additional_safe_admissions']>0
    assert entries['count_fanout']['summary']['additional_safe_admissions']>0
    p=tmp_path/'both.json';save(p,payload)
    assert verify(p,rerun=True)['verified_trials']==8*3*6
    # After SHA repair, source provenance, not just byte integrity, still matters.
    record=json.loads(p.read_text())
    record['payload']['results'][-1]['trials'][0]['dimension_rows'][0]['label']='tampered'
    import hashlib
    from axiomforge.semantics.experiment import _canonical
    record['payload_sha256']=hashlib.sha256(_canonical(record['payload'])).hexdigest()
    p.write_text(json.dumps(record))
    with pytest.raises(ValueError):verify(p,rerun=True)
