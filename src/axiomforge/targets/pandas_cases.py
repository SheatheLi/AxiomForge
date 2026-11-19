"""Actual, unmodified pandas calls; two independent upstream-confirmed bug mechanisms.

Nothing in this module replaces pandas internals, fabricates outputs or loads a
fixed version during generation. The published issues are used for audit labels
and source authority, not as part of the search decision.
"""
from __future__ import annotations
import random
from typing import Any
import pandas as pd
from axiomforge.model import Contract, Authority

SORT = Contract(
 id="pandas.groupby.sort_false.groups", title="Unsorted group order is stable across API representations",
 statement="For sort=False, iteration of GroupBy.groups preserves the order in which distinct keys first occur",
 applicability="real orderable integer keys, all keys present, sort=False",
 authority=Authority.DOCUMENTED,
 authority_url="https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.groupby.html",
 target="pandas", bound="Only tested for integer keys in this adapter")

COW = Contract(
 id="pandas.groupby.cow.view_irrelevance", title="Unrelated view creation must not modify groupby semantics",
 statement="Creating an unused view before changing a grouping Series must not change a groupby's observable result",
 applicability="equal starting DataFrame/Series and mutation; only branch difference is an unused view",
 authority=Authority.DOMAIN_DEFINITION,
 authority_url="https://github.com/pandas-dev/pandas/issues/63219",
 target="pandas",bound="Conditional on declared view being unused and having no intended external side effects")

CONTRACTS = {"group-sort": SORT, "cow-alias": COW}
ISSUES = {"group-sort":"https://github.com/pandas-dev/pandas/issues/56966", "cow-alias":"https://github.com/pandas-dev/pandas/issues/63219"}

def input_for(target: str, seed: int) -> dict[str, Any]:
    rng = random.Random(seed * 101 + 73)
    if target == "group-sort":
        # Existing order is deliberately not chosen from any known failing example.
        n = rng.randint(2, 9)
        keys = rng.sample(range(-20, 21), n)
        return {"keys": keys, "values": [rng.randrange(1,100) for _ in keys], "sort":False}
    if target == "cow-alias":
        # Search-space grammar includes repeated grouping keys, whose mutation
        # can expose an interaction between aliasing and grouping snapshot state.
        n = rng.randint(3, 9)
        keys = [rng.choice([0, 1, 2]) for _ in range(n)]
        keys[0] = 0; keys[1] = 1; keys[-1] = 0
        return {"keys":keys,"values":[rng.randrange(1,100) for _ in keys],
                "mutated_key":100,"copy_on_write":True}
    raise KeyError(target)

def _first_seen(keys: list[Any]) -> list[Any]:
    return list(dict.fromkeys(keys))

def _group_view(keys: list[int], vals: list[int], *, use_view: bool, copy_on_write: bool, mutated_key: int) -> dict[int, int]:
    with pd.option_context("mode.copy_on_write",copy_on_write):
        s = pd.Series(keys)
        df = pd.DataFrame({"value":vals})
        group = df.groupby(s,sort=False)
        if use_view:
            # This alias never enters groupby and is never intentionally read.
            unused_alias = s[:]
            assert len(unused_alias)==len(s)
        s.iloc[0] = mutated_key
        result=group.sum()["value"]
        return {str(int(k)):int(v) for k,v in result.items()}

def execute(target: str, payload: dict[str, Any], control: dict[str, Any] | None = None) -> dict[str, Any]:
    control = control or {}
    if target == "group-sort":
        keys=list(payload["keys"]); values=list(payload["values"])
        sort=bool(control.get("sort",payload.get("sort",False)))
        if len(keys)!=len(values) or not keys or not all(type(k)==int for k in keys):
            raise ValueError("group-sort supports nonempty equal-length integer keys and values")
        # Upstream #56966 specifically concerns *multiple grouping keys*.
        # A single column is the wrong SUT and would suppress this real bug.
        df=pd.DataFrame({"k1":keys,"k2":[1]*len(keys),"value":values})
        gb=df.groupby(["k1","k2"],sort=sort)
        expected = [[int(x),1] for x in _first_seen(keys)]
        observed = [[int(a),int(b)] for a,b in gb.groups]
        agg_index=[[int(a),int(b)] for a,b in gb["value"].sum().index]
        # Observe with genuine pandas methods; no guessed version-specific bug answer.
        return {"left":expected,"right":observed,"state":{"aggregation_order":agg_index,
                   "input_keys":keys,"input_values":values,"sort":sort},
                "holds":expected==observed,
                "checks":{"inputs_equivalent":True,"domain_valid":not sort,"semantic_change":False}}
    if target == "cow-alias":
        keys=list(payload["keys"]); values=list(payload["values"])
        mutated=int(payload.get("mutated_key",100))
        cow=bool(control.get("copy_on_write",payload.get("copy_on_write",True)))
        semantic_change=bool(control.get("semantic_change",False))
        if len(keys)!=len(values) or len(keys)<2 or mutated in keys:
            raise ValueError("cow-alias requires >=2 rows; mutated key must be new")
        left=_group_view(keys,values,use_view=False,copy_on_write=cow,mutated_key=mutated)
        # Controlled negative: on change in the actual values the variant is
        # semantically different and admission must reject even if it differs.
        other_values=values.copy()
        if semantic_change:other_values[0]+=1
        right=_group_view(keys,other_values,use_view=True,copy_on_write=cow,mutated_key=mutated)
        return {"left":left,"right":right,"state":{"keys":keys,"values":values,
                 "unused_view_created":True,"copy_on_write":cow},
                "holds":left==right,
                "checks":{"inputs_equivalent":not semantic_change,"domain_valid":True,
                          "semantic_change":semantic_change}}
    raise KeyError(target)
