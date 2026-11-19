"""Real replay-based delta debugging, never a synthetic flag-only shrink."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from typing import Any
from axiomforge.runner import run_one
from axiomforge.model import Status

@dataclass
class Minimized:
    target:str
    original:dict[str,Any]
    minimized:dict[str,Any]
    before_rows:int
    after_rows:int
    replays:int
    preserved:bool
    trace:list[dict[str,Any]]
    budget_exhausted:bool=False
    def serial(self)->dict[str,Any]:return asdict(self)

def ddmin(target:str,payload:dict[str,Any],max_replays:int=500)->Minimized:
    """Delete chunks of rows, rerunning actual pandas each time. Preserves the
    exact relational violation and source admissibility. 1-minimal *within the
    row-deletion grammar*; does not guarantee globally shortest witness.
    """
    if not isinstance(max_replays, int) or isinstance(max_replays, bool) or max_replays < 1:
        raise ValueError("max_replays must be a positive integer")
    if (not isinstance(payload.get("keys"), list) or
        not isinstance(payload.get("values"), list) or
        len(payload["keys"]) != len(payload["values"])):
        raise ValueError("keys/values must be equally sized lists")
    candidate={k:(v.copy() if isinstance(v,list) else v) for k,v in payload.items()}
    size=len(candidate["keys"])
    replays=1; trace=[]
    original=run_one(target,candidate)
    if original.status!=Status.VERIFIED_RELATIONAL_VIOLATION:
        return Minimized(target,payload,candidate,size,size,replays,False,trace)
    def test(try_payload:dict[str,Any])->bool:
        nonlocal replays
        replays+=1
        w=run_one(target,try_payload)
        return w.status==Status.VERIFIED_RELATIONAL_VIOLATION
    granularity=2
    while len(candidate["keys"])>=2 and replays<max_replays:
        n=len(candidate["keys"])
        chunk=(n+granularity-1)//granularity
        reduced=False
        for start in range(0,n,chunk):
            drop=set(range(start,min(start+chunk,n)))
            nxt={k:([x for i,x in enumerate(v) if i not in drop]
                     if k in ("keys","values") else v) for k,v in candidate.items()}
            if len(nxt["keys"])<2:continue
            if replays>=max_replays:break
            if test(nxt):
                trace.append({"removed_indices":sorted(drop),"rows":len(nxt["keys"])})
                candidate=nxt;granularity=max(2,granularity-1);reduced=True;break
        if reduced:continue
        if granularity>=n:break
        granularity=min(n,2*granularity)
    # Every accepted candidate was just verified by the actual replay oracle.
    # Avoid an uncounted final replay: at a hard budget this used to call the
    # target once more while claiming replays <= max_replays.
    return Minimized(target,payload,candidate,size,len(candidate["keys"]),
                     replays,True,trace,budget_exhausted=(replays>=max_replays))
