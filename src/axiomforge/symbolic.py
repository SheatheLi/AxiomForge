"""Small independent algebraic certificate for the PARTITION REFERENCE MODEL.

This does NOT prove arbitrary Pandas implementation correctness. It proves
that the relation and learned predicate match a mathematical decomposition of
partitioned integer sums; library correctness remains a separate experiment.
"""
from __future__ import annotations
from typing import Any

def prove_partition_model()->dict[str,Any]:
    try:
        import sympy as sp
    except ImportError:
        return {"status":"NOT_RUN","reason":"sympy optional extra not installed"}
    kept,missing = sp.symbols('kept missing',integer=True)
    dropna=sp.symbols('dropna',integer=True)
    total=kept+missing
    observed=kept+(1-dropna)*missing
    difference=sp.simplify(total-observed)
    check_keep=sp.simplify(difference.subs(dropna,0))==0
    check_zero=sp.simplify(difference.subs(missing,0))==0
    check_counterexample=sp.simplify(difference.subs({dropna:1,missing:1}))!=0
    result={"status":"MODEL_PROVED" if all([check_keep,check_zero,check_counterexample]) else "MODEL_DISPROVED",
      "symbolic_difference":str(difference),"guard":"(dropna = 0) OR (missing_sum = 0)",
      "assumptions":["commutative exact integer addition", "partition is exhaustive before dropna",
                     "dropna excludes every null-key row", "no integer overflow or float rounding",
                     "does not assert any statement about entire Pandas implementation"],
      "proof_obligations":{"keep_null":bool(check_keep),"zero_missing_sum":bool(check_zero),
                           "nontrivial_negative_control":bool(check_counterexample)},
      "sympy_version":sp.__version__}
    if result['status']!='MODEL_PROVED':raise AssertionError('model proof obligation failed')
    return result
