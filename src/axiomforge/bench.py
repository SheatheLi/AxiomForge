"""Fair same-budget comparison, without misleading 'industry leading' claims."""
from __future__ import annotations
import random,time
from typing import Any
from axiomforge.runner import run_one
from axiomforge.targets.pandas_cases import input_for
from axiomforge.model import Status

# No preknowledge of exact failure-inducing seed. Different orderings of one
# immutable seed pool are used for both policies. This tests ordering only;
# this module is not a Hypothesis/Pynguin competitive evaluation.
def compare(per_target:int=40, seeds:int=12,search_budget:int=12)->dict[str,Any]:
    if not(2<=per_target<=1000 and 1<=seeds<=100 and 1<=search_budget<=per_target):
        raise ValueError("invalid benchmark parameters")
    cases={t:[input_for(t,i) for i in range(per_target)] for t in ("group-sort","cow-alias")}
    # Priority heuristic is input-only; does not see subject execution or bug ID.
    # For group-sort prefer inputs with many inversions, for CoW prefer a repeated
    # grouping key. This is intentionally a *weak* transparent control policy.
    def score(t,p):
        keys=p["keys"]
        if t=="group-sort":return sum(keys[i]>keys[j] for i in range(len(keys)) for j in range(i+1,len(keys)))
        return sum(keys.count(k)-1 for k in set(keys))
    results=[]
    for target in cases:
        items=cases[target]
        for seed in range(seeds):
            order=list(range(per_target))
            random.Random(seed+811).shuffle(order)
            heuristic=sorted(order,key=lambda i:(-score(target,items[i]),i))
            for policy,indices in (("random",order[:search_budget]),("input-heuristic",heuristic[:search_budget])):
                start=time.perf_counter()
                hits=0
                first=None
                for j,i in enumerate(indices):
                    w=run_one(target,items[i],i)
                    if w.status==Status.VERIFIED_RELATIONAL_VIOLATION:
                        hits+=1
                        if first is None:first=j+1
                results.append({"target":target,"trial_seed":seed,"policy":policy,
                                "budget":len(indices),"detections":hits,
                                "first_failure_position":first,"seconds":time.perf_counter()-start})
    agg={}
    for target in cases:
        for policy in ("random","input-heuristic"):
            part=[x for x in results if x["target"]==target and x["policy"]==policy]
            key=f"{target}:{policy}"
            agg[key]={"trials":len(part),"discoveries":sum(x["first_failure_position"] is not None for x in part),
                      "avg_unique_budget":search_budget,"mean_failure_position":
                       sum((x["first_failure_position"] or search_budget+1) for x in part)/len(part),
                      "total_wall_seconds":sum(x["seconds"] for x in part)}
    return {"method":"input-priority heuristic versus random (NOT mature tool competition)",
            "per_target":per_target,"search_budget":search_budget,"seeds":seeds,
            "unit":"distinct upstream issue families, NOT repeated runs as unique defects",
            "raw":results,"aggregate":agg}
