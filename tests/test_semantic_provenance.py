"""Independent precondition semantics, third-party replay, attack cases."""
from __future__ import annotations
import copy,hashlib,json
import pytest
from axiomforge.semantics import compile_pandas_pair,CompileError,build_certificate,inspect_case,residual
from axiomforge.semantics.algebra import DomainError,counterfactual_repair
from axiomforge.semantics.backends import BACKENDS
from axiomforge.semantics.experiment import PROGRAMS,run_experiment,save,verify,evaluate,_canonical,_trial_rows

@pytest.mark.parametrize('key',list(PROGRAMS))
def test_all_programs_static_compile_and_algebraic_proof(key):
    p=compile_pandas_pair(*PROGRAMS[key]);proof=build_certificate(p)
    assert proof['status']=='ALGEBRAIC_IR_CERTIFIED_NOT_SUT_PROVED'
    assert proof['ir_fingerprint']==p.fingerprint()
    assert all(v is True for v in proof['proof_obligations'].values() if isinstance(v,bool))

@pytest.mark.parametrize('exprs',[
 ('df["amount"].sum()', '__import__("os").system("echo HACK")'),
 ('df["amount"].sum()', 'df.groupby("group",dropna=True)["amount"].mean().sum()'),
 ('df["amount"].sum()', 'df.groupby("group",dropna=(lambda:True)())["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby("group",sort=False)["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby("group",dropna=True)["other"].sum().sum()'),
 ('df["amount"].sum()', 'other.groupby("group",dropna=True)["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby(["group","group"],dropna=True)["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby("amount",dropna=True)["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby("group")["amount"].sum().sum()'),
 ('df["amount"].sum()', 'df.groupby("group",dropna=True)["amount"].sum(min_count=1).sum()'),
])
def test_unsupported_expressions_fail_closed(exprs):
    with pytest.raises(CompileError): compile_pandas_pair(*exprs)

@pytest.mark.parametrize('wrong',[[],[{'group':None,'amount':1.5}],[{'group':'A','amount':True}],
                                   [{'group':'A','amount':1,'extra':1}],[{'group':float('nan'),'amount':1}],
                                   [{'group':'A','amount':10**13}]])
def test_unsupported_input_domain_abstains(wrong):
    p=compile_pandas_pair(*PROGRAMS['sum_single'])
    with pytest.raises(DomainError):inspect_case(p,wrong)

def test_automatic_missing_mass_feature_no_engineer_feature_name():
    p=compile_pandas_pair(*PROGRAMS['sum_single'])
    xs=[{'group':None,'amount':3},{'group':None,'amount':-3},{'group':'A','amount':6}]
    ev=inspect_case(p,xs)
    assert ev['guard'] is True and ev['fallback_guard'] is False and ev['residual']==0
    assert [t['coefficient'] for t in ev['explanation']]==[1,1,0]
    for run in BACKENDS.values():assert run(p,xs)==(6,6)

def test_multi_key_and_count_nonzero_residual():
    xs=[{'group':None,'region':'A','amount':3},
        {'group':'A','region':'A','amount':6},
        {'group':'B','region':None,'amount':-3}]
    p=compile_pandas_pair(*PROGRAMS['sum_multi'])
    assert inspect_case(p,xs)['guard'] and not inspect_case(p,xs)['fallback_guard']
    for run in BACKENDS.values():assert run(p,xs)==(6,6)
    p=compile_pandas_pair(*PROGRAMS['count_multi'])
    ev=inspect_case(p,xs)
    assert ev['residual']==2 and not ev['guard']
    for run in BACKENDS.values():assert run(p,xs)==(3,1)

def test_counterfactual_produces_exact_single_row_repair():
    p=compile_pandas_pair(*PROGRAMS['sum_single'])
    inp=[{'group':None,'amount':7},{'group':'A','amount':2}]
    out=counterfactual_repair(p,inp)
    assert out['status']=='ONE_ROW_MINIMUM_ABSOLUTE_DELTA'
    assert out['old_value']==7 and out['new_value']==0 and out['absolute_delta']==7
    assert inp[0]['amount']==7  # never edits the original
    assert counterfactual_repair(compile_pandas_pair(*PROGRAMS['count_multi']),[{'group':None,'region':'B','amount':2}])['status']=='ABSTAIN_NON_SUM'

def test_all_backends_holdout_unseen_row_lengths():
    for label,src in PROGRAMS.items():
        p=compile_pandas_pair(*src)
        for seed in range(12):
            rows=_trial_rows(p,41017+seed)
            assert len(rows)>=4
            for name in BACKENDS:
                obs=evaluate(p,rows,name)
                assert obs['status'] in ('ADMITTED_CONSISTENT','ABSTAIN_CONDITION_NOT_MET')
                assert obs['actual']==obs['independent_reference']

def test_cross_backend_experiment_and_independent_replay(tmp_path):
    data=run_experiment(seeds=8)
    out=tmp_path/'evidence.json'; save(out,data)
    result=verify(out,rerun=True)
    assert result['verified_trials']==8*3*(len(PROGRAMS)+2)
    assert result['status']=='THIRD_PARTY_REEXECUTED'
    entries={r['case']:r for r in data['results']}
    assert entries['sum_single']['summary']['additional_safe_admissions']>0
    assert entries['sum_multi']['summary']['additional_safe_admissions']>0
    for v in entries.values():
        assert v['summary']['incorrect_synthesized_admissions']==0
        assert v['summary']['independent_model_disagreements']==0

def test_recomputed_hash_does_not_exonerate_fabricated_result(tmp_path):
    data=run_experiment(seeds=2)
    out=tmp_path/'evidence.json';save(out,data)
    obj=json.loads(out.read_text())
    obj['payload']['results'][0]['trials'][0]['actual'][0]=999
    # Fake forged totals are not enough; even recomputing SHA is rejected by independent replay.
    obj['payload_sha256']=hashlib.sha256(_canonical(obj['payload'])).hexdigest()
    out.write_text(json.dumps(obj))
    with pytest.raises(ValueError): verify(out,rerun=True)

def test_incomplete_record_rejected_even_with_correct_digest(tmp_path):
    data=run_experiment(seeds=2)
    data['results'][0]['trials'].pop()
    out=tmp_path/'missing.json';save(out,data)
    with pytest.raises(ValueError):verify(out,rerun=True)

def test_changed_semantic_source_and_environment_rejected(tmp_path):
    data=run_experiment(seeds=1)
    data['results'][0]['pair']['left_source']='fake value'
    path=tmp_path/'source.json';save(path,data)
    with pytest.raises(ValueError):verify(path,rerun=True)
    data=run_experiment(seeds=1)
    data['environment']['pandas']='unknown'
    save(path,data)
    with pytest.raises(ValueError):verify(path,rerun=True)
