"""Validate that packaging cannot upgrade weak evidence into a PR claim."""
import importlib.util
import json
from pathlib import Path
import textwrap

ROOT=Path(__file__).resolve().parents[2]


def load():
    spec=importlib.util.spec_from_file_location('axiomforge_review_export',ROOT/'scripts'/'export_review_bundle.py')
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def fixture(tmp_path):
    repo=tmp_path/'upstream';repo.mkdir()
    (repo/'api.py').write_text('def f(v):\n    return v - 1\n')
    (repo/'repro.py').write_text('from api import f\nassert f(3)==4, "ISSUE-8831-WRONG-RESULT"\n')
    (repo/'control.py').write_text('from api import f\nassert f(2)==1\n')
    # Need independent controls that remain true across the patch.
    (repo/'control.py').write_text('from api import f\nassert isinstance(f(2),int)\n')
    (repo/'repro2.py').write_text('from api import f\nassert f(8)==9, "ISSUE-8831-WRONG-RESULT"\n')
    patch=tmp_path/'fix.patch'
    patch.write_text(textwrap.dedent('''\
        diff --git a/api.py b/api.py
        --- a/api.py
        +++ b/api.py
        @@ -1,2 +1,2 @@
         def f(v):
        -    return v - 1
        +    return v + 1
    '''))
    contract=tmp_path/'contract.json'
    doc={
      'schema':'axiomforge.issue-contract.v1','issue_url':'https://github.com/example/repo/issues/8831',
      'checkout':'upstream','reproduce':['${PYTHON}','repro.py'],'control':['${PYTHON}','control.py'],
      'editable_files':['api.py'],'failure_fingerprint':'ISSUE-8831-WRONG-RESULT',
      'timeout_seconds':10,'repetitions':2,'isolated_checks':True,
      'independent_checks':[['${PYTHON}','control.py']],
      'independent_reproductions':[['${PYTHON}','repro2.py']]}
    contract.write_text(json.dumps(doc))
    return contract,patch


def test_export_requires_independent_evidence(tmp_path):
    contract,patch=fixture(tmp_path)
    doc=json.loads(contract.read_text());doc['independent_checks']=[]
    contract.write_text(json.dumps(doc))
    result=load().build_bundle(contract,patch,tmp_path/'export',title='test',summary='test')
    assert result['status']=='BLOCKED_EVIDENCE'
    assert not (tmp_path/'export').exists()


def test_export_requires_two_repetitions_and_snapshots(tmp_path):
    contract,patch=fixture(tmp_path)
    doc=json.loads(contract.read_text());doc['repetitions']=1;doc['isolated_checks']=False
    contract.write_text(json.dumps(doc))
    result=load().build_bundle(contract,patch,tmp_path/'export',title='test',summary='test')
    assert result['status']=='BLOCKED_EVIDENCE'
    assert 'isolated_checks must be true' in result['reasons']


def test_export_creates_review_bundle_but_does_not_claim_merged_pr(tmp_path):
    contract,patch=fixture(tmp_path)
    result=load().build_bundle(contract,patch,tmp_path/'export',title='Fix integer transform',
                     summary='The caller receives an incorrect result.')
    assert result['status']=='LOCAL_REVIEW_PACKAGE',result
    assert (tmp_path/'export'/'candidate.patch').read_bytes()==patch.read_bytes()
    assert 'not an upstream endorsement' in (tmp_path/'export'/'PR_DRAFT.md').read_text().lower()
    assert result['ablation_passed']
