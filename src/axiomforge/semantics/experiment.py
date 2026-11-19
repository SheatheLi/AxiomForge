"""Frozen-contract, unseen-size holdout experiments and tamper-resistant replay.

Both baseline and synthesized rule receive the SAME input observations.
Declared trial cost includes every measured backend invocation. SHA256 is an
integrity receipt, not authenticity; independent library replay is required.
"""
from __future__ import annotations
import hashlib, json, random, pathlib, platform, datetime
from dataclasses import dataclass
from typing import Any
from .compiler import ProgramPair,compile_pandas_pair
from .algebra import inspect_case,build_certificate,DomainError,counterfactual_repair
from .backends import BACKENDS
from .join_provenance import JOIN_PROGRAMS,compile_join,prove_join,inspect_join,execute_join

PROGRAMS={
 'sum_single':('df["amount"].sum()', 'df.groupby("group", dropna=True)["amount"].sum().sum()'),
 'sum_multi':('df["amount"].sum()', 'df.groupby(["group", "region"], dropna=True)["amount"].sum().sum()'),
 'count_multi':('df["amount"].count()', 'df.groupby(["group", "region"], dropna=True)["amount"].count().sum()'),
 'sum_keep_null':('df["amount"].sum()', 'df.groupby("group", dropna=False)["amount"].sum().sum()'),
}

def _canonical(obj:Any)->bytes:
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')

def _env()->dict[str,str]:
    import pandas,sqlalchemy,sqlite3,sympy
    return {'python':platform.python_version(),'pandas':pandas.__version__,
            'sqlalchemy':sqlalchemy.__version__,'sqlite':sqlite3.sqlite_version,
            'sympy':sympy.__version__}

def _trial_rows(pair:ProgramPair,seed:int,lo:int=4,hi:int=9)->list[dict[str,Any]]:
    """Stratified *unseen-size* holdout. Four predeclared strata, not labels.

    Stratum 2 contains two excluded rows with cancelling integer measures,
    which is where provenance algebra can prove a valid relation even though
    the naive no-null condition abstains. Stratification is disclosed to avoid
    pretending this is representative random production traffic.
    """
    rng=random.Random(seed)
    size=rng.randrange(lo,hi+1)
    keys=pair.right.group_columns
    strata=seed%4
    rows=[]
    for _ in range(size):
        row={k:rng.choice(('A','B','C',None)) if strata==0 else rng.choice(('A','B','C')) for k in keys}
        row[pair.right.value_column]=rng.choice((-5,-3,-2,-1,0,1,2,3,5))
        rows.append(row)
    if strata==2:
        rows[0][keys[0]]=None
        rows[1][keys[0]]=None
        rows[0][pair.right.value_column]=3
        rows[1][pair.right.value_column]=-3
    elif strata==3:
        rows[0][keys[0]]=None
        rows[0][pair.right.value_column]=3
    return rows

def _join_rows(p,seed:int)->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    rng=random.Random(seed)
    strata=seed%4
    facts=[{p.key:rng.choice(('B','C')),'amount':rng.choice((-4,-2,1,2,4))} for _ in range(rng.randrange(4,9))]
    dims=[{p.key:k,'label':k+'_one'} for k in ('A','B','C')]
    if strata==0:
        facts[0][p.key]='A';facts[0]['amount']=3
    elif strata==1:
        # Overrepresented A has zero total amount: SUM is safe even though dimension is nonunique.
        facts[0][p.key]='A';facts[1][p.key]='A'
        facts[0]['amount']=3;facts[1]['amount']=-3
        dims.append({p.key:'A','label':'A_duplicate'})
    elif strata==2:
        facts[0][p.key]='A';facts[0]['amount']=3
        dims.append({p.key:'A','label':'A_duplicate'})
    else:
        # Dimension contains a duplicate unmatched key; no fact row is duplicated.
        dims.extend(({p.key:'Z','label':'unused_one'},{p.key:'Z','label':'unused_two'}))
    return facts,dims

