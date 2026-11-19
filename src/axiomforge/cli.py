"""One cohesive installable developer tool, not independent experiment scripts."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from axiomforge.runner import environment,hunt,run_one
from axiomforge.suite import run_suite,controls
from axiomforge.guard_cegis import derive
from axiomforge.evidence import write,verify
from axiomforge.report import create
from axiomforge.minimize import ddmin
from axiomforge.bench import compare

def _json(obj):
    print(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True))

def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description="AxiomForge: condition-aware relational failure evidence")
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("doctor",help="Print actual software environment and negative controls")
    sem=sub.add_parser("semantics",help="Proof-carrying typed semantic guard compiler and third-party replay")
    sem.add_argument("semantic_args",nargs=argparse.REMAINDER)
    full=sub.add_parser("run",help="Run external SUT search and bounded CEGIS, save raw evidence")
    full.add_argument("--budget",type=int,default=24);full.add_argument("--start-seed",type=int,default=0)
    full.add_argument("--max-rows",type=int,default=3);full.add_argument("--isolated",action="store_true")
    full.add_argument("--out",default="artifacts/full.json")
    test=sub.add_parser("hunt",help="Find relational violations of one contract")
    test.add_argument("target",choices=("group-sort","cow-alias","sa-lifecycle"))
    test.add_argument("--budget",type=int,default=32);test.add_argument("--isolated",action="store_true")
    test.add_argument("--first",action="store_true")
    sub.add_parser("prove-model",help="Independent SymPy proof for exact-additive partition reference semantics")
    small=sub.add_parser("synth",help="Counterexample-guided bounded guard synthesis")
    small.add_argument("--max-rows",type=int,default=3)
    vf=sub.add_parser("verify",help="Independent structure and real SUT replay")
    vf.add_argument("path");vf.add_argument("--rerun",action="store_true",help="Required for real evidence validation")
    rp=sub.add_parser("report",help="Generate inspectable offline HTML from evidence")
    rp.add_argument("path");rp.add_argument("--out",default="artifacts/report.html")
    mm=sub.add_parser("minimize",help="Delete rows and re-execute actual third-party SUT")
    mm.add_argument("target",choices=("group-sort","cow-alias"));mm.add_argument("--seed",type=int,default=0)
    bb=sub.add_parser("benchmark",help="Transparent weak heuristic-vs-random sanity comparator")
    bb.add_argument("--budget",type=int,default=12);bb.add_argument("--seeds",type=int,default=12)
    bb.add_argument("--out",default="artifacts/weak_baseline.json")
    args=p.parse_args(argv)
    try:
        if args.cmd=="semantics":
            from axiomforge.semantics.cli import cli as semantics_cli
            return semantics_cli(args.semantic_args)
        if args.cmd=="doctor":
            env=environment();ct=controls();_json({"environment":env,"negative_controls":ct})
            return 0 if ct["all_expected"] else 3
        if args.cmd=="run":
            payload=run_suite(args.budget,args.start_seed,isolated=args.isolated,max_rows=args.max_rows)
            digest=write(args.out,payload)
            _json({"file":args.out,"sha256":digest,"sources":[x['contract']['authority_url'] for x in payload['searches']],
                   "distinct_witnesses":[len(x['witnesses']) for x in payload['searches']],
                   "raw_violations":[sum(y['status']=='VERIFIED_RELATIONAL_VIOLATION' for y in x['observations']) for x in payload['searches']],
                   "guard":payload['guard']['formula'],"guard_false_positives":payload['guard']['false_positives']})
            return 0
        if args.cmd=="hunt":
            _json(hunt(args.target,args.budget,isolated=args.isolated,stop_first=args.first).serial());return 0
        if args.cmd=="synth":_json(derive(args.max_rows).serial());return 0
        if args.cmd=="prove-model":
            from axiomforge.symbolic import prove_partition_model
            obj=prove_partition_model();_json(obj);return 0 if obj["status"]=="MODEL_PROVED" else 4
        if args.cmd=="verify":_json(verify(args.path,rerun=args.rerun));return 0
        if args.cmd=="report":_json({"html":create(args.path,args.out)});return 0
        if args.cmd=="minimize":
            from axiomforge.targets.registry import input_for
            _json(ddmin(args.target,input_for(args.target,args.seed)).serial());return 0
        if args.cmd=="benchmark":
            data=compare(search_budget=args.budget,seeds=args.seeds)
            out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf8")
            _json({"file":str(out),"aggregate":data["aggregate"]});return 0
    except Exception as exc:
        print(f"AXIOMFORGE_ERROR {type(exc).__name__}: {exc}",file=sys.stderr)
        return 2
    return 2
if __name__=="__main__":raise SystemExit(main())
