"""A candidate must run from its patched snapshot even if the host PYTHONPATH is poisoned."""
from __future__ import annotations
from pathlib import Path
import sys
from axiomforge.issue_engine.core import IssueContract, evaluate
from axiomforge.issue_engine.synthesis import to_patch


def test_patched_snapshot_takes_precedence_over_external_imports(tmp_path,monkeypatch):
    repo=tmp_path/'upstream'
    src=repo/'src'/'toy_project'
    src.mkdir(parents=True)
    (src/'__init__.py').write_text('')
    file=src/'policy.py'
    file.write_text('def check():\n    return False\n')
    # This absolute path deliberately points at the *unpatched* source tree.
    monkeypatch.setenv('PYTHONPATH',str(repo/'src'))
    code='from toy_project.policy import check; assert check(), "CHECKOUT-SHADOWED"'
    control='from toy_project.policy import check; assert callable(check)'
    c=IssueContract('https://github.com/test/toy/issues/1',repo,
                    (sys.executable,'-c',code),(sys.executable,'-c',control),
                    frozenset(['src/toy_project/policy.py']),'CHECKOUT-SHADOWED',
                    timeout_seconds=4,repetitions=1,isolated_checks=True)
    patch=tmp_path/'fix.patch'
    patch.write_text(to_patch('src/toy_project/policy.py',file.read_text(),
                              'def check():\n    return True\n'))
    receipt=evaluate(c,patch)
    assert receipt['passed'],receipt
    for part in receipt['patched']['reproducer']:
        assert part['returncode']==0
