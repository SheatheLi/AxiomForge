"""CLI for multi-repository, executable patch contracts and reduction."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .core import IssueError, evaluate, load_contract, minimize

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Independently check candidate upstream patches against executable Issue contracts')
    sp = p.add_subparsers(dest='op',required=True)
    for name in ('init','verify','minimize','synthesize','synthesize-repo','localize','dataflow','oracle-audit','portfolio','audit'):
        sub = sp.add_parser(name)
        if name == 'init':
            sub.add_argument('--issue-url',required=True)
            sub.add_argument('--checkout',required=True,type=Path)
            sub.add_argument('--repro',required=True,help='relative Python failing test script')
            sub.add_argument('--control',required=True,help='relative Python passing test script')
            sub.add_argument('--editable',required=True,action='append',help='allowlisted source file; repeat for more')
            sub.add_argument('--failure-fingerprint',required=True)
            sub.add_argument('--shared-checks',action='store_false',dest='isolated_checks',default=True,
                             help='opt out of fresh snapshot per check (faster, weaker evidence)')
            sub.add_argument('--out',required=True,type=Path)
        elif name == 'portfolio':
            sub.add_argument('--manifest',required=True,type=Path)
        else:
            sub.add_argument('--contract',required=True,type=Path)
        if name not in ('init','synthesize','synthesize-repo','localize','dataflow','oracle-audit','portfolio','audit'):
            sub.add_argument('--patch',required=True,type=Path)
        if name != 'init':
            sub.add_argument('--out',type=Path)
        if name == 'dataflow':
            sub.add_argument('--file', required=True)
            sub.add_argument('--line', required=True, type=int)
            sub.add_argument('--interprocedural', action='store_true',
                             help='lexically follow same-class self.method calls')
        if name == 'oracle-audit':
            sub.add_argument('--file', required=True)
            sub.add_argument('--max-mutants', type=int, default=12)
            sub.add_argument('--max-seconds', type=float, default=150)
            sub.add_argument('--seed', type=int, default=2026)
            sub.add_argument('--line-start', type=int, default=1)
            sub.add_argument('--line-end', type=int, default=100000)
        if name != 'init':
            sub.add_argument('--allow-exec',action='store_true',help='required: commands execute trusted checkout code')
        if name in ('synthesize', 'synthesize-repo'):
            sub.add_argument('--max-candidates',type=int,default=40)
            sub.add_argument('--max-seconds',type=float,default=180)
            sub.add_argument('--max-edits',type=int,choices=(1,2),default=1)
        if name == 'synthesize':
            sub.add_argument('--file',required=True)
            sub.add_argument('--line-start',type=int,default=1)
            sub.add_argument('--line-end',type=int,default=100000)
            sub.add_argument('--knobs',default='pop,strict,recursive,fixable')
        if name == 'synthesize-repo':
            sub.add_argument('--max-files',type=int,default=100)
            sub.add_argument('--spectrum',action='store_true',help='rank source files with dynamic failure vs control coverage')
            sub.add_argument('--dataflow',action='store_true',help='extend observed fault locations with reaching-definition backward slicing')
        if name in ('synthesize-repo','localize'):
            sub.add_argument('--max-trace-events',type=int,default=2000000)
        if name == 'audit':
            sub.add_argument('--max-hunks',type=int,default=12)
            sub.add_argument('--max-seconds',type=float,default=180)
        if name == 'minimize':
            sub.add_argument('--file',required=True)
            sub.add_argument('--max-evaluations',type=int,default=60)
            sub.add_argument('--max-seconds',type=float,default=180)
            sub.add_argument('--max-edits',type=int,choices=(1,2),default=1)
    args=p.parse_args(argv)
    if args.op == 'init':
        from .core import IssueContract
        try:
            checkout=args.checkout.resolve()
            for name in (args.repro,args.control):
                script=checkout/name
                if not script.is_file() or script.suffix != '.py' or not script.resolve().is_relative_to(checkout):
                    raise IssueError('repro/control must be existing Python scripts inside the repository')
            data={'schema':'axiomforge.issue-contract.v1','issue_url':args.issue_url,
                  'checkout':str(checkout),
                  'reproduce':['${PYTHON}',args.repro],
                  'control':['${PYTHON}',args.control],
                  'failure_fingerprint':args.failure_fingerprint,
                  'editable_files':args.editable,
                  'isolated_checks':args.isolated_checks,
                  'repetitions':2,'timeout_seconds':20}
            IssueContract.parse(data,base=args.out.parent.resolve())
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
            print(f'Created {args.out}: validate test behavior before synthesizing')
            return 0
        except (IssueError,OSError,ValueError) as exc:
            print(f'Cannot create contract: {exc}',file=sys.stderr)
            return 1
    if args.op != 'dataflow' and not args.allow_exec:
        p.error('This operation executes repository commands. Inspect and trust the source, then pass --allow-exec.')
    try:
        if args.op == 'portfolio':
            from .portfolio import review_portfolio
            receipt=review_portfolio(args.manifest)
        else:
            contract=load_contract(args.contract)
        if args.op == 'dataflow':
            from .dataflow import slice_file, interprocedural_slice
            if args.file not in contract.editable_files:
                raise IssueError('dataflow source must be an approved production file')
            target=contract.checkout / args.file
            if args.interprocedural and (target.suffix!='.py' or target.stat().st_size>350000):
                raise IssueError('interprocedural analysis requires a bounded Python source file')
            receipt = (interprocedural_slice(target.read_text('utf8'),args.line)
                       if args.interprocedural else slice_file(target, args.line))
            receipt['issue_url'] = contract.issue_url
        elif args.op == 'oracle-audit':
            from .oracle_audit import audit_oracle
            receipt = audit_oracle(contract, file=args.file, max_mutants=args.max_mutants,
                                   max_seconds=args.max_seconds, seed=args.seed,
                                   line_start=args.line_start, line_end=args.line_end)
        elif args.op=='audit':
            from .ablation import audit_hunk_necessity
            receipt=audit_hunk_necessity(contract,args.patch,max_hunks=args.max_hunks,max_seconds=args.max_seconds)
        elif args.op=='verify':
            receipt=evaluate(contract,args.patch)
        elif args.op=='portfolio':
            pass
        elif args.op=='synthesize-repo':
            from .search import synthesize_repository
            receipt=synthesize_repository(contract,max_candidates_per_file=args.max_candidates,
                max_seconds=args.max_seconds,max_files=args.max_files,
                use_spectrum=args.spectrum,max_trace_events=args.max_trace_events,
                max_edits=args.max_edits,use_dataflow=args.dataflow)
        elif args.op=='localize':
            from .localization import localize
            receipt=localize(contract,max_events=args.max_trace_events)
        elif args.op=='synthesize':
            from .synthesis import synthesize
            receipt=synthesize(contract,args.file,max_candidates=args.max_candidates,
                max_seconds=args.max_seconds,line_start=args.line_start,line_end=args.line_end,
                knobs=tuple(x.strip() for x in args.knobs.split(',') if x.strip()),
                max_edits=args.max_edits)
        else:
            receipt=minimize(contract,args.patch,args.file,
                max_evaluations=args.max_evaluations,max_seconds=args.max_seconds)
        result=json.dumps(receipt,ensure_ascii=False,indent=2)+'\n'
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(result,encoding='utf8')
        else:print(result)
        success=(receipt['passed'] if args.op=='verify' else
                 receipt['evidence_relative_necessary'] if args.op=='audit' else
                 receipt['status']=='VERIFIED_CANDIDATE' if args.op in ('synthesize','synthesize-repo') else
                 receipt['all_candidate_checks_passed'] if args.op=='portfolio' else True)
        return 0 if success else 2
    except (IssueError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'Issue contract rejected: {exc}',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
