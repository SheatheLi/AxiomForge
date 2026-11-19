from __future__ import annotations
import argparse,json,sys
from .pipeline_audit import AuditError
from .factorized import evaluate_manifest


def main(argv=None):
    p=argparse.ArgumentParser(prog='axiomforge-factorized',description='Factorized multi-join risk audit')
    p.add_argument('--manifest',required=True)
    p.add_argument('--limit',type=int,default=30000,help='maximum materialized join rows to cross-check')
    p.add_argument('--gate',action='store_true',help='reject risk OR unverified model-only result')
    args=p.parse_args(argv)
    try:
        result=evaluate_manifest(args.manifest,limit=args.limit)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        if args.gate and (result['verdict']!='EXACTLY_ONCE_PRESERVED' or result['mode']!='MODEL_AND_REAL_BACKENDS'):
            return 3
        return 0
    except (AuditError,ValueError,OSError) as exc:
        print(json.dumps({'status':'REJECTED','error':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
