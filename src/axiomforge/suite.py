"""Experimental suite and independent control cases.

Pre-registration of labels happens at publication, *after* the search engine
selects failing inputs; no upstream fixed version is consulted during search.
"""
from __future__ import annotations
import statistics
from typing import Any
from axiomforge.model import Status
from axiomforge.runner import hunt,run_one,environment
from axiomforge.targets.registry import input_for
from axiomforge.guard_cegis import derive
from axiomforge.minimize import ddmin

def run_suite(budget:int=24,start_seed:int=0,*, isolated:bool=False,max_rows:int=3)->dict[str,Any]:
    searches=[]; minimized=[]
    for name in ("group-sort","cow-alias","sa-lifecycle"):
        result=hunt(name,budget,start_seed,isolated=isolated)
        searches.append(result.serial())
        if result.witnesses and name!="sa-lifecycle":
            minimized.append(ddmin(name,result.witnesses[0].input).serial())
    guard=derive(max_rows=max_rows).serial()
    return {"suite":{"name":"AxiomForge external library semantic witness + bounded guard CEGIS",
                      "external_versions":environment(),"trials_per_target":budget,
                      "start_seed":start_seed,"isolated_worker":isolated},
            "searches":searches,"minimized":minimized,"guard":guard,
            "limitations":["known upstream pandas historical issues, not new upstream discoveries",
                           "relationship contract sources are engineer-selected, not automatically mined",
                           "Boolean guard grammar is engineer-designed; expression learned",
                           "bounded exhaustive verification is not universal symbolic proof",
                           "no Hypothesis/Pynguin competitive benchmark has been executed",
                           "no online CI run or GitHub adoption verified"]}

def controls()->dict[str,Any]:
    sort=input_for("group-sort",1)
    right_sort=run_one("group-sort",sort,control={"sort":True})
    cow=input_for("cow-alias",1)
    safe_cow=run_one("cow-alias",cow,control={"copy_on_write":False})
    invalid_cow=run_one("cow-alias",cow,control={"semantic_change":True})
    return {"sort_true_rejected":right_sort.status.value,
            "cow_disabled":safe_cow.status.value,
            "semantic_change_rejected":invalid_cow.status.value,
            "all_expected":right_sort.status==Status.ABSTAIN and
              safe_cow.status==Status.CONSISTENT and invalid_cow.status==Status.ABSTAIN}
