"""Post-tuning regression/ablation on two *previously seen* upstream issues.

DO NOT present these as a held-out APR benchmark. The engine implementation
was revised after the issue descriptions were inspected. Original v0.8
8/8-baseline and 0/8 success results remain separate and immutable.

Run in trusted local checkouts only; tests execute the checkout's Python code.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.issue_engine.core import IssueContract
from axiomforge.issue_engine.synthesis import synthesize


def digest(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()


def sql_alchemy(root:Path)->tuple[IssueContract,str,tuple[int,int]]:
    head='from sqlalchemy.engine import make_url\nfrom sqlalchemy.dialects.postgresql.base import PGDialect\n'
    repro=head+'''try:
 result=PGDialect()._split_multihost_from_url(make_url('postgresql://u:p@/app?host=[2001:db8::1]:5432&host=[2001:db8::2]:5433'))
 assert result == (('2001:db8::1','2001:db8::2'),(5432,5433)), repr(result)
except Exception as e:
 raise AssertionError('MULTIHOST-BOUNDARY-FAIL') from e
'''
    control=head+'''x=PGDialect()._split_multihost_from_url(make_url('postgresql://u:p@/app?host=a.test:5432&host=b.test:5433'))
assert x == (('a.test','b.test'),(5432,5433)), repr(x)
'''
    extra=head+'''x=PGDialect()._split_multihost_from_url(make_url('postgresql://u:p@/app?host=c.test:4567&host=d.test:9999'))
assert x == (('c.test','d.test'),(4567,9999))
'''
    indep=head+'''for host, expected in [('[::1]:4567',('::1',4567)), ('[2001:db8::1]',('2001:db8::1',None))]:
 try:
  pair=PGDialect()._split_multihost_from_url(make_url('postgresql://u:p@/app?host='+host+'&host=e.test:1234'))
  assert pair[0][0]==expected[0] and pair[1][0]==expected[1],repr(pair)
 except Exception as exc:
  raise AssertionError('MULTIHOST-BOUNDARY-FAIL') from exc
'''
    rel='sqlalchemy/dialects/postgresql/base.py'
    return IssueContract('https://github.com/sqlalchemy/sqlalchemy/issues/13637',root,('${PYTHON}','-c',repro),
        ('${PYTHON}','-c',control),frozenset([rel]),'MULTIHOST-BOUNDARY-FAIL',
        timeout_seconds=20,repetitions=1,independent_checks=(('${PYTHON}','-c',extra),),
        independent_reproductions=(('${PYTHON}','-c',indep),),isolated_checks=False),rel,(3420,3440)


def sql_fluff(root:Path)->tuple[IssueContract,str,tuple[int,int]]:
    prefix='from sqlfluff import fix,lint\n'
    repro=prefix+'''x=fix('SELECT * FROM a JOIN b ON b.y <=> a.x;',dialect='mysql',rules=['ST09'])
assert 'a.x <=> b.y' in x and '>=>' not in x,'SYMMETRIC-OPERATOR-FAIL'
'''
    # The original weak validation omitted <= / >=, and wrongly allowed a
    # singleton-only guard. These are now mandatory independent checks.
    control=prefix+'''x=fix('SELECT * FROM a JOIN b ON b.y = a.x;',dialect='mysql',rules=['ST09'])
assert 'a.x = b.y' in x,x
'''
    matrix=prefix+'''for src,dest in [('<','>'),('>','<'),('<=','>='),('>=','<='),('<>','<>')]:
 x=fix(f'SELECT * FROM a JOIN b ON b.y {src} a.x;',dialect='mysql',rules=['ST09'])
 assert f'a.x {dest} b.y' in x,(src,x)
 assert not any(v['code']=='PRS' for v in lint(x,dialect='mysql')),(src,x)
'''
    other=prefix+'''x=fix('SELECT * FROM a JOIN b ON b.y <=> a.x;',dialect='sparksql',rules=['ST09'])
assert '<=>' in x and '>=>' not in x,x
'''
    rel='src/sqlfluff/rules/structure/ST09.py'
    return IssueContract('https://github.com/sqlfluff/sqlfluff/issues/8709',root,('${PYTHON}','-c',repro),
        ('${PYTHON}','-c',control),frozenset([rel]),'SYMMETRIC-OPERATOR-FAIL',
        timeout_seconds=20,repetitions=1,
        independent_checks=(('${PYTHON}','-c',matrix),('${PYTHON}','-c',other)),isolated_checks=False),rel,(269,269)


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sqlalchemy',required=True,type=Path)
    p.add_argument('--sqlfluff',required=True,type=Path)
    p.add_argument('--sqlfluff-deps',type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--allow-exec',action='store_true',required=True)
    p.add_argument('--case',choices=('both','sqlalchemy','sqlfluff'),default='both')
    p.add_argument('--models',choices=('both','expanded','old_family_ablation'),default='both')
    p.add_argument('--max-candidates',type=int,default=7)
    p.add_argument('--max-seconds',type=float,default=45)
    args=p.parse_args()
    from axiomforge.issue_engine import delimiter_repair,shape_guard_repair
    os.environ['PYTHONPATH']='src'
    if args.sqlfluff_deps:
        os.environ['PYTHONPATH']+=os.pathsep+str(args.sqlfluff_deps.resolve())
    # Other repo sources need to be at the front of sys.path during each run.
    # Subprocesses run with the checkout as current working directory.
    results=[]
    for id,build,path in (
        ('sqlalchemy-13637',sql_alchemy,args.sqlalchemy),
        ('sqlfluff-8709',sql_fluff,args.sqlfluff),
    ):
        if args.case != 'both' and not id.startswith(args.case):continue
        contract,rel,lines=build(path.resolve())
        snapshot=digest(contract.checkout/rel)
        oldenv=os.environ.get('PYTHONPATH','')
        # -c scripts import from the checkout via cwd, not installed packages.
        env=['src','.']
        if args.sqlfluff_deps:env.append(str(args.sqlfluff_deps.resolve()))
        os.environ['PYTHONPATH']=os.pathsep.join(env)
        row={'id':id,'public_issue':contract.issue_url,'upstream_source_sha256':snapshot,
             'classification':'RETROSPECTIVE_AFTER_ISSUE_REVIEW',
             'candidate_limit':args.max_candidates,'time_budget_seconds':args.max_seconds,'trials':{}}
        for model in ('expanded','old_family_ablation'):
            if args.models!='both' and model!=args.models:continue
            if model=='old_family_ablation':
                scopes=ExitStack()
                scopes.enter_context(patch.object(delimiter_repair,'delimiter_candidates',return_value=iter(())))
                scopes.enter_context(patch.object(shape_guard_repair,'singleton_guard_candidates',return_value=iter(())))
            else: scopes=ExitStack()
            with scopes:
                start=time.monotonic()
                result=synthesize(contract,rel,max_candidates=args.max_candidates,max_seconds=args.max_seconds,
                                  line_start=lines[0],line_end=lines[1])
            entry={'status':result['status'],'operator':result.get('operator'),
                   'attempts':result['attempts'],'elapsed_seconds':round(time.monotonic()-start,3)}
            if result['status']=='VERIFIED_CANDIDATE':
                patchname=id+'.patch'
                args.out.parent.mkdir(parents=True,exist_ok=True)
                patchpath=args.out.parent/patchname
                patchpath.write_text(result['patch'],encoding='utf8')
                entry['patch_sha256']=digest(patchpath)
                entry['evidence_tier']=result['receipt'].get('evidence_tier')
                entry['receipt_passed']=result['receipt']['passed']
            row['trials'][model]=entry
            print(f'{id} {model}: {entry["status"]}, attempts={entry["attempts"]}',flush=True)
        os.environ['PYTHONPATH']=oldenv
        results.append(row)
    report={'schema':'axiomforge.retrospective-ablation.v1',
            'research_integrity':'These issues were visible before the new families were developed; NOT a held-out benchmark or an estimate of unknown-bug repair rate.',
            'external_prs_submitted':0,'external_prs_merged':0,'cases':results}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'cases':len(results),'new_verified':sum(x['trials'].get('expanded',{}).get('status')=='VERIFIED_CANDIDATE' for x in results),'ablated_verified':sum(x['trials'].get('old_family_ablation',{}).get('status')=='VERIFIED_CANDIDATE' for x in results)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
