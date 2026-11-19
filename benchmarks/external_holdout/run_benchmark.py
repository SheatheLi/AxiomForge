"""Preregistered external-issue benchmark. Never modifies the repair operators.

Runs external source code. Inspect and trust source, then pass --allow-exec.
This tests a *frozen known operator catalog* on unfamiliar issue mechanisms.
It does not simulate maintainer acceptance or replace an APR benchmark.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))
from axiomforge.issue_engine.core import IssueContract, _run, _broken, _passes, IssueError
from axiomforge.issue_engine.synthesis import synthesize

BASE = Path(__file__).resolve().parent
PATCHED_GENERATORS = (
    ('state_repair','borrowed_lookup_candidates'),
    ('context_repair','timeout_flow_candidates'),
    ('lifecycle_repair','initialization_cleanup_candidates'),
    ('async_cleanup','two_phase_close_candidates'),
    ('safety_repair','comment_preservation_candidates'),
    ('layout_repair','bounded_rejoin_candidates'),
    ('query_scope_repair','dml_scope_candidates'),
)

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def check_freeze(manifest: dict) -> None:
    for filename, expected in manifest['frozen_operator_sha256'].items():
        path=ROOT/filename
        if not path.is_file() or sha(path)!=expected:
            raise RuntimeError(f'FROZEN SOURCE MISMATCH: {filename}')

def check_historical_archive(manifest: dict) -> None:
    for filename, expected in manifest["frozen_operator_sha256"].items():
        path = BASE / "v08_frozen_source" / filename
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError(f"HISTORICAL SOURCE CHANGED: {filename}")


def no_structural_generators():
    stack=ExitStack()
    # Only suppress high-level repair operators. Generic AST baseline is kept.
    def empty(*args, **kwargs):
        return iter(())
    for module, name in PATCHED_GENERATORS:
        stack.enter_context(patch(f'axiomforge.issue_engine.{module}.{name}',new=empty))
    return stack

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sqlfluff',type=Path,required=True)
    p.add_argument('--sqlalchemy',type=Path,required=True)
    p.add_argument('--httpcore',type=Path,required=True)
    p.add_argument('--sqlfluff-deps',type=Path,help='optional locally installed SQLFluff runtime dependency directory')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--allow-exec',action='store_true',required=True)
    p.add_argument('--models',choices=('full','generic','both'),default='both')
    p.add_argument('--retrospective',action='store_true',help='run upgraded engine without claiming the original operator freeze')
    a=p.parse_args()
    cases=json.loads((BASE/'case_scripts.json').read_text('utf8'))
    registered=json.loads((BASE/'preregistered_cases.json').read_text('utf8'))
    check_historical_archive(registered)
    if not a.retrospective:
        check_freeze(registered)
    if a.sqlfluff_deps:
        deps=str(a.sqlfluff_deps.resolve())
        os.environ['PYTHONPATH']=deps+os.pathsep+os.environ.get('PYTHONPATH','')
    manifest_sha=sha(BASE/'preregistered_cases.json')
    repos={x:str(getattr(a,x).resolve()) for x in ('sqlfluff','sqlalchemy','httpcore')}
    report={'schema':'axiomforge.external-holdout-result.v1','manifest_sha256':manifest_sha,
        'frozen_operator_sources_confirmed':not a.retrospective,
        'evaluation_class': 'POST_TUNING_DIAGNOSTIC' if a.retrospective else 'ORIGINAL_FROZEN_EVALUATION','python':platform.python_version(),
        'environment':'local offline source snapshots; upstream dependencies provided externally',
        'current_synthesis_sha256':sha(ROOT/'src/axiomforge/issue_engine/synthesis.py'),
        'budgets':registered['budgets'],'models':a.models,'results':[]}
    for entry, reg in zip(cases,registered['cases']):
        if entry['id']!=reg['id']:raise RuntimeError('case manifest mismatch')
        if sha_for_text(entry['repro'])!=reg['repro_sha256'] or sha_for_text(entry['control'])!=reg['control_sha256']:
            raise RuntimeError('preregistered test body mismatch')
        checkout=Path(repos[entry['repo']]); src=checkout/entry['file']
        current={'id':entry['id'],'public_issue':reg['url'],'operator_source_frozen':True,
                 'repo_source_sha256':sha(src) if src.is_file() else None}
        if not src.is_file():
            current.update(status='INVALID_ENVIRONMENT',reason='missing expected source file')
            report['results'].append(current);continue
        contract=IssueContract(
            reg['url'], checkout, ('${PYTHON}','-c',entry['repro']),
            ('${PYTHON}','-c',entry['control']),frozenset((entry['file'],)),
            entry['fp'],timeout_seconds=20,repetitions=1,isolated_checks=True)
        # Preflight is read-only; all candidates will use full snapshot isolation.
        before=_run(contract.reproduce,checkout,20)
        normal=_run(contract.control,checkout,20)
        current['baseline']={'repro_exit':before['returncode'],
             'fingerprint_match':_broken(before,entry['fp']),
             'repro_timeout':before['timeout'],'control_pass':_passes(normal),
             'repro_stderr_tail':before['stderr'][-340:],
             'control_stderr_tail':normal['stderr'][-250:]}
        if not (_broken(before,entry['fp']) and _passes(normal)):
            current.update(status='INVALID_BASELINE',reason='bug not reproduced or control fails')
            report['results'].append(current)
            print(f'{entry["id"]}: INVALID_BASELINE',flush=True)
            continue
        current['status']='VALID_BASELINE'
        for model in (('generic','full') if a.models=='both' else (a.models,)):
            started=time.monotonic()
            try:
                if model=='generic':
                    with no_structural_generators():
                        result=synthesize(contract,entry['file'],max_candidates=registered['budgets']['candidates_per_case'],
                            max_seconds=registered['budgets']['seconds_per_case'],max_edits=1)
                else:
                    result=synthesize(contract,entry['file'],max_candidates=registered['budgets']['candidates_per_case'],
                        max_seconds=registered['budgets']['seconds_per_case'],max_edits=1)
                model_result={'status':result['status'],'attempts':result.get('attempts'),
                     'seconds':round(time.monotonic()-started,3),'operator':result.get('operator'),
                     'verified':result['status']=='VERIFIED_CANDIDATE',
                     'evidence_tier':result.get('receipt',{}).get('evidence_tier','NOT_SOLVED')}
                if result['status']=='VERIFIED_CANDIDATE':
                    model_result['patch_sha256']=hashlib.sha256(result['patch'].encode()).hexdigest()
                    (a.out.parent/f'{entry["id"]}_{model}.patch').write_text(result['patch'],encoding='utf8')
                current[model]=model_result
                print(f'{entry["id"]} {model}: {result["status"]} attempts={result.get("attempts")}',flush=True)
            except Exception as exc:
                current[model]={'status':'ENGINE_ERROR','error':f'{type(exc).__name__}: {exc}'[:500],
                                'seconds':round(time.monotonic()-started,3),'verified':False}
                print(f'{entry["id"]} {model}: ENGINE_ERROR {exc}',flush=True)
        report['results'].append(current)
        write(a.out, report)
    report['summary']={'registered':len(cases),
        'valid_baselines':sum(x['status']=='VALID_BASELINE' for x in report['results']),
        'invalid_baselines':sum(x['status']=='INVALID_BASELINE' for x in report['results']),
        'verified_full':sum(x.get('full',{}).get('verified',False) for x in report['results']),
        'verified_generic':sum(x.get('generic',{}).get('verified',False) for x in report['results']),
        'independent_upstream_prs_reviewed':0,
        'strong_external_apr_baseline_run':False,
        'limitations':'Not a statistically representative blind benchmark: issue descriptions were read; exact regression scripts were authored after operator freeze and before search; file scope is predeclared.'}
    write(a.out,report)
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2),flush=True)

def sha_for_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def write(path: Path, report:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

if __name__=='__main__':main()
