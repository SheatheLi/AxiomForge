"""Counterexample-guided inference of an admissibility guard.

Guarantee is deliberately finite: all inputs in the declared bounded domain
are independently re-evaluated, and the chosen guard never asserts a false
relation within that domain. Beyond the domain, it is not a theorem.

Optimization criterion: maximize fraction of the *input feature population*
where guard admits, subject to no previously observed false positives. A
penalty on literal count breaks ties. No bug/fixed labels are needed.
"""
from __future__ import annotations
from dataclasses import dataclass,asdict
from itertools import combinations,product
from typing import Any
from axiomforge.targets.partition import (bounded_cases, feature_values,independent_model,actual,FEATURES,PartitionInput)

Literal=tuple[str,bool]
Term=tuple[Literal,...]
Guard=tuple[Term,...]

def _term_matches(term:Term, features:dict[str,bool]) -> bool:
    return all(features[name]==val for name,val in term)

def matches(guard: Guard,features:dict[str,bool]) -> bool:
    return any(_term_matches(term,features) for term in guard)

def printable(guard: Guard) -> str:
    if not guard:return "FALSE"
    if any(not term for term in guard):return "TRUE"
    return " OR ".join("("+" AND ".join(("" if val else "NOT ")+name for name,val in term)+")" for term in guard)

def search_grammar(max_clause_literals:int=2,max_terms:int=2, feature_names:tuple[str,...]=FEATURES) -> list[Guard]:
    if max_clause_literals not in (1,2) or max_terms not in (1,2):
        raise ValueError("bounded search grammar supported: at most 2 literals and 2 disjunctive terms")
    clauses: list[Term]=[()]
    lits=[(name,val) for name in feature_names for val in (False,True)]
    for n in range(1,max_clause_literals+1):
        for group in combinations(lits,n):
            if len({lit[0] for lit in group})==n:
                clauses.append(group)
    out=[(term,) for term in clauses]
    if max_terms==2:
        out.extend((a,b) for a,b in combinations(clauses[1:],2))
    return out

@dataclass
class GuardDiscovery:
    formula: str
    terms: list[list[list[Any]]]
    domain: dict[str, Any]
    total_domain_cases: int
    accepted: int
    rejected: int
    false_positives: int
    false_negatives: int
    semantic_reference_disagreements: int
    cegis_rounds: int
    counterexamples: list[dict[str, Any]]
    oracle_evaluations: int
    candidate_formulas_considered: int
    kind: str

    def serial(self)->dict[str,Any]:return asdict(self)

