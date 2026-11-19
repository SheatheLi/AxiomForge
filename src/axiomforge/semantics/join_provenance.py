"""Cardinality-provenance guards across fact/dimension LEFT JOIN aggregation.

Very deliberately excludes NULL join keys. Pandas joins NULL=NULL while SQL
uses three-valued logic; mixing them is unsound. This compiler never silently
asserts cross-backend equality without checking these semantic preconditions.
"""
from __future__ import annotations
import ast,hashlib,json,collections
from dataclasses import dataclass
from typing import Any
from .compiler import CompileError
from .algebra import DomainError

@dataclass(frozen=True)
class JoinPlan:
    fact_frame: str
    dimension_frame: str
    key: str
    measure: str
    operation: str
    left_source: str
    right_source: str

    def serial(self): return self.__dict__.copy()
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.serial(),sort_keys=True).encode()).hexdigest()

def _zero_call(x,name):
    return isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute) and x.func.attr==name and not x.args and not x.keywords

def _str_slice(x):
    if not (isinstance(x,ast.Subscript) and isinstance(x.slice,ast.Constant) and type(x.slice.value)==str):
        raise CompileError('only static one-column selection is supported')
    return x.value,x.slice.value

def compile_join(left:str,right:str)->JoinPlan:
    try: a=ast.parse(left,mode='eval').body;b=ast.parse(right,mode='eval').body
    except SyntaxError as exc:raise CompileError('parse error') from exc
    if not (_zero_call(a,'sum') or _zero_call(a,'count')) or not isinstance(a.func.value,ast.Subscript):
        raise CompileError('fact side requires a direct additive aggregate')
    fact,measure=_str_slice(a.func.value)
    if not isinstance(fact,ast.Name):raise CompileError('fact must be named')
    if not _zero_call(b,a.func.attr):raise CompileError('matching aggregate required')
    merged,measure_right=_str_slice(b.func.value)
    if measure_right!=measure or not isinstance(merged,ast.Call) or not isinstance(merged.func,ast.Attribute) or merged.func.attr!='merge':
        raise CompileError('right must merge then aggregate the same measure')
    if not isinstance(merged.func.value,ast.Name) or merged.func.value.id!=fact.id:
        raise CompileError('left merge owner must be fact frame')
    if len(merged.args)!=1 or not isinstance(merged.args[0],ast.Name):
        raise CompileError('dimension frame must be a named argument')
    kwargs={}
    for kw in merged.keywords:
        if kw.arg not in ('on','how') or kw.arg in kwargs or not isinstance(kw.value,ast.Constant):
            raise CompileError('only static on= and how= are allowed')
        kwargs[kw.arg]=kw.value.value
    if set(kwargs)!={'on','how'} or kwargs['how']!='left' or type(kwargs['on'])!=str:
        raise CompileError('exact left join with static key required')
    if kwargs['on']==measure or fact.id==merged.args[0].id:
        raise CompileError('join key must differ from measure and frames must differ')
    return JoinPlan(fact.id,merged.args[0].id,kwargs['on'],measure,a.func.attr,left,right)

def _validate(p:JoinPlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]])->None:
    if not isinstance(facts,list) or not facts or not isinstance(dims,list):raise DomainError('nonempty fact, list dimension required')
    if len(facts)>10000 or len(dims)>10000:raise DomainError('join size limit')
    for d in facts:
        if type(d)!=dict or set(d)!={p.key,p.measure}:
            raise DomainError('fact schema mismatch')
        if d[p.key] is None or type(d[p.key]) not in (str,int):
            raise DomainError('NULL joins and non-discrete keys unsupported (SQL/pandas discrepancy)')
        if type(d[p.measure])!=int or abs(d[p.measure])>10**10:
            raise DomainError('only bounded exact integer measures')
    for d in dims:
        if type(d)!=dict or set(d)!={p.key,'label'}:
            raise DomainError('dimension schema mismatch')
        if d[p.key] is None or type(d[p.key]) not in (str,int):
            raise DomainError('NULL joins not certified across different systems')
        if type(d['label'])!=str:raise DomainError('dimension labels must be strings')
    if len(facts)*max(1,len(dims))*max(1,max(abs(d[p.measure]) for d in facts))>2**60:
        raise DomainError('possible SQL aggregate overflow')

