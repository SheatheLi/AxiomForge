"""Input-only contract obligation checker, separate from the SUT adapter.

The adapter cannot grant itself authority by returning checks={...: True}.
Input equivalence is evaluated from immutable submitted inputs and controls;
the checker does not read 'holds' or 'left/right' from the target.
"""
from __future__ import annotations
from typing import Any

def independent_checks(target:str,payload:dict[str,Any],control:dict[str,Any]|None,
                       observed_state:dict[str,Any])->dict[str,bool]:
    control=control or {}
    if target=='group-sort':
        keys=payload.get('keys',[]); vals=payload.get('values',[])
        requested_sort=control.get('sort',payload.get('sort',False))
        return {'inputs_equivalent':isinstance(keys,list) and isinstance(vals,list) and len(keys)==len(vals),
                'domain_valid': bool(keys) and all(type(k)==int for k in keys) and requested_sort is False,
                'semantic_change':False}
    if target=='cow-alias':
        keys=payload.get('keys',[]);vals=payload.get('values',[])
        semantic_change=control.get('semantic_change',False)
        valid=(isinstance(keys,list) and isinstance(vals,list) and len(keys)==len(vals) and len(keys)>=2
               and payload.get('mutated_key') not in keys)
        return {'inputs_equivalent':valid and not semantic_change,
                'domain_valid':valid,'semantic_change':bool(semantic_change)}
    if target=='sa-lifecycle':
        stage=control.get('stage',payload.get('stage'))
        attempts=payload.get('attempts')
        valid=(type(attempts)==int and 1<=attempts<=12 and
               stage in ('connect','first_connect') and observed_state.get('errors')==attempts)
        return {'inputs_equivalent':True,'domain_valid':bool(valid),'semantic_change':False}
    return {'inputs_equivalent':False,'domain_valid':False,'semantic_change':True}