def derive(max_rows:int=3, max_clause_literals:int=2,max_terms:int=2, *, include_sum_feature:bool=True)->GuardDiscovery:
    domain=list(bounded_cases(max_rows))
    names=FEATURES if include_sum_feature else tuple(x for x in FEATURES if x!="zero_missing_sum")
    vectors=[feature_values(p) for p in domain]
    features=[tuple(int(x[k]) for k in names) for x in vectors]
    # Candidate grammar is an actual Boolean-expression search; not a hardcoded
    # answer disguised as an inference algorithm.
    grammar=search_grammar(max_clause_literals,max_terms,names)
    unique=sorted(set(features))
    counts={x:features.count(x) for x in unique}
    atom={x:dict(zip(names,map(bool,x))) for x in unique}
    positives: list[int]=[]
    negatives: list[int]=[]
    observations:dict[int,tuple[int,int,bool]]={}
    checked_reference:dict[int,bool]={}
    ref_disagree=0
    counterexamples=[]

    def observe(i:int)->bool:
        nonlocal ref_disagree
        if i not in observations:
            real=actual(domain[i]); independent=independent_model(domain[i]);observations[i]=real
            if real!=independent:ref_disagree+=1
            checked_reference[i]=independent[2]
        return observations[i][2]

    # Initial feedback comprises a tiny number of deliberately documented
    # observations; rest is discovered by a falsifier, not by labeled bug data.
    seed_indices=[0,1,2,3]
    for i in seed_indices:
        if observe(i):positives.append(i)
        else:negatives.append(i)

    # Optimize admission coverage by feature distribution, not ground-truth
    # relation labels of unqueried inputs. Constant number of feature vectors
    # makes exact candidate enumeration tractable.
    candidates=[]
    for guard in grammar:
        covered={v for v in unique if matches(guard,atom[v])}
        bitset={tuple(int(v) for v in features[i]) for i in negatives}
        if covered.intersection(bitset):continue
        if any(features[i] not in covered for i in positives):continue
        cov=sum(counts[v] for v in covered)
        complexity=sum(len(t) for t in guard)
        candidates.append((-cov,complexity,len(guard),printable(guard),guard,covered))
    if not candidates:raise AssertionError("no candidate guard from initial samples")

    # Search begins with broadest consistent guard. Recompute eligibility after
    # every actual counterexample, never claiming a speculative formula proved.
    rounds=0
    while True:
        rounds+=1
        candidates.sort(key=lambda x:x[:4])
        selected=None
        for info in candidates:
            covered=info[-1]
            if any(features[i] in covered for i in negatives):continue
            if any(features[i] not in covered for i in positives):continue
            selected=info;break
        if selected is None:raise RuntimeError("candidate language cannot separate evidence")
        selected_guard=selected[-2]
        new_counterexample=None
        for i in range(len(domain)):
            if features[i] not in selected[-1]:continue
            if not observe(i):
                if i not in negatives:
                    new_counterexample=i
                    negatives.append(i)
                    counterexamples.append({"input":domain[i].serial(),"features":vectors[i],
                        "relation_observed":observations[i][2],"independent_reference":checked_reference[i]})
                break
        if new_counterexample is None:break
        if rounds>len(unique)+2:raise RuntimeError("no finite-domain progress")

    # Final complete domain certification: NO inference based on an incomplete
    # sample, including rejected inputs. Also check independent reference on all.
    for i in range(len(domain)):observe(i)
    selected_guard=selected[-2]
    false_positive=sum(1 for i in range(len(domain)) if matches(selected_guard,vectors[i]) and not observations[i][2])
    false_negative=sum(1 for i in range(len(domain)) if not matches(selected_guard,vectors[i]) and observations[i][2])
    if false_positive or ref_disagree:raise AssertionError("bounded certification or independent reference disagreement")
    accepted=sum(matches(selected_guard,v) for v in vectors)
    return GuardDiscovery(formula=printable(selected_guard),
      terms=[[[name,val] for name,val in term] for term in selected_guard],
      domain={"keys":list(map(lambda x: "null" if x is None else x,("A","B",None))),
              "values":[-1,0,1],"max_rows":max_rows,"dropna":[False,True],
              "feature_grammar":list(names),"max_clause_literals":max_clause_literals,"max_terms":max_terms},
      total_domain_cases=len(domain),accepted=accepted,rejected=len(domain)-accepted,
      false_positives=false_positive,false_negatives=false_negative,
      semantic_reference_disagreements=ref_disagree,
      cegis_rounds=rounds,counterexamples=counterexamples,
      oracle_evaluations=len(observations),candidate_formulas_considered=len(grammar),
      kind="EXHAUSTIVELY_CHECKED_FINITE_DOMAIN_NOT_UNIVERSAL_PROOF")

def assess_bounded(result:dict[str,Any],p:PartitionInput)->dict[str,Any]:
    """Do not promote bounded evidence to unbounded inputs. Domain checks
    precede Boolean guard evaluation; a hypothesis cannot silently become proof.
    """
    d=result["domain"]
    within=(1<=len(p.keys)<=d["max_rows"] and all(k in ("A","B",None) for k in p.keys)
            and all(type(v)==int and v in (-1,0,1) for v in p.values)
            and len(p.keys)==len(p.values) and type(p.dropna)==bool)
    if not within:
        return {"status":"ABSTAIN_OUTSIDE_CERTIFIED_DOMAIN","domain_admitted":False}
    guard=guard_from_serial(result)
    if not matches(guard,feature_values(p)):
        return {"status":"ABSTAIN_GUARD_NOT_MET","domain_admitted":True}
    observed=actual(p)
    independent=independent_model(p)
    if observed!=independent:
        return {"status":"INDEPENDENT_REFERENCE_DISAGREEMENT","domain_admitted":True,
                "observed":observed,"expected":independent}
    return {"status":"ADMITTED_CONSISTENT","domain_admitted":True,
            "observed":observed,"expected":independent}

def guard_from_serial(result: dict[str,Any])->Guard:
    return tuple(tuple((str(k),bool(v)) for k,v in term) for term in result["terms"])
