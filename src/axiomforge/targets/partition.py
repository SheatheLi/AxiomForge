"""Finite-domain semantic guard synthesis fixture using real pandas behavior.

The *grammar* (features) is supplied by an engineer, but the Boolean guard
formula is mined via bounded CEGIS. The independent mathematical reference
sums the rows based on membership, not on the GroupBy result being judged.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
from typing import Iterator,Any
import pandas as pd

LABELS = ("A", "B", None)
VALUES = (-1,0,1)
FEATURES = ("no_null_keys","keep_null_groups","zero_missing_sum","length_ge_2")

@dataclass(frozen=True)
class PartitionInput:
    keys: tuple[str|None,...]
    values: tuple[int,...]
    dropna: bool

    def serial(self) -> dict[str, Any]:
        return {"keys":list(self.keys),"values":list(self.values),"dropna":self.dropna}

def bounded_cases(max_rows: int = 3) -> Iterator[PartitionInput]:
    if not 1 <= max_rows <= 4:
        raise ValueError("max_rows must be in [1, 4]; avoid accidental combinatorial explosion")
    for n in range(1,max_rows+1):
        for keys in product(LABELS,repeat=n):
            for vals in product(VALUES,repeat=n):
                for dropna in (False,True):
                    yield PartitionInput(keys,vals,dropna)

def feature_values(p: PartitionInput) -> dict[str,bool]:
    missing=sum(value for key,value in zip(p.keys,p.values) if key is None)
    return {"no_null_keys":None not in p.keys, "keep_null_groups":not p.dropna,
            "zero_missing_sum":missing==0, "length_ge_2":len(p.keys)>=2}

def independent_model(p: PartitionInput) -> tuple[int,int,bool]:
    """Reference uses direct row semantics and no pandas.GroupBy calls."""
    total=sum(p.values)
    expected=sum(v for k,v in zip(p.keys,p.values) if k is not None or not p.dropna)
    return total,expected,total==expected

def actual(p: PartitionInput) -> tuple[int,int,bool]:
    # Explicit dropna parameter is important; tests must not infer from default.
    df = pd.DataFrame({"group":list(p.keys),"amount":list(p.values)})
    total = int(df["amount"].sum())
    grouped = int(df.groupby("group",dropna=p.dropna)["amount"].sum().sum())
    return total,grouped,total==grouped
