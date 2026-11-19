"""Counterexample-guided two-edit repair without canned patches or known issues."""
from __future__ import annotations
import json
from pathlib import Path
from axiomforge.issue_engine.core import load_contract
from axiomforge.issue_engine.synthesis import synthesize, compose_nonoverlapping


def make_two_faults(tmp_path: Path) -> tuple[Path, Path]:
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'computations.py').write_text('''def alpha(x):
    if x < 0:
        return 0
    return x - 1


def beta(x):
    if x < 0:
        return 0
    return x - 1
''')
    (repo/'primary.py').write_text('from computations import alpha\nassert alpha(4)==5, "MULTI-SITE-ISSUE-7001"\n')
    (repo/'second.py').write_text('from computations import beta\nassert beta(8)==9, "MULTI-SITE-ISSUE-7001"\n')
    (repo/'control.py').write_text('from computations import alpha,beta\nassert alpha(-10)==0 and beta(-5)==0\n')
    c={'schema':'axiomforge.issue-contract.v1',
       'issue_url':'https://github.com/test/fixture/issues/7001',
       'checkout':'repo','reproduce':['${PYTHON}','primary.py'],
       'control':['${PYTHON}','control.py'],
       'independent_reproductions':[['${PYTHON}','second.py']],
       'independent_checks':[['${PYTHON}','control.py']],
       'editable_files':['computations.py'],
       'failure_fingerprint':'MULTI-SITE-ISSUE-7001',
       'timeout_seconds':7,'repetitions':1}
    path=tmp_path/'contract.json';path.write_text(json.dumps(c))
    return repo,path


def test_compose_rejects_overlapping_source_edits():
    before='x = 1\ny = 2\n'
    assert compose_nonoverlapping(before,'x = 4\ny = 2\n','x = 9\ny = 2\n') is None
    combined=compose_nonoverlapping(before,'x = 4\ny = 2\n','x = 1\ny = 8\n')
    assert combined=='x = 4\ny = 8\n'


def test_two_independent_code_changes_required_for_dual_failure(tmp_path):
    repo,path=make_two_faults(tmp_path)
    original=(repo/'computations.py').read_bytes()
    result=synthesize(load_contract(path),'computations.py',max_edits=2,
                      max_candidates=14,max_seconds=65)
    assert result['status']=='VERIFIED_CANDIDATE',result
    assert result['edits_used']==2
    assert result['operator']=='composed-two-site'
    assert result['receipt']['independent_reproduction_count']==1
    assert result['receipt']['evidence_tier']=='INDEPENDENT_FAILURES_AND_CONTROLS'
    assert result['patch'].count('+    return x + 1')==2
    assert (repo/'computations.py').read_bytes()==original


def test_single_edit_mode_does_not_claim_full_issue_solution(tmp_path):
    repo,path=make_two_faults(tmp_path)
    result=synthesize(load_contract(path),'computations.py',max_edits=1,
                      max_candidates=8,max_seconds=32)
    assert result['status']=='NOT_SOLVED'
    assert result['attempts']>0