def evaluate_join(p,facts:list[dict[str,Any]],dims:list[dict[str,Any]],backend:str)->dict[str,Any]:
    if backend not in BACKENDS:raise ValueError('backend not allowlisted')
    state=inspect_join(p,facts,dims)
    actual=execute_join(p,facts,dims,backend)
    expected=tuple(state['reference'])
    if actual!=expected:status='SUT_MODEL_MISMATCH_REQUIRES_INDEPENDENT_REVIEW'
    elif not state['guard']:status='ABSTAIN_CONDITION_NOT_MET'
    elif actual[0]!=actual[1]:status='SUT_RELATIONAL_VIOLATION'
    else:status='ADMITTED_CONSISTENT'
    return {'backend':backend,'rows':facts,'dimension_rows':dims,'actual':list(actual),
        'independent_reference':list(expected),'residual':state['residual'],
        'synthesized_guard':state['guard'],'baseline_guard':state['simple_unique_key_guard'],
        'status':status,'plan_hash':p.fingerprint(),'trace':state['trace']}

def evaluate(pair:ProgramPair,rows:list[dict[str,Any]],backend:str)->dict[str,Any]:
    if backend not in BACKENDS:raise ValueError('backend not allowlisted')
    evidence=inspect_case(pair,rows)
    actual=BACKENDS[backend](pair,rows)
    expected=tuple(evidence['reference'])
    model_matches=(actual==expected)
    if not model_matches:
        status='SUT_MODEL_MISMATCH_REQUIRES_INDEPENDENT_REVIEW'
    elif not evidence['guard']:
        status='ABSTAIN_CONDITION_NOT_MET'
    elif actual[0]!=actual[1]:
        status='SUT_RELATIONAL_VIOLATION'
    else:
        status='ADMITTED_CONSISTENT'
    return {'backend':backend,'rows':rows,'actual':list(actual),'independent_reference':list(expected),
      'residual':evidence['residual'],'synthesized_guard':evidence['guard'],
      'baseline_guard':evidence['fallback_guard'],'status':status,
      'plan_hash':pair.fingerprint(),'trace':evidence['explanation']}

def _summary(trials:list[dict[str,Any]])->dict[str,Any]:
    admissions=sum(x['synthesized_guard'] for x in trials)
    baseline=sum(x['baseline_guard'] for x in trials)
    invalid=sum(not x['synthesized_guard'] for x in trials)
    wrong=sum(x['synthesized_guard'] and x['actual'][0]!=x['actual'][1] for x in trials)
    model_mismatch=sum(x['actual']!=x['independent_reference'] for x in trials)
    always_wrong=sum(x['actual'][0]!=x['actual'][1] for x in trials)
    return {'execution_count':len(trials),'synthesized_admitted':admissions,
            'simple_baseline_admitted':baseline,'additional_safe_admissions':admissions-baseline,
            'abstained':invalid,'incorrect_synthesized_admissions':wrong,
            'independent_model_disagreements':model_mismatch,
            'unguarded_false_alarms':always_wrong}

def run_experiment(seeds:int=48)->dict[str,Any]:
    if not 1<=seeds<=500:raise ValueError('seeds out of range')
    data=[]
    for label,src in PROGRAMS.items():
        pair=compile_pandas_pair(*src)
        proof=build_certificate(pair)
        if proof['status']!='ALGEBRAIC_IR_CERTIFIED_NOT_SUT_PROVED':raise RuntimeError('proof not available')
        # same rows across all backends; disjoint holdout sizes n>=4, excluded
        # from Gate-0 max_rows<=3 feature search.
        trials=[]
        for seed in range(seeds):
            rows=_trial_rows(pair,seed+41017)
            for backend in BACKENDS:
                trials.append(evaluate(pair,rows,backend))
        entry={'case':label,'pair':pair.serial(),'proof':proof,'summary':_summary(trials),'trials':trials}
        data.append(entry)
    for label,src in JOIN_PROGRAMS.items():
        pair=compile_join(*src)
        proof=prove_join(pair)
        if proof['status']!='ALGEBRAIC_JOIN_IR_CERTIFIED_NOT_SUT_PROVED':raise RuntimeError('join proof not available')
        trials=[]
        for seed in range(seeds):
            facts,dims=_join_rows(pair,seed+41017)
            for backend in BACKENDS:
                trials.append(evaluate_join(pair,facts,dims,backend))
        data.append({'case':label,'pair':pair.serial(),'proof':proof,
                     'summary':_summary(trials),'trials':trials})
    return {'schema':'axiomforge.semantic_provenance.v1','timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'environment':_env(),'holdout_description':'4-9 rows for filter, 4-8 fact rows for join; stratified holdout, exact integer; no claim of natural data distribution',
      'method':'AST-safe relational plan -> additive row provenance -> algebraic guard',
      'search_claim':'none; this measures sound admission, not unknown bug discovery',
      'results':data}