def inspect_join(p:JoinPlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]])->dict[str,Any]:
    _validate(p,facts,dims)
    counts=collections.Counter(d[p.key] for d in dims)
    terms=[]
    for i,fact in enumerate(facts):
        count=counts[fact[p.key]]
        multiplicity=max(1,count)
        contribution=(fact[p.measure] if p.operation=='sum' else 1)
        terms.append({'fact_index':i,'join_key':fact[p.key], 'dimension_matches':count,
                      'right_multiplicity':multiplicity,'left_multiplicity':1,
                      'contribution':contribution,'delta':(1-multiplicity)*contribution})
    delta=sum(t['delta'] for t in terms)
    ref_left=sum(t['contribution'] for t in terms)
    ref_right=sum(t['right_multiplicity']*t['contribution'] for t in terms)
    if ref_left-ref_right!=delta:raise AssertionError('join contribution accounting mismatch')
    return {'guard':delta==0,'residual':delta,'trace':terms,'reference':[ref_left,ref_right],
            'simple_unique_key_guard':max(counts.values(),default=0)<=1,
            'plan_hash':p.fingerprint()}

def prove_join(p:JoinPlan)->dict[str,Any]:
    try:import sympy as sp
    except ImportError:return {'status':'PROOF_UNAVAILABLE'}
    m,v=sp.symbols('m v',integer=True,nonnegative=True)
    n=sp.symbols('n',integer=True,positive=True)
    # Per-row identity under multiplicity n >= 1, extend using sum linearity.
    identity=sp.simplify(v-n*v-(1-n)*v)==0
    count_identity=sp.simplify(1-n-(1-n))==0
    if not identity or not count_identity:raise AssertionError('join proof failure')
    return {'status':'ALGEBRAIC_JOIN_IR_CERTIFIED_NOT_SUT_PROVED','fingerprint':p.fingerprint(),
       'guard':'SUM((1 - max(1, dimension_matches_i))*contribution_i)==0',
       'proof_obligations':{'sum_multiplicity':bool(identity),'count_multiplicity':bool(count_identity),
                            'lift':'finite additive fold'},
       'assumptions':['left-outer join; exact bag multiplicity; nonnull keys',
                      'exact integer measure; stable join equality semantics; no SQL overflow'],
       'excluded':['NULL=NULL comparisons in pandas vs SQL','outer/right/inner joins',
                   'floating arithmetic or nonadditive aggregation','arbitrary join predicates'],
       'sympy_version':sp.__version__}

def execute_join(p:JoinPlan,facts:list[dict[str,Any]],dims:list[dict[str,Any]],backend:str)->tuple[int,int]:
    _validate(p,facts,dims)
    if backend=='pandas':
        import pandas as pd
        df=pd.DataFrame(facts);dim=pd.DataFrame(dims)
        merged=df.merge(dim,on=p.key,how='left')
        if p.operation=='sum':return int(df[p.measure].sum()),int(merged[p.measure].sum())
        return int(df[p.measure].count()),int(merged[p.measure].count())
    if backend=='sqlite':
        import sqlite3
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE facts (join_key TEXT, amount INTEGER)')
            c.execute('CREATE TABLE dims (join_key TEXT, label TEXT)')
            c.executemany('INSERT INTO facts VALUES (?,?)',[(f[p.key],f[p.measure]) for f in facts])
            c.executemany('INSERT INTO dims VALUES (?,?)',[(d[p.key],d['label']) for d in dims])
            agg=('COALESCE(SUM(f.amount),0)' if p.operation=='sum' else 'COUNT(f.amount)')
            l=c.execute(f'SELECT {agg} FROM facts f').fetchone()[0]
            r=c.execute(f'SELECT {agg} FROM facts f LEFT JOIN dims d ON f.join_key=d.join_key').fetchone()[0]
        return int(l),int(r)
    if backend=='sqlalchemy':
        import sqlalchemy as sa
        engine=sa.create_engine('sqlite+pysqlite:///:memory:')
        md=sa.MetaData()
        f=sa.Table('facts',md,sa.Column('join_key',sa.String),sa.Column('amount',sa.Integer))
        d=sa.Table('dims',md,sa.Column('join_key',sa.String),sa.Column('label',sa.String))
        md.create_all(engine)
        try:
            with engine.begin() as c:
                c.execute(f.insert(),[{'join_key':row[p.key],'amount':row[p.measure]} for row in facts])
                if dims:c.execute(d.insert(),[{'join_key':row[p.key],'label':row['label']} for row in dims])
                expr=(sa.func.coalesce(sa.func.sum(f.c.amount),0) if p.operation=='sum' else sa.func.count(f.c.amount))
                l=int(c.scalar(sa.select(expr).select_from(f)))
                r=int(c.scalar(sa.select(expr).select_from(f.outerjoin(d,f.c.join_key==d.c.join_key))))
        finally:engine.dispose()
        return l,r
    raise ValueError('unregistered backend')

JOIN_PROGRAMS={
 'sum_fanout':('df["amount"].sum()', 'df.merge(dim, on="group", how="left")["amount"].sum()'),
 'count_fanout':('df["amount"].count()', 'df.merge(dim, on="group", how="left")["amount"].count()'),
}
