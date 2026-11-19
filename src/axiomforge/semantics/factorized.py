"""Factorized provenance for a bounded STAR-shaped multi-join pipeline.

A fact row is joined independently through multiple dimension tables using
its original key. The contribution multiplicity is a product of per-stage
survivor counts. The transfer model is O(facts + dimension rows + stages*keys)
instead of materializing the Cartesian explosion. If materialization is safe,
we cross-check by running real Pandas and SQLite joins; otherwise we *only*
assert a model-derived planning result, not an observed backend outcome.
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .pipeline_audit import AuditError, _load


@dataclass(frozen=True)
class Stage:
    name: str
    segment: str
    dropna: bool
    dims: list[dict[str, Any]]


def _validate(stages: list[Stage], facts: list[dict], key: str, measure: str):
    if not 1 <= len(stages) <= 8:
        raise AuditError('1..8 independent join stages supported')
    if not facts or len(facts) > 5000:
        raise AuditError('1..5000 facts supported')
    if not all(s.name.isidentifier() and s.segment.isidentifier() and type(s.dropna) is bool for s in stages):
        raise AuditError('stage and group identifiers must be Python identifiers')
    if len({s.name for s in stages}) != len(stages):
        raise AuditError('duplicate stage identity')
    for f in facts:
        if type(f) is not dict or set(f) != {key, measure} or type(f[key]) is not str or not f[key] or type(f[measure]) is not int:
            raise AuditError('fact schema: unique key text, exact integer measure')
    if sum(abs(f[measure]) for f in facts) > 2**48:
        raise AuditError('fact amount bound exceeded')
    for stage in stages:
        if len(stage.dims) > 5000:
            raise AuditError('dimension stage row budget exceeded')
        for d in stage.dims:
            if type(d) is not dict or set(d) != {key, stage.segment} or type(d[key]) is not str or not d[key] or (d[stage.segment] is not None and type(d[stage.segment]) is not str):
                raise AuditError('dimension stage schema invalid')


def factorized(facts: list[dict], stages: list[Stage], *, key: str='sku',
               measure: str='cost', operation: str='sum', materialize_limit: int=30000) -> dict[str, Any]:
    """Compute stage-wise provenance and validate against SUT when affordable."""
    if operation not in ('sum','count') or materialize_limit < 1:
        raise AuditError('invalid aggregation or execution limit')
    _validate(stages,facts,key,measure)
    by_stage = []
    for stage in stages:
        per_key = collections.defaultdict(list)
        for d in stage.dims:
            per_key[d[key]].append(d[stage.segment])
        by_stage.append(per_key)
    total_before = 0
    total_after = 0
    post_rows = 0
    raw_cartesian_upper_bound = 0
    abnormal = 0
    terms = []
    total_gross_difference = 0
    for idx, fact in enumerate(facts):
        value = fact[measure] if operation == 'sum' else 1
        per_stage = []
        multiplicity = 1
        unfiltered_multiplicity = 1
        for s, mapping in zip(stages,by_stage):
            matches = mapping.get(fact[key],[])
            # When LEFT JOIN finds no match, one NULL-extended row survives
            # unless the corresponding group column is filtered out.
            survivors = sum(v is not None for v in matches) if s.dropna else max(1,len(matches))
            per_stage.append({'stage':s.name,'matches':len(matches), 'survivors':survivors})
            multiplicity *= survivors
            unfiltered_multiplicity *= max(1,len(matches))
        contribution_difference=(1-multiplicity)*value
        total_before += value
        total_after += value*multiplicity
        post_rows += multiplicity
        raw_cartesian_upper_bound += unfiltered_multiplicity
        abnormal += multiplicity!=1
        total_gross_difference += abs(contribution_difference)
        terms.append({'fact_row':idx,'stage_factors':per_stage,'final_multiplicity':multiplicity,
                      'contribution':value,'difference':contribution_difference})
    if abs(total_before) > 2**60 or abs(total_after) > 2**60:
        raise AuditError('factorized aggregate outside supported safe integer bound')
    if total_before != total_after:
        verdict='GLOBAL_VALUE_DRIFT'
    elif abnormal:
        verdict='HIDDEN_AT_GLOBAL_TOTAL'
    else:
        verdict='EXACTLY_ONCE_PRESERVED'
    model = {'mode':'FACTORIZED_MODEL_ONLY','original_fact_rows':len(facts),'stage_count':len(stages),
             'would_materialize_rows':post_rows,'unfiltered_join_rows_upper_bound':raw_cartesian_upper_bound,'model_before':total_before,'model_after':total_after,
             'model_residual':total_before-total_after,'abnormal_fact_rows':abnormal,
             'gross_absolute_contribution_drift':total_gross_difference,'verdict':verdict,
             'stage_summary':[{'stage':stage.name,'dimension_rows':len(stage.dims),
                               'dropna':stage.dropna,'group_column':stage.segment} for stage in stages],
             'important_rows':sorted((r for r in terms if r['final_multiplicity']!=1),key=lambda r:-abs(r['difference']))[:15],
             'safety_notes':['model is exact within the restricted independent-key LEFT JOIN semantics',
                             'it is not proof that external query implementations satisfy this model',
                             'record-level once-per-fact must be explicitly required by business contract']}
    if raw_cartesian_upper_bound > materialize_limit or post_rows > materialize_limit:
        model['validation']={'status':'NOT_MATERIALIZED_BUDGET','limit':materialize_limit,
                             'reason':'result or intermediate join cardinality makes independent engine validation too expensive'}
        return model
    # Independent real executions, not the factorized transfer algorithm.
    actual = {name:backend_expand(facts,stages,key,measure,operation,name) for name in ('pandas','sqlite')}
    expected = (total_after,post_rows)
    if any(v != expected for v in actual.values()):
        raise AuditError(f'factorized model / actual executor disagree: model={expected}, observed={actual}')
    model['mode']='MODEL_AND_REAL_BACKENDS'
    model['validation']={'status':'PANDAS_AND_SQLITE_MATERIALIZED_AGREEMENT','executions':actual,
                         'limit':materialize_limit}
    return model


def backend_expand(facts,stages,key,measure,operation,backend):
    if backend=='pandas':
        import pandas as pd
        df=pd.DataFrame(facts)
        for i, s in enumerate(stages):
            right=pd.DataFrame(s.dims,columns=[key,s.segment]).rename(columns={s.segment:f'_stage{i}_group'})
            df=df.merge(right,on=key,how='left')
            if s.dropna:
                df=df[df[f'_stage{i}_group'].notna()]
        aggregate=len(df) if operation=='count' else int(df[measure].sum())
        return aggregate,len(df)
    if backend=='sqlite':
        import sqlite3
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE f (k TEXT NOT NULL, v INTEGER NOT NULL)')
            conn.executemany('INSERT INTO f VALUES (?,?)',[(x[key],x[measure]) for x in facts])
            joins=[];guards=[]
            for i,s in enumerate(stages):
                table=f'd{i}'
                conn.execute(f'CREATE TABLE {table} (k TEXT NOT NULL, seg TEXT)')
                conn.executemany(f'INSERT INTO {table} VALUES (?,?)',[(x[key],x[s.segment]) for x in s.dims])
                joins.append(f'LEFT JOIN {table} ON f.k={table}.k')
                if s.dropna:guards.append(f'{table}.seg IS NOT NULL')
            sql='SELECT COUNT(*), COALESCE(SUM(f.v),0) FROM f ' + ' '.join(joins)
            if guards:sql+=' WHERE '+' AND '.join(guards)
            num,amount=conn.execute(sql).fetchone()
            return (int(num) if operation=='count' else int(amount)),int(num)
        finally:conn.close()
    raise AuditError('unregistered factorized backend')


def load_manifest(path: str | Path) -> tuple[list[dict], list[Stage],str,str,str]:
    file=Path(path)
    raw=json.loads(file.read_text(encoding='utf-8'))
    if set(raw)!={'facts','key','measure','operation','stages'} or not isinstance(raw['stages'],list):
        raise AuditError('manifest requires facts/key/measure/operation/stages')
    key=raw['key'];measure=raw['measure']
    if not all(isinstance(x,str) and x.isidentifier() for x in (key,measure)):
        raise AuditError('invalid manifest identifiers')
    def csvpath(relative):
        if type(relative) is not str or not relative or '\\x00' in relative:
            raise AuditError('invalid input path')
        # Explicit file paths are local and read-only. No network URL execution.
        return file.parent/relative
    facts=_load(csvpath(raw['facts']),columns=(key,measure),null_second=False,int_second=True,max_rows=5000)
    stages=[]
    for n,entry in enumerate(raw['stages']):
        if set(entry)!={'name','path','segment','dropna'} or type(entry['dropna']) is not bool:
            raise AuditError('stage manifest must have name/path/segment/dropna')
        dimensions=_load(csvpath(entry['path']),columns=(key,entry['segment']),null_second=True,int_second=False,max_rows=5000)
        stages.append(Stage(entry['name'],entry['segment'],entry['dropna'],dimensions))
    return facts,stages,key,measure,raw['operation']


def evaluate_manifest(path:str|Path,*,limit:int=30000):
    facts,stages,key,measure,operation=load_manifest(path)
    return factorized(facts,stages,key=key,measure=measure,operation=operation,materialize_limit=limit)