def save(path:str|pathlib.Path,payload:dict[str,Any])->str:
    p=pathlib.Path(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    record={'envelope_schema':'axiomforge.semantic_evidence.v1','payload':payload,
            'payload_sha256':hashlib.sha256(_canonical(payload)).hexdigest()}
    p.write_text(json.dumps(record,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
    return record['payload_sha256']

def verify(path:str|pathlib.Path,rerun:bool=True)->dict[str,Any]:
    record=json.loads(pathlib.Path(path).read_text(encoding='utf8'))
    if set(record)!={'envelope_schema','payload','payload_sha256'} or record['envelope_schema']!='axiomforge.semantic_evidence.v1':
        raise ValueError('invalid semantic evidence envelope')
    payload=record['payload']
    if hashlib.sha256(_canonical(payload)).hexdigest()!=record['payload_sha256']:
        raise ValueError('semantic evidence digest mismatch')
    if payload['schema']!='axiomforge.semantic_provenance.v1' or len(payload['results'])!=len(PROGRAMS)+len(JOIN_PROGRAMS):
        raise ValueError('invalid semantic run metadata')
    if rerun and payload['environment']!=_env():
        raise ValueError('software version drift blocks comparable replay')
    checks=0;wrong=0
    suite=list(PROGRAMS.items())+list(JOIN_PROGRAMS.items())
    for entry,(label,sources) in zip(payload['results'],suite):
        is_join=label in JOIN_PROGRAMS
        pair=compile_join(*sources) if is_join else compile_pandas_pair(*sources)
        proof=prove_join(pair) if is_join else build_certificate(pair)
        if entry['case']!=label or entry['pair']!=pair.serial() or entry['proof']!=proof:
            raise ValueError('unsupported source or certificate tampering')
        trials=entry['trials']
        if not trials or len(trials)%len(BACKENDS):raise ValueError('trial coverage incomplete')
        if entry['summary']!=_summary(trials):raise ValueError('published totals inconsistent')
        for i,row in enumerate(trials):
            seed=i//len(BACKENDS)
            backend=list(BACKENDS)[i%len(BACKENDS)]
            if is_join:
                expected_rows,dimension_rows=_join_rows(pair,seed+41017)
                valid_row=(row['backend']==backend and row['rows']==expected_rows and row['dimension_rows']==dimension_rows)
            else:
                expected_rows=_trial_rows(pair,seed+41017)
                valid_row=(row['backend']==backend and row['rows']==expected_rows)
            if not valid_row:raise ValueError('trial seed / backend / input provenance mismatch')
            if rerun:
                actual=evaluate_join(pair,expected_rows,dimension_rows,backend) if is_join else evaluate(pair,expected_rows,backend)
                if row!=actual:raise ValueError('real-library semantic evidence mismatch')
            checks+=1
            wrong+=(not row['synthesized_guard'] and row['actual'][0]!=row['actual'][1])
    return {'status':'THIRD_PARTY_REEXECUTED' if rerun else 'STRUCTURE_AND_DIGEST_ONLY',
            'verified_trials':checks,'unguarded_wrong_relations':wrong,
            'warnings':['SHA256 alone does not prove authenticity','limited algebraic domain, no unknown bug discovery']}
