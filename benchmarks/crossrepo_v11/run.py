"""Reproduce independent upstream issues against supplied external checkouts.

Usage: python benchmarks/crossrepo_v11/run.py --repos-root /path/to/checkouts --out /tmp/v11.json
Checkouts under --repos-root/urllib3, /requests, /httpcore, /sqlalchemy.
Source files must be writable and trusted: the evaluator runs their Python code.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,os.environ.get('AXIOMFORGE_ENGINE_SRC',str(ROOT/'src')))
from axiomforge.issue_engine.core import IssueContract, IssueError, _trial, _check_baseline
from axiomforge.issue_engine.synthesis import synthesize


def run_case(spec:dict,checkout:Path,root:Path,*,max_candidates:int=8,max_seconds:float=35)->dict:
    required=checkout/spec['file']
    if not required.is_file():return {'status':'SOURCE_MISSING','reason':spec['file']}
    expected=spec.get('source_sha256')
    actual=hashlib.sha256(required.read_bytes()).hexdigest()
    if expected and actual != expected:
        return {'status':'SOURCE_VERSION_MISMATCH','expected':expected,'actual':actual}
    script_rel=f'axiom_checks/{spec["id"]}'
    for testcase in ('repro.py','control.py'):
        dst=checkout/script_rel/testcase
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/'cases'/spec['id']/testcase,dst)
    extras=[]
    for optional_name in ('independent.py','real_http.py'):
        optional=root/'cases'/spec['id']/optional_name
        if optional.exists():
            shutil.copy2(optional,checkout/script_rel/optional_name)
            extras.append(['${PYTHON}',f'{script_rel}/{optional_name}'])
    contract_dict={'schema':'axiomforge.issue-contract.v1','issue_url':spec['issue'],
        'checkout':str(checkout.resolve()),'reproduce':['${PYTHON}',f'{script_rel}/repro.py'],
        'control':['${PYTHON}',f'{script_rel}/control.py'],'editable_files':[spec['file']],
        'failure_fingerprint':spec['fingerprint'],'timeout_seconds':8,'repetitions':1,
        'isolated_checks':True,
        'independent_checks':[],
        'independent_reproductions':extras}
    contract=IssueContract.parse(contract_dict,base=checkout)
    with tempfile.TemporaryDirectory(prefix='axiomforge-baseline-v11-') as d:
        baseline=_trial(contract,Path(d)/'baseline',None)
    try:_check_baseline(baseline)
    except IssueError as exc:
        return {'status':'NOT_REPRODUCIBLE','reason':str(exc),'baseline':baseline}
    outcome=synthesize(contract,spec['file'],line_start=spec['line_start'],
        line_end=spec['line_end'],max_candidates=max_candidates,max_seconds=max_seconds)
    return {'status':outcome['status'],'operator':outcome.get('operator'),
            'attempts':outcome.get('attempts',0),'seconds':outcome.get('seconds'),
            'patch':outcome.get('patch'),'receipt':outcome.get('receipt'),
            'rejections':outcome.get('rejections',[])[:3]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repos-root',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--max-candidates',type=int,default=8)
    parser.add_argument('--max-seconds',type=float,default=35)
    args=parser.parse_args()
    spec=json.loads((Path(__file__).parent/'manifest.json').read_text())
    outputs=[]
    with tempfile.TemporaryDirectory(prefix='axiomforge-crossrepo-v11-') as temp:
        for item in spec['cases']:
            source=args.repos_root/item['repo']
            dest=Path(temp)/item['id']
            if not source.is_dir():out={'status':'SOURCE_MISSING','reason':str(source)}
            else:
                shutil.copytree(source,dest,ignore=shutil.ignore_patterns('__pycache__','.git','.pytest_cache'))
                try:out=run_case(item,dest,Path(__file__).parent,max_candidates=args.max_candidates,max_seconds=args.max_seconds)
                except Exception as exc:out={'status':'ERROR','reason':str(exc)}
            outputs.append({'id':item['id'],'repository':item['repo'],'issue':item['issue'],
                            'source_sha256':item['source_sha256'],**out})
            print(item['id'],out['status'],out.get('operator'),flush=True)
    summary={s:sum(x['status']==s for x in outputs) for s in sorted(set(x['status'] for x in outputs))}
    result={'schema':'axiomforge.crossrepo-results.v1','assessment':'development-time diagnosis, NOT blinded unseen Issue sample',
            'case_count':len(outputs),'source_repository_count':len(set(x['repository'] for x in outputs)),
            'summary':summary,'cases':outputs}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return 0
if __name__=='__main__':raise SystemExit(main())
