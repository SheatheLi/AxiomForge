"""CLI for bounded cancellation-site fault injection on trusted local cases."""
from __future__ import annotations
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

from .cancellation_safety import explore, explore_nested_cancellation, Instrumented


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description='Bounded async cancellation-site exploration; executes TRUSTED scenario code')
    p.add_argument('--scenario',required=True,help='absolute/local path.py:function returning (instrumented, fresh_factory)')
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--max-sites',type=int,default=50)
    p.add_argument('--max-depth',type=int,default=1,choices=(1,2,3),help='nested cancellation schedule depth; 1=single site')
    p.add_argument('--timeout',type=float,default=1)
    p.add_argument('--risk-guided', action='store_true', help='requires Scenario.risk_probe; heuristic, not completeness preserving')
    p.add_argument('--allow-exec',action='store_true',help='required: executes trusted Python source and async workload')
    args=p.parse_args(argv)
    if not args.allow_exec:
        p.error('Explicit --allow-exec is required: scenario Python code is executed')
    try:
        path, sep, factory_name=args.scenario.rpartition(':')
        if not sep or not path or not factory_name.isidentifier():raise ValueError('Expected path.py:function')
        source=Path(path).resolve(strict=True)
        if source.suffix!='.py' or not source.is_file():raise ValueError('Expected an existing Python source file')
        spec=importlib.util.spec_from_file_location('axiom_user_scenario',source)
        if not spec or not spec.loader:raise ValueError('Unable to load source')
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        setup=getattr(mod,factory_name)
        unit, factory=setup()
        if not isinstance(unit,Instrumented) or not callable(factory):
            raise TypeError('Scenario setup must return (Instrumented, factory)')
        if args.risk_guided and args.max_depth != 1:
            raise ValueError("--risk-guided currently only supports --max-depth=1")
        receipt=(asyncio.run(explore(factory,unit,max_sites=args.max_sites,timeout=args.timeout, risk_guided=args.risk_guided))
                 if args.max_depth==1 else
                 asyncio.run(explore_nested_cancellation(factory,unit,max_depth=args.max_depth,
                                                        max_runs=args.max_sites,timeout=args.timeout)))
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        print(json.dumps({'runs':len(receipt.get('experiments',receipt.get('runs',[]))),
                          'violations':receipt['violations'],
                          'unresolved':receipt.get('unknown_results',
                                      sum(x['status'] in ('TIMEOUT','UNREACHED_SITE') for x in receipt.get('runs',[]))),
                          'out':str(args.out)},ensure_ascii=False))
        unresolved=(receipt.get('unknown_results',0) or
                    any(x['status'] in ('TIMEOUT','UNREACHED_SITE') for x in receipt.get('runs',[])))
        return 2 if receipt['violations'] else 3 if unresolved else 0
    except (ValueError,TypeError,OSError,AttributeError) as exc:
        print(f'Cannot run scenario: {exc}',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
