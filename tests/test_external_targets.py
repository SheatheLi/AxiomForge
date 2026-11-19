from __future__ import annotations
from axiomforge.targets.pandas_cases import input_for,execute,CONTRACTS
from axiomforge.runner import run_one,hunt
from axiomforge.minimize import ddmin
from axiomforge.model import Status
from axiomforge.suite import controls

def test_controls():
    assert controls()["all_expected"]

def test_schema_invalid_inputs():
    r=run_one('cow-alias',{'keys':[1], 'values':[5], 'mutated_key':100})
    assert r.status==Status.EXECUTION_ERROR

def test_semantic_change_not_false_alarm():
    w=run_one('cow-alias',input_for('cow-alias',0), control={'semantic_change':True})
    assert w.status==Status.ABSTAIN

def test_sort_true_abstains_even_if_relation_fails():
    w=run_one('group-sort',{'keys':[2,1],'values':[1,1],'sort':False},control={'sort':True})
    assert w.status==Status.ABSTAIN

def test_both_real_pandas_upstream_issues_are_reproduced():
    a=hunt('group-sort',24)
    b=hunt('cow-alias',24)
    assert a.witnesses and b.witnesses
    assert all(w.status==Status.VERIFIED_RELATIONAL_VIOLATION for w in a.witnesses+b.witnesses)
    assert len({w.known_issue for w in a.witnesses+b.witnesses})==2

def test_pandas_cow_false_is_consistent():
    w=run_one('cow-alias',input_for('cow-alias',0),control={'copy_on_write':False})
    assert w.status==Status.CONSISTENT

def test_minimizes_real_bug_not_report_only():
    for name in ['group-sort','cow-alias']:
        x=hunt(name,24,stop_first=True)
        assert x.witnesses
        m=ddmin(name,x.witnesses[0].input)
        assert m.preserved
        assert m.after_rows<=m.before_rows
        assert run_one(name,m.minimized).status==Status.VERIFIED_RELATIONAL_VIOLATION

def test_subprocess_isolation_actual_pandas():
    w=run_one('cow-alias',input_for('cow-alias',0),isolated=True)
    assert w.status==Status.VERIFIED_RELATIONAL_VIOLATION

def test_sqlalchemy_actual_instrumented_failure_path():
    w=run_one('sa-lifecycle',{'stage':'connect','attempts':3})
    assert w.status==Status.VERIFIED_RELATIONAL_VIOLATION
    assert w.left['created']==3 and w.right['closed']==0
    assert w.state['errors']==3

def test_sqlalchemy_normal_control_does_not_claim_false_failure():
    w=run_one('sa-lifecycle',{'stage':'connect','attempts':3},control={'stage':'none'})
    assert w.status==Status.ABSTAIN
    assert w.relation_holds is True

def test_sqlalchemy_first_connect_event_and_subprocess():
    w=run_one('sa-lifecycle',{'stage':'first_connect','attempts':2},isolated=True)
    assert w.status==Status.VERIFIED_RELATIONAL_VIOLATION
    assert w.left=={'created':2}
