"""Compositional row-multiplicity semantics: LEFT JOIN -> GROUPBY -> SUM.

This is not a full SQL analyzer. It demonstrates operator composition: per
fact row contributions are multiplied by a *derived* number of surviving
joined rows, which may be zero, one, or many. Drop and fanout are combined in
one coefficient rather than treated as separate hardcoded failure kinds.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import ast,hashlib,json,collections
from .compiler import CompileError,_source
from .algebra import DomainError

@dataclass(frozen=True)
class CompositePlan:
    fact_frame:str
    dimension_frame:str
    join_key:str
    dimension_group_key:str
    value_column:str
    aggregate:str
    dropna:bool
    left_source:str
    right_source:str

    def serial(self):return self.__dict__.copy()
    def fingerprint(self):return hashlib.sha256(json.dumps(self.serial(),sort_keys=True).encode()).hexdigest()

def _zero_call(x,name):
    return isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr==name and not x.args and not x.keywords

def compile_composite(left:str,right:str)->CompositePlan:
    l=_source(left,'left')
    try:outer=ast.parse(right,mode='eval').body
    except SyntaxError as exc:raise CompileError('invalid composite expression') from exc
    if not _zero_call(outer,'sum') or not _zero_call(outer.func.value,l.operation):
        raise CompileError('right must be additive grouped aggregate, then sum')
    inner=outer.func.value
    if not isinstance(inner.func.value,ast.Subscript):raise CompileError('grouped measure selection required')
    selection=inner.func.value
    if not isinstance(selection.slice,ast.Constant) or selection.slice.value!=l.value_column:
        raise CompileError('measure mismatch')
    group=selection.value
    if not isinstance(group,ast.Call) or not isinstance(group.func,ast.Attribute) or group.func.attr!='groupby':
        raise CompileError('groupby operation required')
    if len(group.args)!=1 or not isinstance(group.args[0],ast.Constant) or type(group.args[0].value)!=str:
        raise CompileError('only one dimension group key is supported')
    if len(group.keywords)!=1 or group.keywords[0].arg!='dropna' or not isinstance(group.keywords[0].value,ast.Constant) or type(group.keywords[0].value.value)!=bool:
        raise CompileError('explicit static dropna required')
    grouped=group.func.value
    if not isinstance(grouped,ast.Call) or not isinstance(grouped.func,ast.Attribute) or grouped.func.attr!='merge':
        raise CompileError('grouping must be over a left merge')
    if not isinstance(grouped.func.value,ast.Name) or grouped.func.value.id!=l.data_frame:
        raise CompileError('fact dataframe mismatch')
    if len(grouped.args)!=1 or not isinstance(grouped.args[0],ast.Name):raise CompileError('dimension frame must be named')
    kwargs={}
    for k in grouped.keywords:
        if k.arg not in ('on','how') or k.arg in kwargs or not isinstance(k.value,ast.Constant):
            raise CompileError('unsupported dynamic join semantics')
        kwargs[k.arg]=k.value.value
    if set(kwargs)!={'on','how'} or kwargs['how']!='left' or type(kwargs['on'])!=str:
        raise CompileError('only a static left join supported')
    key=kwargs['on'];region=group.args[0].value
    if len({key,region,l.value_column})!=3:
        raise CompileError('keys and measure must be distinct')
    return CompositePlan(l.data_frame,grouped.args[0].id,key,region,l.value_column,
                         l.operation,group.keywords[0].value.value,left,right)

def _validate(p:CompositePlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]]):
    if not isinstance(facts,list) or not facts or not isinstance(dims,list):raise DomainError('invalid relation inputs')
    if len(facts)>10000 or len(dims)>10000:raise DomainError('exceeds bound')
    for f in facts:
        if type(f)!=dict or set(f)!={p.join_key,p.value_column}:
            raise DomainError('fact schema mismatch')
        if type(f[p.join_key])!=str or type(f[p.value_column])!=int or abs(f[p.value_column])>10**10:
            raise DomainError('fact join keys must be nonnull strings; measure exact int')
    for d in dims:
        if type(d)!=dict or set(d)!={p.join_key,p.dimension_group_key}:
            raise DomainError('dimension schema mismatch')
        if type(d[p.join_key])!=str or not (d[p.dimension_group_key] is None or type(d[p.dimension_group_key])==str):
            raise DomainError('non-null string join keys; group key string/None')
    if len(facts)*max(1,len(dims))*max(1,max(abs(f[p.value_column]) for f in facts))>2**60:
        raise DomainError('possible integer overflow')

def inspect_composite(p:CompositePlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]])->dict[str,Any]:
    _validate(p,facts,dims)
    by_key=collections.defaultdict(list)
    for d in dims:by_key[d[p.join_key]].append(d)
    pieces=[]
    for i,f in enumerate(facts):
        matches=by_key.get(f[p.join_key]) or [{p.dimension_group_key:None}]
        survivors=sum(int((not p.dropna) or (d[p.dimension_group_key] is not None)) for d in matches)
        v=f[p.value_column] if p.aggregate=='sum' else 1
        pieces.append({'row':i,'fact_key':f[p.join_key],
                       'joined_rows':len(matches),'retained_after_groupby':survivors,
                       'contribution':v,'coefficient':1-survivors,
                       'delta':(1-survivors)*v})
    left=sum(x['contribution'] for x in pieces)
    right=sum(x['contribution']*x['retained_after_groupby'] for x in pieces)
    difference=sum(x['delta'] for x in pieces)
    if left-right!=difference:raise AssertionError('wrong composite transfer')
    return {'guard':difference==0,'residual':difference,'reference':[left,right],
            'trace':pieces,'baseline_guard':all(x['retained_after_groupby']==1 for x in pieces),
            'plan_hash':p.fingerprint()}

def prove_composite(p:CompositePlan)->dict[str,Any]:
    try:import sympy as sp
    except ImportError:return {'status':'PROOF_UNAVAILABLE'}
    n,v=sp.symbols('n v',integer=True)
    holds=sp.simplify(v-n*v-(1-n)*v)==0
    # Composition maps |dimension matches| to number of rows after dropna.
    cases=[(0,1,False),(1,0,True),(2,1,True),(2,2,False)]
    checks=all(sum(1 for _ in range(total) if not(drop and _>=keep))==keep for total,keep,drop in cases)
    if not holds or not checks:raise AssertionError('composition transfer proof failed')
    return {'status':'COMPOSITIONAL_IR_CERTIFIED_NOT_SUT_PROVED','fingerprint':p.fingerprint(),
            'derived':'Σ_i (1 - retained_join_and_group_rows_i) * measure_i = 0',
            'proof_obligations':{'per_row_additive_identity':bool(holds),'selection_count_controls':bool(checks),
                                 'lift':'additive fold for arbitrary finite non-overflow rows'},
            'limitations':['operator subset only','nonnull join keys','no float/nonlinear ops',
                           'does not prove external libraries implement semantics']}

def execute_composite(p:CompositePlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]],backend:str)->tuple[int,int]:
    _validate(p,facts,dims)
    if backend=='pandas':
        import pandas as pd
        f=pd.DataFrame(facts);d=pd.DataFrame(dims,columns=[p.join_key,p.dimension_group_key])
        left=(f[p.value_column].sum() if p.aggregate=='sum' else f[p.value_column].count())
        merged=f.merge(d,on=p.join_key,how='left')
        grouped=merged.groupby(p.dimension_group_key,dropna=p.dropna)[p.value_column]
        right=(grouped.sum().sum() if p.aggregate=='sum' else grouped.count().sum())
        return int(left),int(right)
    if backend=='sqlite':
        import sqlite3
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE facts (join_key TEXT, amount INTEGER)')
            c.execute('CREATE TABLE dims (join_key TEXT, segment TEXT)')
            c.executemany('INSERT INTO facts VALUES(?,?)',[(f[p.join_key],f[p.value_column]) for f in facts])
            c.executemany('INSERT INTO dims VALUES(?,?)',[(d[p.join_key],d[p.dimension_group_key]) for d in dims])
            agg=('COALESCE(SUM(f.amount),0)' if p.aggregate=='sum' else 'COUNT(f.amount)')
            left=c.execute(f'SELECT {agg} FROM facts f').fetchone()[0]
            where='WHERE d.segment IS NOT NULL' if p.dropna else ''
            grouped=c.execute(f'SELECT d.segment,{agg} FROM facts f LEFT JOIN dims d ON f.join_key=d.join_key {where} GROUP BY d.segment').fetchall()
            right=sum(int(x[-1]) for x in grouped)
        return int(left),int(right)
    if backend=='sqlalchemy':
        import sqlalchemy as sa
        engine=sa.create_engine('sqlite+pysqlite:///:memory:')
        md=sa.MetaData()
        f=sa.Table('facts',md,sa.Column('join_key',sa.String),sa.Column('amount',sa.Integer))
        d=sa.Table('dims',md,sa.Column('join_key',sa.String),sa.Column('segment',sa.String))
        md.create_all(engine)
        try:
            with engine.begin() as c:
                c.execute(f.insert(),[{'join_key':r[p.join_key],'amount':r[p.value_column]} for r in facts])
                if dims:c.execute(d.insert(),[{'join_key':r[p.join_key],'segment':r[p.dimension_group_key]} for r in dims])
                agg=(sa.func.coalesce(sa.func.sum(f.c.amount),0) if p.aggregate=='sum' else sa.func.count(f.c.amount))
                left=int(c.scalar(sa.select(agg).select_from(f)))
                stmt=sa.select(agg).select_from(f.outerjoin(d,f.c.join_key==d.c.join_key))
                if p.dropna:stmt=stmt.where(d.c.segment.is_not(None))
                grouped=c.execute(stmt.group_by(d.c.segment)).all()
                right=sum(int(r[0]) for r in grouped)
        finally:engine.dispose()
        return left,right
    raise ValueError('unregistered backend')

COMPOSITE_PROGRAMS={
  'sum_join_group':('df["amount"].sum()',
     'df.merge(dim,on="group",how="left").groupby("region",dropna=True)["amount"].sum().sum()'),
  'count_join_group':('df["amount"].count()',
     'df.merge(dim,on="group",how="left").groupby("region",dropna=True)["amount"].count().sum()'),
}
