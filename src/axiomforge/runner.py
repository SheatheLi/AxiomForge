"""Relational failure witness search over genuine third-party APIs."""
from __future__ import annotations
import os,sys,json,subprocess,time,hashlib,platform
from typing import Any
from axiomforge.model import Witness,Status,SearchResult,Contract
from axiomforge.authority import check_authority
from axiomforge.admissibility import independent_checks
from axiomforge.targets import registry

def environment()->dict[str,str]:
    import pandas,numpy,sqlalchemy
    return {"python":platform.python_version(),"pandas":pandas.__version__,
            "numpy":numpy.__version__,"sqlalchemy":sqlalchemy.__version__,"platform":platform.system()}

def _execute(target: str, payload: dict[str,Any],control:dict[str,Any]|None=None,
             *, isolated:bool=False,timeout:float=12)->dict[str,Any]:
    if not isolated:
        return registry.execute(target,payload,control)
    request={"target":target,"payload":payload,"control":control or {}}
    proc=subprocess.run([sys.executable,"-m","axiomforge.worker"],input=json.dumps(request),
                        capture_output=True,text=True,timeout=timeout)
    if proc.returncode or not proc.stdout:
        raise RuntimeError(f"isolated worker failed: exit={proc.returncode} {proc.stderr[-900:]}")
    return json.loads(proc.stdout)

def run_one(target:str,payload:dict[str,Any],seed:int=0,control:dict[str,Any]|None=None,
            *,isolated:bool=False)->Witness:
    if target not in registry.CONTRACTS:raise KeyError(target)
    contract=registry.CONTRACTS[target]
    try:
        data=_execute(target,payload,control,isolated=isolated)
        checks=independent_checks(target,payload,control,data["state"])
        if checks!=data["checks"]:
            checks={"inputs_equivalent":False,"domain_valid":False,"semantic_change":True}
        admission=check_authority(contract,{"checks":checks})
        holds=data["holds"]
        if not admission.permitted:status=Status.ABSTAIN
        elif holds: status=Status.CONSISTENT
        else: status=Status.VERIFIED_RELATIONAL_VIOLATION
        return Witness(target=target,contract=contract.id,seed=seed,input=payload,
           state=data["state"],left=data["left"],right=data["right"],
           relation_holds=holds,admitted=admission.permitted,
           admission_evidence=admission.evidence,status=status,
           failure_reason=admission.reason if status==Status.ABSTAIN else
             ("relational mismatch under independently sourced contract" if not holds else ""),
           environment=environment(),known_issue=registry.ISSUES.get(target))
    except (ValueError,KeyError,RuntimeError,subprocess.TimeoutExpired) as e:
        return Witness(target=target,contract=contract.id,seed=seed,input=payload,
               state={},left=None,right=None,relation_holds=None,admitted=False,
               admission_evidence={},status=Status.EXECUTION_ERROR,
               failure_reason=f"{type(e).__name__}: {str(e)[:300]}",
               environment=environment(),known_issue=registry.ISSUES.get(target))

def hunt(target:str,budget:int=32,start_seed:int=0,*,isolated:bool=False,
         stop_first:bool=False)->SearchResult:
    if not 1<=budget<=100000:raise ValueError("budget out of range")
    if target not in registry.CONTRACTS:raise KeyError(target)
    started=time.perf_counter()
    witnesses=[]; observations=[]; seen=set();count=0
    for seed in range(start_seed,start_seed+budget):
        payload=registry.input_for(target,seed)
        w=run_one(target,payload,seed,isolated=isolated)
        count+=1
        observations.append(w)
        if w.status==Status.VERIFIED_RELATIONAL_VIOLATION:
            fingerprint=hashlib.sha256(json.dumps(w.input,sort_keys=True).encode()).hexdigest()
            if fingerprint not in seen:witnesses.append(w);seen.add(fingerprint)
            if stop_first:break
    return SearchResult(registry.CONTRACTS[target],budget,count,witnesses,time.perf_counter()-started,observations)
