"""Regression: a short wall-clock budget must constrain candidate subprocesses."""
from pathlib import Path
import json
import time
from axiomforge.issue_engine.core import load_contract
from axiomforge.issue_engine.synthesis import synthesize


def test_search_budget_limits_slow_candidate_execution(tmp_path: Path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'app.py').write_text('def value():\n    return False\n')
    (repo/'repro.py').write_text('from app import value\nassert value(), "ISSUE-SLOW-BUDGET"\n')
    (repo/'control.py').write_text('import time\ntime.sleep(2)\n')
    path=tmp_path/'c.json'
    path.write_text(json.dumps({'schema':'axiomforge.issue-contract.v1',
        'issue_url':'https://github.com/example/repo/issues/9001',
        'checkout':'repo','reproduce':['${PYTHON}','repro.py'],
        'control':['${PYTHON}','control.py'],
        'editable_files':['app.py'],'failure_fingerprint':'ISSUE-SLOW-BUDGET',
        'timeout_seconds':4,'repetitions':1}))
    started=time.monotonic()
    result=synthesize(load_contract(path),'app.py',max_candidates=1,max_seconds=0.8)
    assert result['status']=='NOT_SOLVED'
    assert time.monotonic()-started < 2.5
