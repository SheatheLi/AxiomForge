"""Reproduce a real HTTPcore SOCKS5 timeout fault with a neutral local test driver.

The third-party checkout is user supplied, never redistributed, and never
modified. All candidate synthesis runs against temporary copies. A successful
local candidate is NOT a novel PR: #1089 has independent upstream PRs.
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from axiomforge.issue_engine.core import load_contract
from axiomforge.issue_engine.synthesis import synthesize


def commands(authenticated: bool, enforce: bool) -> list[str]:
    payload = ("import os,runpy;"
               "os.environ.update({'AXIOMFORGE_ASSERT_TIMEOUT':'%s',"
               "'AXIOMFORGE_TEST_VARIANT':'%s'});"
               "runpy.run_path('deadline_probe.py',run_name='__main__')") % (
                   '1' if enforce else '0', 'auth' if authenticated else 'noauth')
    return ['${PYTHON}', '-c', payload]


def run(checkout: Path, out: Path, *, budget: float = 90.0) -> dict:
    checkout = checkout.resolve()
    file = checkout/'httpcore/_sync/socks_proxy.py'
    if not file.is_file():
        raise ValueError(f'expected an HTTPcore source checkout with {file}')
    if not budget > 0:
        raise ValueError('invalid search budget')
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='axiomforge-httpcore-holdout-') as temp:
        temp_path = Path(temp)
        workspace = temp_path/'checkout'
        shutil.copytree(checkout, workspace, ignore=shutil.ignore_patterns(
            '.git','__pycache__','.pytest_cache','.venv','build','dist'))
        shutil.copy2(ROOT/'examples/holdout/httpcore_deadline_probe.py',
                     workspace/'deadline_probe.py')
        contract = {
            'schema':'axiomforge.issue-contract.v1',
            'issue_url':'https://github.com/encode/httpcore/issues/1089',
            'checkout':str(workspace),
            'reproduce':commands(False, True),
            'control':commands(False, False),
            'independent_reproductions':[commands(True, True)],
            'independent_checks':[commands(True, False)],
            'failure_fingerprint':'SOCKS-DEADLINE-LOST',
            'editable_files':['httpcore/_sync/socks_proxy.py'],
            'timeout_seconds':12,
            'repetitions':2,
            'isolated_checks':True,
        }
        spec = temp_path/'contract.json'
        spec.write_text(json.dumps(contract), encoding='utf8')
        outcome = synthesize(load_contract(spec), 'httpcore/_sync/socks_proxy.py',
                             max_candidates=20,max_seconds=budget)
        if outcome['status'] == 'VERIFIED_CANDIDATE':
            (out/'candidate.patch').write_text(outcome['patch'],encoding='utf8')
        receipt = {k:v for k,v in outcome.items() if k!='patch'}
        # Avoid making a soon-to-be-deleted temp checkout look reusable.
        if 'receipt' in receipt:
            receipt['receipt']['repo'] = '<ephemeral-authorized-checkout>'
        (out/'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
        return receipt


def main() -> int:
    p=argparse.ArgumentParser(description='Run independently checkable HTTPcore SOCKS deadline holdout')
    p.add_argument('--httpcore',type=Path,required=True)
    p.add_argument('--out',type=Path,default=ROOT/'results/httpcore_deadline_study')
    p.add_argument('--max-seconds',type=float,default=90)
    p.add_argument('--allow-exec',action='store_true')
    args=p.parse_args()
    if not args.allow_exec:
        p.error('executes third-party code: verify trust and pass --allow-exec')
    result=run(args.httpcore,args.out,budget=args.max_seconds)
    print(f"{result['status']}: {result.get('operator','none')}, attempts={result.get('attempts',0)}")
    return 0 if result['status']=='VERIFIED_CANDIDATE' else 2

if __name__=='__main__':
    raise SystemExit(main())
