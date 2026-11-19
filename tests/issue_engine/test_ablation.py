"""Independent contract experiments for counterfactual patch necessity."""
from pathlib import Path
import tempfile
import difflib

from axiomforge.issue_engine.core import IssueContract
from axiomforge.issue_engine.ablation import audit_hunk_necessity


def test_ablation_detects_unrelated_hunk():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = root / 'repo'
        repo.mkdir()
        source = 'def broken():\n    return 0\n' + '\n' * 9 + 'def unrelated():\n    return 1\n'
        fixed = 'def broken():\n    return 1\n' + '\n' * 9 + 'def unrelated():\n    return 2\n'
        (repo / 'logic.py').write_text(source)
        (repo / 'fail.py').write_text("from logic import broken\nassert broken()==1, 'BUG-GUARD-123'\n")
        (repo / 'pass.py').write_text('from logic import unrelated\nassert unrelated()>0\n')
        diff = ''.join(difflib.unified_diff(source.splitlines(True),fixed.splitlines(True),fromfile='a/logic.py',tofile='b/logic.py'))
        patch = root / 'repair.patch'
        patch.write_text('diff --git a/logic.py b/logic.py\n' + diff)
        contract = IssueContract.parse({
            'schema':'axiomforge.issue-contract.v1',
            'issue_url':'https://github.com/example/project/issues/123',
            'checkout':str(repo),'reproduce':['${PYTHON}','fail.py'],
            'control':['${PYTHON}','pass.py'], 'editable_files':['logic.py'],
            'failure_fingerprint':'BUG-GUARD-123', 'repetitions':1,
            'timeout_seconds':5,'isolated_checks':True,
        },base=root)
        result = audit_hunk_necessity(contract,patch,max_seconds=25)
        assert result['status'] == 'AUDITED'
        assert result['hunk_count'] == 2
        assert {x['result'] for x in result['hunks']} == {
            'REDUNDANT_UNDER_CONTRACT', 'REQUIRED_UNDER_CONTRACT'
        }
        assert result['evidence_relative_necessary'] is False


def test_ablation_single_necessary_hunk():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp); repo=root/'repo'; repo.mkdir()
        (repo/'logic.py').write_text('value = 0\n')
        (repo/'fail.py').write_text("from logic import value\nassert value == 1, 'EXPECTED-BUG-1'\n")
        (repo/'pass.py').write_text('from logic import value\nassert value >= 0\n')
        patch=root/'repair.patch'
        patch.write_text('diff --git a/logic.py b/logic.py\n--- a/logic.py\n+++ b/logic.py\n@@ -1 +1 @@\n-value = 0\n+value = 1\n')
        c=IssueContract.parse({'schema':'axiomforge.issue-contract.v1',
            'issue_url':'https://github.com/test/repo/issues/123','checkout':str(repo),
            'reproduce':['${PYTHON}','fail.py'],'control':['${PYTHON}','pass.py'],
            'editable_files':['logic.py'],'failure_fingerprint':'EXPECTED-BUG-1',
            'repetitions':1,'timeout_seconds':5},base=root)
        res=audit_hunk_necessity(c,patch,max_seconds=20)
        assert res['evidence_relative_necessary'] is True
        assert res['hunks'][0]['result']=='SOLE_HUNK'
