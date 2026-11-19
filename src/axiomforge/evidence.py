"""Fail-closed bundle verifier and replay against third-party libraries.

Hash catches accidental corruption but is not a cryptographic authenticity
proof, because anyone may rewrite the payload and recompute SHA256.
"""
from __future__ import annotations
import json,hashlib,pathlib,datetime
from typing import Any
from axiomforge.model import Status
from axiomforge.runner import run_one
from axiomforge.targets.registry import input_for,CONTRACTS
from axiomforge.minimize import ddmin
from axiomforge.guard_cegis import derive

def canonical(obj:Any)->bytes:
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf8")

def write(path:str|pathlib.Path,payload:dict[str,Any])->str:
    p=pathlib.Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    blob={"schema":"axiomforge.evidence.v2","created_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
          "payload":payload}
    blob["payload_sha256"]=hashlib.sha256(canonical(payload)).hexdigest()
    p.write_text(json.dumps(blob,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf8")
    return blob["payload_sha256"]

def verify(path:str|pathlib.Path,*,rerun:bool=True)->dict[str,Any]:
    raw=json.loads(pathlib.Path(path).read_text(encoding="utf8"))
    if set(raw)!={"schema","created_utc","payload","payload_sha256"}:raise ValueError("evidence schema keys are incorrect")
    if raw["schema"]!="axiomforge.evidence.v2":raise ValueError("unsupported schema")
    if hashlib.sha256(canonical(raw["payload"])).hexdigest()!=raw["payload_sha256"]:
        raise ValueError("evidence digest mismatch")
    p=raw["payload"]
    if set(p)!={"suite","searches","minimized","guard","limitations"}:raise ValueError("payload keys are incomplete or unknown")
    if not isinstance(p["searches"],list) or not p["searches"]:raise ValueError("no search evidence")
    checks=0;violations=0;observed_violations=0;seen=set();total_attempts=0
    unique_inputs=set()
    if p["suite"]["trials_per_target"]<1:raise ValueError("invalid declared budget")
    for search in p["searches"]:
        contract=search["contract"]["id"]
        if contract not in {"pandas.groupby.sort_false.groups","pandas.groupby.cow.view_irrelevance","sqlalchemy.pool.failed_connect_cleanup"}:
            raise ValueError("unknown contract")
        if search["evaluations"]!=len(search["observations"]):
            raise ValueError("missing or fabricated raw observations")
        if search["evaluations"]>search["candidates"] or search["evaluations"]<len(search["witnesses"]):
            raise ValueError("invalid budget accounting")
        if search["candidates"] != p["suite"]["trials_per_target"]:
            raise ValueError("inconsistent declared execution budget")
        target=next((k for k,c in CONTRACTS.items() if c.id==contract),None)
        if target is None:raise ValueError("unregistered contract")
        if search["contract"] != CONTRACTS[target].serial():raise ValueError("contract authority or metadata changed")
        fingerprints=set()
        for offset,row in enumerate(search["observations"]):
            total_attempts+=1
            expected_seed=p["suite"]["start_seed"]+offset
            if row["target"]!=target or row["contract"]!=contract or row["seed"]!=expected_seed:
                raise ValueError("execution order/contract mismatch")
            if row["input"]!=input_for(target,expected_seed):
                raise ValueError("input generator / sample provenance mismatch")
            unique_inputs.add((target,canonical(row["input"])))
            if row["status"]==Status.VERIFIED_RELATIONAL_VIOLATION.value:
                if row["relation_holds"] is not False or not row["admitted"]:
                    raise ValueError("invalid violation observation classification")
                observed_violations+=1
                fingerprints.add(canonical(row["input"]))
            if rerun:
                replay=run_one(target,row["input"],row["seed"])
                comparison=replay.serial()
                if {k:v for k,v in comparison.items() if k!="environment"}!={k:v for k,v in row.items() if k!="environment"}:
                    raise ValueError("real external replay mismatch")
                for lib in ("pandas","numpy","sqlalchemy"):
                    if comparison["environment"].get(lib)!=row["environment"].get(lib):
                        raise ValueError(f"dependency version drift: {lib}; pinned historical repro is not comparable")
            checks+=1
        published_fingerprints=set()
        for row in search["witnesses"]:
            ident=(row["target"],row["seed"],canonical(row["input"]))
            if ident in seen:raise ValueError("duplicate witness")
            seen.add(ident)
            if row["status"]!=Status.VERIFIED_RELATIONAL_VIOLATION.value or row["relation_holds"] is not False or not row["admitted"]:
                raise ValueError("invalid witness classification")
            fingerprint=canonical(row["input"])
            if fingerprint in published_fingerprints:raise ValueError("duplicate input witness")
            published_fingerprints.add(fingerprint)
            original=next((obs for obs in search["observations"] if obs["seed"]==row["seed"]),None)
            if original is None:raise ValueError("witness not traceable to raw observation")
            if {k:v for k,v in row.items() if k!="environment"}!={k:v for k,v in original.items() if k!="environment"}:
                raise ValueError("witness replay mismatch: original observation differs")
            for lib in ("pandas","numpy","sqlalchemy"):
                if row["environment"].get(lib)!=original["environment"].get(lib):
                    raise ValueError(f"dependency version drift: {lib}; witness dependency provenance differs")
            violations+=1
        if published_fingerprints!=fingerprints:raise ValueError("published witnesses do not match observed violations")
    for row in p["minimized"]:
        if row["after_rows"]>row["before_rows"] or not row["preserved"]:
            raise ValueError("shrinking not accepted")
        if rerun:
            result=run_one(row["target"],row["minimized"])
            if result.status!=Status.VERIFIED_RELATIONAL_VIOLATION:
                raise ValueError("minimized witness no longer reproduced")
            checks+=1
    guard=p["guard"]
    if guard["false_positives"]!=0 or guard["semantic_reference_disagreements"]!=0:
        raise ValueError("unsound guard claim")
    if guard["accepted"]+guard["rejected"]!=guard["total_domain_cases"]:
        raise ValueError("guard coverage arithmetic mismatch")
    if rerun:
        again=derive(max_rows=guard["domain"]["max_rows"])
        # Validate all significant claims, not wall-clock information.
        keys=("formula","terms","total_domain_cases","accepted","rejected","false_positives","false_negatives","semantic_reference_disagreements","cegis_rounds","oracle_evaluations")
        if any(again.serial()[k]!=guard[k] for k in keys):raise ValueError("bounded domain external replay mismatch")
        checks+=guard["total_domain_cases"]
    return {"status":"VERIFIED_WITH_REAL_LIBRARY_REPLAY" if rerun else "DIGEST_AND_STRUCTURE_ONLY",
            "replayed_checks":checks,"known_issue_witnesses":violations,
            "raw_external_attempts":total_attempts,"raw_violation_observations":observed_violations,
            "unique_input_signatures":len(unique_inputs),
            "guard_domain_size":guard["total_domain_cases"]}
