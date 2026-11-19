"""Research CLI for bounded model checking, fault optimization and live repair comparison."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .cleanup_model import verify_bounded


def main(argv=None):
    parser=argparse.ArgumentParser(description="AxiomForge reliability research tools")
    sub=parser.add_subparsers(dest="command",required=True)
    model=sub.add_parser("model-check")
    model.add_argument("--attempts",type=int,default=2)
    model.add_argument("--max-interruptions",type=int,default=1)
    plan=sub.add_parser("plan")
    plan.add_argument("file",type=Path)
    patch=sub.add_parser("emit-patch")
    patch.add_argument("--out",type=Path)
    live=sub.add_parser("live-compare")
    live.add_argument("--repetitions",type=int,default=4)
    live.add_argument("--out",type=Path)
    triage=sub.add_parser("triage",help="Detect, reduce and compare a real HTTPX cancellation failure")
    triage.add_argument("--outdir",type=Path,required=True)
    triage.add_argument("--symptom",choices=("pool_exhaustion","stranded_active"),default="pool_exhaustion")
    triage.add_argument("--repetitions",type=int,default=5)
    triage.add_argument("--confirmations",type=int,default=2)
    triage.add_argument("--timeout",type=int,default=35)
    triage.add_argument("--no-repair-comparison",action="store_true")
    replay=sub.add_parser("replay-triage",help="Replay a minimized native failure in a clean process")
    replay.add_argument("record",type=Path)
    replay.add_argument("--out",type=Path,required=True)
    replay.add_argument("--timeout",type=int,default=35)
    args=parser.parse_args(argv)
    if args.command in ("triage", "replay-triage"):
        from .runtime_triage import NativeRunFailure, triage as native_triage, replay as native_replay
        try:
            if args.command == "triage":
                result=native_triage(args.outdir, symptom=args.symptom, repetitions=args.repetitions,
                                    confirmations=args.confirmations, timeout=args.timeout,
                                    compare_repair=not args.no_repair_comparison)
            else:
                result=native_replay(args.record,args.out,timeout=args.timeout)
        except (ValueError, OSError, NativeRunFailure) as exc:
            parser.exit(1, f"Native triage failed: {exc}\n")
        print(json.dumps({"status":result["status"],"report":str(args.outdir / 'triage.json') if args.command=='triage' else str(args.out),
                          "minimal_repetitions":result.get("prefix_reduction",{}).get("minimum_repetitions")},
                          indent=2,ensure_ascii=False))
        return 0 if result["status"] == "REPRODUCED" else 2
    if args.command=="model-check":
        result={x:verify_bounded(x,attempts_limit=args.attempts,max_interruptions=args.max_interruptions) for x in ("early-commit","defer-commit")}
    elif args.command=="plan":
        from .fault_optimization import optimal_fault_plan
        obj=json.loads(args.file.read_text(encoding="utf-8"))
        result=optimal_fault_plan(obj["paths"],obj["costs"])
    elif args.command=="emit-patch":
        try:
            from .transactional_cleanup import upstream_rearm_patch
            patch=upstream_rearm_patch()
        except (ImportError, RuntimeError) as exc:
            parser.error(f"emit-patch requires a verified HTTPcore 1.0.9 installation: {exc}")
        if args.out:
            args.out.parent.mkdir(parents=True,exist_ok=True)
            args.out.write_text(patch,encoding="utf-8")
        print(patch,end="")
        return 0
    else:
        # import the local benchmark by path only for a development checkout;
        # pip wheel supports model-check/plan without bundling benchmark scripts.
        raise SystemExit("live-compare: run benchmarks/reliability/run_live_comparison.py from the source checkout")
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=="__main__":raise SystemExit(main())
