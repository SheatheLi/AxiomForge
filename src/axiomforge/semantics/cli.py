"""CLI commands for the soundness-first semantic compiler and external replay."""
import argparse,json
from .compiler import compile_pandas_pair,CompileError
from .algebra import build_certificate,inspect_case,counterfactual_repair
from .experiment import PROGRAMS,run_experiment,save,verify

def cli(argv=None):
    p=argparse.ArgumentParser(prog='axiomforge semantics',description='Semantic-provenance compiler & replay')
    sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('compile');c.add_argument('--case',choices=tuple(PROGRAMS),default='sum_multi')
    e=sub.add_parser('experiment');e.add_argument('--seeds',type=int,default=48);e.add_argument('--out',default='artifacts/semantics/experiment.json')
    v=sub.add_parser('verify');v.add_argument('path');v.add_argument('--rerun',action='store_true')
    cf=sub.add_parser('counterfactual');cf.add_argument('--case',choices=tuple(PROGRAMS),default='sum_single')
    args=p.parse_args(argv)
    try:
        if args.action=='compile':
            pair=compile_pandas_pair(*PROGRAMS[args.case]);data=build_certificate(pair)
        elif args.action=='experiment':
            payload=run_experiment(args.seeds);sha=save(args.out,payload)
            data={'file':args.out,'sha256':sha,'cases':{r['case']:r['summary'] for r in payload['results']}}
        elif args.action=='verify':data=verify(args.path,rerun=args.rerun)
        elif args.action=='counterfactual':
            pair=compile_pandas_pair(*PROGRAMS[args.case]);row={k:None for k in pair.right.group_columns};row['amount']=3
            rows=[row,{**{k:'A' for k in pair.right.group_columns},'amount':2}]
            data=counterfactual_repair(pair,rows)
        else:raise RuntimeError('unknown mode')
        print(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True));return 0
    except (ValueError,ImportError,RuntimeError) as exc:
        print(json.dumps({'status':'FAILED','error':str(exc)},ensure_ascii=False));return 2
