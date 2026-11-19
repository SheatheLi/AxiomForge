"""SQLAlchemy #13548 pre-fix code-path contract: connect event raises.

Executes the installed SQLAlchemy 2.0.50 real QueuePool and connect event
mechanism with a SQLite DBAPI (no external database). Demonstrates the pool
never invokes its formal close path on DBAPI handles orphaned by the event
exception. SQLite's own GC behavior may eventually close the OS handle, so
this DOES NOT prove durable server-side session leakage on SQLite.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import sqlalchemy
from sqlalchemy import create_engine,event
from sqlalchemy.pool import QueuePool
from axiomforge.issue_engine.ownership_oracle import OwnershipOracle


def run_case(*, explicit_close: bool, runs:int=5):
    created=[]
    pool_closes=[]
    explicit=[]
    ownership=OwnershipOracle()
    engine=create_engine('sqlite:///:memory:',poolclass=QueuePool,pool_size=1,max_overflow=0)
    pool=engine.pool
    real_create=pool._invoke_creator
    real_close=pool._close_connection
    def create(rec):
        connection=real_create(rec)
        created.append(id(connection))
        ownership.created_resource(connection)
        return connection
    def close(conn,*args,**kwargs):
        pool_closes.append(id(conn))
        result=real_close(conn,*args,**kwargs)
        ownership.retired_resource(conn)
        return result
    pool._invoke_creator=create
    pool._close_connection=close
    @event.listens_for(engine,'connect')
    def fail_connect(dbapi_connection,connection_record):
        if explicit_close:
            dbapi_connection.close()
            ownership.retired_resource(dbapi_connection)
            explicit.append(1)
        raise RuntimeError('simulated connect event setup failure')
    failures=0
    for _ in range(runs):
        try:
            with engine.connect():pass
        except RuntimeError:
            failures+=1
    engine.dispose()
    return {'failures':failures, 'dbapi_created_calls':len(created),
            'formal_pool_close_calls':len(pool_closes),
            'manual_dbapi_close_calls':len(explicit),
            'pool_close_gap':len(created)-len(pool_closes),
            'ownership_oracle':ownership.snapshot(),
            'note':'The GC/SQLite physical socket state is NOT measured.'}


def main():
    baseline=run_case(explicit_close=False)
    workaround=run_case(explicit_close=True)
    return {'library':'SQLAlchemy','version':sqlalchemy.__version__,
            'issue':'https://github.com/sqlalchemy/sqlalchemy/issues/13548',
            'integration':'Real QueuePool and event listener with SQLite DBAPI',
            'pre_fix_behavior':baseline,'application_workaround':workaround,
            'scope':'Formal pool close path only; does not prove remote DB sessions or test upstream post-fix version.'}

if __name__=='__main__':
    result=main()
    if len(sys.argv)>1:
        p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
