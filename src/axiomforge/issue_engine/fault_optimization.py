"""Weighted minimum hitting set for observed cleanup paths.

Borrowed from combinatorial optimization and lineage-driven fault injection.
Each path is a measured/declared independent successful cleanup alternative.
A selected interruption site intersects each *observed* path; this does not
prove the real async runtime actually becomes faulty at that site.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import numpy as np

@dataclass(frozen=True)
class Plan:
    selected: list[str]
    cost: float
    baseline_cost: float
    constraints: int
    solver: str
    optimal: bool


def optimal_fault_plan(paths: list[list[str]], costs: dict[str, float]) -> dict[str, Any]:
    from scipy.optimize import milp, LinearConstraint, Bounds
    if not paths or not costs or any(not p for p in paths):
        raise ValueError("Require nonempty paths and candidate costs")
    if any(not isinstance(k,str) or not k for k in costs):
        raise ValueError("Site IDs must be nonempty strings")
    if any(not np.isfinite(x) or x <= 0 for x in costs.values()):
        raise ValueError("Costs must be positive finite numbers")
    sites = sorted(costs)
    if len(sites)>512 or len(paths)>10000:
        raise ValueError("Explicit capacity limit exceeded")
    if any(not set(p).issubset(costs) for p in paths):
        raise ValueError("Every path site must be an eligible candidate")
    a = np.array([[float(site in path) for site in sites] for path in paths],dtype=float)
    c = np.array([costs[s] for s in sites],dtype=float)
    solution = milp(c,integrality=np.ones(len(sites)),bounds=Bounds(0,1),
                    constraints=LinearConstraint(a,1,np.inf),
                    options={"time_limit":10.0,"mip_rel_gap":0.0})
    if not solution.success or solution.x is None:
        return {"status":"unresolved","reason":str(solution.message),
                "scope":"No completeness or optimality claim"}
    chosen = [name for name,x in zip(sites,solution.x) if x>.5]
    assert all(set(p).intersection(chosen) for p in paths)
    return {"status":"optimal",**asdict(Plan(chosen,round(sum(costs[s] for s in chosen),8),
             round(sum(costs.values()),8),len(paths),"scipy.optimize.milp (HiGHS)",True)),
            "scope":"Exact weighted hitting set for the supplied paths only; no runtime soundness guarantee"}
