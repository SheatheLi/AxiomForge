"""Genuine third-party computation backends, not substituted expected values."""
from __future__ import annotations
from typing import Any
from .compiler import ProgramPair
from .algebra import _validated_rows

class BackendError(ValueError):
    pass

def run_pandas(pair:ProgramPair,rows:list[dict[str,Any]])->tuple[int,int]:
    import pandas as pd
    _validated_rows(pair,rows)
    df=pd.DataFrame(rows)
    col=pair.right.value_column
    if pair.right.operation=='sum':
        left=int(df[col].sum())
    elif pair.right.operation=='count':
        left=int(df[col].count())
    else:raise BackendError('unrecognized operation')
    group_arg=pair.right.group_columns[0] if len(pair.right.group_columns)==1 else list(pair.right.group_columns)
    gb=df.groupby(group_arg,dropna=pair.right.dropna)[col]
    if pair.right.operation=='sum':
        right=int(gb.sum().sum())
    else:
        right=int(gb.count().sum())
    return left,right

def run_sqlite(pair:ProgramPair,rows:list[dict[str,Any]])->tuple[int,int]:
    import sqlite3
    _validated_rows(pair,rows)
    cols=pair.right.group_columns+(pair.right.value_column,)
    # Column identifiers are validated by our parser as key names; SQL identifier
    # escaping here is still mandatory. Never interpolate values.
    quoted=[('"'+s.replace('"','""')+'"') for s in cols]
    if len(set(cols))!=len(cols):raise BackendError('duplicate table columns')
    stmt=f'CREATE TABLE t ({", ".join(x+" INTEGER" if name==pair.right.value_column else x+" TEXT" for name,x in zip(cols,quoted))})'
    with sqlite3.connect(':memory:') as conn:
        conn.execute(stmt)
        conn.executemany('INSERT INTO t VALUES ('+','.join('?' for _ in cols)+')',
                         [tuple(row[col] for col in cols) for row in rows])
        col=quoted[-1]
        aggregate=f'COALESCE(SUM({col}),0)' if pair.right.operation=='sum' else f'COUNT({col})'
        left=int(conn.execute(f'SELECT {aggregate} FROM t').fetchone()[0])
        where=('WHERE '+' AND '.join(f'{c} IS NOT NULL' for c in quoted[:-1])) if pair.right.dropna else ''
        grouping=','.join(quoted[:-1])
        # Use SQL engine for grouping and actual aggregate computation.
        vals=conn.execute(f'SELECT {grouping},{aggregate} FROM t {where} GROUP BY {grouping}').fetchall()
        right=sum(int(r[-1]) for r in vals)
    return left,right

def run_sqlalchemy(pair:ProgramPair,rows:list[dict[str,Any]])->tuple[int,int]:
    import sqlalchemy as sa
    _validated_rows(pair,rows)
    names=pair.right.group_columns+(pair.right.value_column,)
    fields=[sa.Column(name,sa.Integer if name==pair.right.value_column else sa.String,nullable=(name!=pair.right.value_column)) for name in names]
    table=sa.Table('t',sa.MetaData(),*fields)
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    agg=(lambda:sa.func.coalesce(sa.func.sum(table.c[pair.right.value_column]),0)) if pair.right.operation=='sum' else (lambda:sa.func.count(table.c[pair.right.value_column]))
    try:
        table.create(engine)
        with engine.begin() as conn:
            conn.execute(table.insert(),rows)
            left=int(conn.scalar(sa.select(agg()).select_from(table)))
            stmt=sa.select(agg()).select_from(table)
            if pair.right.dropna:
                stmt=stmt.where(sa.and_(*(table.c[k].is_not(None) for k in pair.right.group_columns)))
            stmt=stmt.group_by(*(table.c[k] for k in pair.right.group_columns))
            right=sum(int(r[0]) for r in conn.execute(stmt).all())
    finally:
        engine.dispose()
    return left,right

BACKENDS={'pandas':run_pandas,'sqlite':run_sqlite,'sqlalchemy':run_sqlalchemy}
