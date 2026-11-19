"""Mutation pressure must be measured from actual normal controls, not guessed."""
import json
from pathlib import Path
import pytest
from axiomforge.issue_engine.core import IssueError, load_contract
from axiomforge.issue_engine.oracle_audit import audit_oracle


def _case(tmp_path, *, weak=False):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'calculation.py').write_text('''def compute(x):
    if x > 0:
        return x - 1
    return x
''')
    (repo/'failure.py').write_text('''from calculation import compute
assert compute(4) == 5, "BUG-AGREEMENT-4321"
''')
    (repo/'pass.py').write_text(('from calculation import compute\nassert True\n'
                                  if weak else
                                  'from calculation import compute\nassert compute(0)==0\nassert compute(-2)==-2\n'))
    payload={'schema':'axiomforge.issue-contract.v1',
             'issue_url':'https://github.com/sample/real/issues/4321',
             'checkout':'repo','reproduce':['${PYTHON}','failure.py'],
             'control':['${PYTHON}','pass.py'],'editable_files':['calculation.py'],
             'failure_fingerprint':'BUG-AGREEMENT-4321','repetitions':1,
             'timeout_seconds':4,'isolated_checks':True}
    p=tmp_path/'contract.json';p.write_text(json.dumps(payload))
    return repo,load_contract(p)


def test_mutation_audit_detects_sensitive_controls_and_preserves_source(tmp_path):
    repo,c=_case(tmp_path)
    source=(repo/'calculation.py').read_bytes()
    result=audit_oracle(c,file='calculation.py',max_mutants=8,max_seconds=30)
    assert result['status']=='AUDITED'
    assert result['assessed_mutations']>=1
    assert result['killed_by_normal_controls']>=1
    assert result['source_sha256']
    assert (repo/'calculation.py').read_bytes()==source
    assert all('patch_sha256' in x for x in result['mutations'])


def test_oracle_audit_identifies_blind_controls(tmp_path):
    _,c=_case(tmp_path,weak=True)
    result=audit_oracle(c,file='calculation.py',max_mutants=8,max_seconds=25)
    assert result['status']=='AUDITED'
    assert result['killed_by_normal_controls']==0
    assert result['control_kill_fraction']==0.0
    assert result['survived_normal_controls']>0


def test_mutation_pressure_is_deterministic_given_seed(tmp_path):
    _,c=_case(tmp_path)
    r1=audit_oracle(c,file='calculation.py',max_mutants=2,max_seconds=25,seed=42)
    r2=audit_oracle(c,file='calculation.py',max_mutants=2,max_seconds=25,seed=42)
    assert [(x['line'],x['kind'],x.get('patch_sha256')) for x in r1['mutations']] == [
           (x['line'],x['kind'],x.get('patch_sha256')) for x in r2['mutations']]


def test_oracle_rejects_unapproved_file(tmp_path):
    _,c=_case(tmp_path)
    with pytest.raises(IssueError,match='allowlisted'):
        audit_oracle(c,file='failure.py')
