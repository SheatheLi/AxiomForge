"""Actual historical SQLAlchemy #13548 failure-path resource ownership.

Observes explicit pool-side closing, NOT durable server-side connection leaks.
Using SQLite deliberately avoids claims about Oracle/PostgreSQL server sessions.
No patch is applied to SQLAlchemy and no self-written database engine is used.
"""
from __future__ import annotations
from typing import Any
from sqlalchemy import create_engine,event,text
from axiomforge.model import Contract,Authority

CONTRACT=Contract(
 id="sqlalchemy.pool.failed_connect_cleanup", title="Failed connect event preserves resource ownership",
 statement="Connections created by the pool during a failing connect event are explicitly closed by that pool",
 applicability="SQLAlchemy 2.x pre-fix; event handler raises before connection ownership is transferred",
 authority=Authority.DOCUMENTED,
 authority_url="https://github.com/sqlalchemy/sqlalchemy/issues/13548",
 target="sqlalchemy",bound="Measured explicit pool close path only; no claim of durable SQLite server sessions")

def input_for(seed:int)->dict[str,Any]:
    return {"stage":("first_connect","connect")[seed%2],"attempts":(seed%3)+2}

def execute(payload:dict[str,Any],control:dict[str,Any]|None=None)->dict[str,Any]:
    control=control or {}
    stage=control.get("stage",payload["stage"])
    if stage not in ("first_connect","connect","none"):
        raise ValueError("unsupported connect event")
    attempts=int(payload.get("attempts",2))
    if not 1<=attempts<=12:raise ValueError("attempts must be between 1 and 12")
    engine=create_engine("sqlite+pysqlite:///:memory:",pool_size=1)
    pool=engine.pool
    real_create=pool._invoke_creator
    real_close=pool._close_connection
    created=[];closed=[];resource_ordinals={};errors=0
    def creator(record):
        conn=real_create(record)
        ordinal=len(created)+1
        created.append(ordinal)
        resource_ordinals[id(conn)]=ordinal
        return conn
    def closer(conn, **kw):
        closed.append(resource_ordinals.get(id(conn),-1))
        return real_close(conn,**kw)
    pool._invoke_creator=creator
    pool._close_connection=closer
    def fail(conn,record):
        raise RuntimeError("injected failing initialization event")
    if stage!="none":event.listen(engine,stage,fail)
    try:
        for _ in range(attempts):
            try:
                with engine.connect() as conn:conn.execute(text("SELECT 1"))
            except RuntimeError as exc:
                if str(exc)!="injected failing initialization event":raise
                errors+=1
    finally:
        engine.dispose()
    # Count ids rather than assume every attempt produced a unique resource.
    # Observe explicit _close_connection path, not possible GC side-effects.
    unique_created=len(set(created));unique_closed=len(set(closed))
    admitted=stage in ("first_connect","connect") and errors==attempts
    return {"left":{"created":unique_created},"right":{"closed":unique_closed},
            "state":{"stage":stage,"attempts":attempts,"errors":errors,
                "created_ids":created,"closed_ids":closed,
                "measure":"pool-explicit-closer-invocations-not-server-leak"},
            "holds":set(created).issubset(set(closed)),
            "checks":{"inputs_equivalent":True,"domain_valid":admitted,"semantic_change":False}}
