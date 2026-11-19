"""Provenance-aware exact-additive difference compiler.

For every row i and normalized selection mask, produce
    D = Σ_i (left_included_i - right_included_i) * contribution_i.
Thus D=0 iff the two *mathematical* aggregates are equal. This is an
algebraic certificate for the restricted IR, NOT a proof that arbitrary
third-party library implementations conform to it.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from .compiler import ProgramPair

class DomainError(ValueError):
    pass

@dataclass(frozen=True)
class Row:
    values: dict[str,Any]
    def serial(self): return self.values.copy()


def _validated_rows(pair:ProgramPair, rows:list[dict[str,Any]])->list[Row]:
    if not isinstance(rows,list) or not rows:
        raise DomainError('nonempty list of rows required')
    if len(rows)>10000:
        raise DomainError('row bound exceeded')
    expected=set(pair.right.group_columns)|(set([pair.right.value_column]))
    out=[]
    for d in rows:
        if type(d)!=dict or set(d)!=expected:
            raise DomainError('exact column schema required')
        for k in pair.right.group_columns:
            if d[k] is not None and type(d[k]) not in (str,int):
                raise DomainError('key must be str/int/None (no float or NaN)')
        v=d[pair.right.value_column]
        if type(v)!=int:
            raise DomainError('exact Python integers required (no null, floats, booleans)')
        if abs(v)>10**12:
            raise DomainError('amount magnitude exceeds SQL 64-bit-safe policy')
        out.append(Row(d))
    # SQLite has signed 64-bit bounds; limit total accumulation conservatively.
    if sum(abs(d.values[pair.right.value_column]) for d in out)>2**60:
        raise DomainError('aggregate magnitude outside common backend safety domain')
    return out

def selection_masks(pair:ProgramPair, rows:list[dict[str,Any]])->tuple[list[int],list[int]]:
    checked=_validated_rows(pair,rows)
    left=[1]*len(checked)
    right=[int(not (pair.right.dropna and any(row.values[key] is None for key in pair.right.group_columns))) for row in checked]
    return left,right

def _contribution(pair:ProgramPair,row:Row)->int:
    return row.values[pair.right.value_column] if pair.right.operation=='sum' else 1

def residual(pair:ProgramPair,rows:list[dict[str,Any]])->dict[str,Any]:
    data=_validated_rows(pair,rows)
    left,right=selection_masks(pair,rows)
    terms=[]
    for i,(row,l,r) in enumerate(zip(data,left,right)):
        coeff=l-r
        val=_contribution(pair,row)
        terms.append({'row':i,'left_mask':l,'right_mask':r,'coefficient':coeff,
                      'contribution':val,'residual_term':coeff*val})
    return {'difference':sum(t['residual_term'] for t in terms),
            'terms':terms,'applicable':sum(t['residual_term'] for t in terms)==0,
            'no_dropped_rows':all(t['right_mask'] for t in terms)}

def reference(pair:ProgramPair,rows:list[dict[str,Any]])->tuple[int,int]:
    data=_validated_rows(pair,rows)
    left,right=selection_masks(pair,rows)
    contrib=[_contribution(pair,row) for row in data]
    return sum(l*v for l,v in zip(left,contrib)),sum(r*v for r,v in zip(right,contrib))

def build_certificate(pair:ProgramPair)->dict[str,Any]:
    """Check one-row polynomial identity and lift by exact additive folding.

    Sympy proves the coefficient identity over symbols. The lift to N rows
    comes from sum linearity, not executable software correctness.
    """
    try:
        import sympy as sp
    except ImportError:
        return {'status':'PROOF_UNAVAILABLE','pair':pair.serial(), 'reason':'SymPy not installed'}
    l,r,v=sp.symbols('l r v',integer=True)
    aggregate_left=l*v
    aggregate_right=r*v
    identity=sp.simplify(aggregate_left-aggregate_right-(l-r)*v)==0
    negative=sp.simplify((l-r)*v).subs({l:1,r:0,v:3})==3
    cancel=sp.simplify((1-0)*sp.Integer(2)+(1-0)*sp.Integer(-2))==0
    if not all([identity,negative,cancel]):raise AssertionError('semantic algebra not established')
    return {'status':'ALGEBRAIC_IR_CERTIFIED_NOT_SUT_PROVED',
      'ir_fingerprint':pair.fingerprint(),
      'generated_guard':'SUM((left_mask_i - right_mask_i) * measure_i) == 0',
      'operation':pair.right.operation,
      'proof_obligations':{'single_row_polynomial':bool(identity),
                           'nonzero_difference_control':bool(negative),
                           'multi_row_cancellation_control':bool(cancel),
                           'inductive_lift':'additivity of exact integer sum'},
      'assumptions':['exact additive integer semantics','nonnull integer measure',
                     'groupby dropna excludes any row with a null key',
                     'no overflow/NaN/floating point/side effect','right per-group reduction preserves aggregate sum',
                     'SQL backend uses COALESCE for empty aggregate'],
      'excluded':['AVG/median/min/max','float addition','arbitrary user code or Python groupby options',
                  'proof of Pandas / SQLite implementation correctness'],
      'sympy_version':sp.__version__}

def inspect_case(pair:ProgramPair, rows:list[dict[str,Any]])->dict[str,Any]:
    diff=residual(pair,rows)
    ref_left,ref_right=reference(pair,rows)
    if ref_left-ref_right!=diff['difference']:
        raise AssertionError('independent reference and residual disagree')
    return {'input_rows':rows,'guard':diff['applicable'],'residual':diff['difference'],
            'explanation':diff['terms'],'reference':[ref_left,ref_right],
            'fallback_guard':diff['no_dropped_rows'],
            'plan_hash':pair.fingerprint()}

def counterfactual_repair(pair:ProgramPair,rows:list[dict[str,Any]])->dict[str,Any]:
    """Exact one-row L1 minimum value repair for SUM, holding all selectors fixed.

    This is a counterfactual witness about the arithmetic condition; it does
    not recommend modifying real user data to suppress software bugs.
    """
    if pair.right.operation!='sum':return {'status':'ABSTAIN_NON_SUM'}
    state=inspect_case(pair,rows)
    if state['residual']==0:return {'status':'ALREADY_APPLICABLE'}
    impacted=[term for term in state['explanation'] if term['coefficient']!=0]
    if not impacted:return {'status':'ABSTAIN_NO_EDITABLE_ROW'}
    entry=impacted[0];i=entry['row'];coef=entry['coefficient'];old=rows[i][pair.right.value_column]
    if state['residual']%coef:return {'status':'ABSTAIN_NO_INTEGER_EDIT'}
    changed=old-state['residual']//coef
    other=[dict(r) for r in rows]
    other[i][pair.right.value_column]=changed
    inspect=inspect_case(pair,other)
    if not inspect['guard']:raise AssertionError('counterfactual did not solve guard')
    return {'status':'ONE_ROW_MINIMUM_ABSOLUTE_DELTA','row':i,'column':pair.right.value_column,
            'old_value':old,'new_value':changed,'absolute_delta':abs(changed-old),
            'repaired_residual':inspect['residual'],'provenance':'exact additive single-row inverse'}
