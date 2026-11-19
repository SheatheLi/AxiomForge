"""Contract creation is usable without hand-writing a JSON file."""
import json
from pathlib import Path
from axiomforge.issue_engine.cli import main
from axiomforge.issue_engine.core import load_contract


def test_init_creates_valid_isolated_real_repo_contract(tmp_path):
    repo=tmp_path/'repo'
    repo.mkdir()
    for file in ('fail.py','control.py','module.py'):
        (repo/file).write_text('pass\n')
    dest=tmp_path/'contract.json'
    args=['init','--issue-url','https://github.com/example/project/issues/235',
          '--checkout',str(repo),'--repro','fail.py','--control','control.py',
          '--editable','module.py','--failure-fingerprint','BUG-235-EXPECTED',
          '--out',str(dest)]
    assert main(args)==0
    doc=json.loads(dest.read_text())
    assert doc['isolated_checks'] is True
    assert load_contract(dest).editable_files==frozenset(['module.py'])


def test_init_rejects_missing_repro_script(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'control.py').write_text('pass\n')
    (repo/'module.py').write_text('pass\n')
    path=tmp_path/'contract.json'
    assert main(['init','--issue-url','https://github.com/example/project/issues/235',
          '--checkout',str(repo),'--repro','fail.py','--control','control.py',
          '--editable','module.py','--failure-fingerprint','BUG-235-EXPECTED',
          '--out',str(path)])==1
    assert not path.exists()
