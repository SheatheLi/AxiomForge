"""Run AxiomForge's own repair search on independent external repositories.

No prewritten patch is passed into synthesize(). All listed third-party source
checkouts are user-provided and executed only after --allow-exec.  This tool
is offline after dependencies are installed and never changes those checkouts.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import sys
import os

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.issue_engine.core import IssueError, load_contract
from axiomforge.issue_engine.synthesis import synthesize

CASES={
    'sqlfluff_am07':('sqlfluff','src/sqlfluff/rules/ambiguous/AM07.py'),
    'sqlfluff_8714':('sqlfluff','src/sqlfluff/rules/references/RF03.py'),
    'sqlfluff_8562':('sqlfluff','src/sqlfluff/rules/structure/ST01.py'),
    'sqlfluff_8624':('sqlfluff','src/sqlfluff/rules/layout/LT09.py'),
    'sqlalchemy_13548':('sqlalchemy','sqlalchemy/pool/base.py'),
    'httpcore_1115':('httpcore','httpcore/_async/connection_pool.py'),
}

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description='Generate and verify repair proposals in independent upstream repositories')
    for name in ('sqlfluff','sqlalchemy','httpcore'):
        parser.add_argument('--'+name,type=Path,help=f'extracted {name} source checkout')
    parser.add_argument('--out',type=Path,default=ROOT/'results/real_issue_study')
    parser.add_argument('--allow-exec',action='store_true',help='confirm that each external repository is trusted for local subprocess execution')
    parser.add_argument('--max-seconds',type=float,default=100)
    args=parser.parse_args(argv)
    if not args.allow_exec:
        parser.error('third-party source executes subprocesses; inspect it and pass --allow-exec')
    inputs={k:getattr(args,k) for k in ('sqlfluff','sqlalchemy','httpcore')}
    if not any(inputs.values()):parser.error('supply at least one --sqlfluff / --sqlalchemy / --httpcore checkout')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    cases=[]
    for case,(project,src) in CASES.items():
        if inputs[project] is None:continue
        repo=inputs[project].resolve()
        if not (repo/src).is_file():
            raise SystemExit(f'missing production file in {repo}: {src}')
        template=json.loads((ROOT/'examples/real_issues'/f'{case}.json').read_text('utf8'))
        template['checkout']=str(repo)
        contract_path=out/f'{case}.json'
        contract_path.write_text(json.dumps(template,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        try:
            contract=load_contract(contract_path)
            outcome=synthesize(contract,src,max_candidates=10,max_seconds=args.max_seconds)
            if outcome.get('status')=='VERIFIED_CANDIDATE':
                (out/f'{case}.patch').write_text(outcome['patch'],encoding='utf8')
            record={k:v for k,v in outcome.items() if k != 'patch'}
            (out/f'{case}_evidence.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            cases.append({'case':case,'issue':contract.issue_url,'status':outcome['status'],
                          'generator':outcome.get('operator'),'attempts':outcome.get('attempts'),
                          'patch':f'{case}.patch' if outcome.get('patch') else None})
            print(f'{case}: {outcome["status"]} via {outcome.get("operator", "none")}')
        except (IssueError,OSError,ValueError) as e:
            cases.append({'case':case,'status':'ERROR','error':str(e)[:500]})
            print(f'{case}: ERROR {e}',file=sys.stderr)
    summary={'schema':'axiomforge.autonomous-repair-study.v1','results':cases,
             'created_from_prewritten_patches':False,'upstream_prs_submitted':0,
             'claims':'Local AST-generated candidates verified by executable tests; no maintainers reviewed or merged them.'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return 0 if cases and all(x['status']=='VERIFIED_CANDIDATE' for x in cases) else 2

if __name__=='__main__':raise SystemExit(main())
