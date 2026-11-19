"""Execute real SQLAlchemy QueuePool connect-event failure on installed version.

Tests observed close *calls*, NOT actual server-side session lifetime. Uses
SQLite and SQLAlchemy internal pool hooks only for observational study.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlalchemy
from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool
from axiomforge.issue_engine.resource_protocol import ProtocolChecker
from axiomforge.issue_engine.ownership_oracle import OwnershipOracle


def run_case(mode: str, *, attempts: int=5) -> dict:
    checker = ProtocolChecker()
    legacy = OwnershipOracle()
    engine = create_engine('sqlite:///:memory:', poolclass=QueuePool,
                           pool_size=1, max_overflow=0)
    pool = engine.pool
    orig_create = pool._invoke_creator
    orig_close = pool._close_connection
    handles: dict[int,tuple[str,str]] = {}
    allocations = 0
    pool_closes = 0
    manual_closes = 0
    failures = 0

    def record_create(record):
        nonlocal allocations
        dbapi = orig_create(record)
        allocations += 1
        token = f'dbapi:{allocations}'
        scope = f'connect-init:{allocations}'
        handles[id(dbapi)] = (token,scope)
        checker.emit('allocate',resource=token,owner=scope,
                     note='SQLAlchemy _invoke_creator returned DBAPI connection')
        legacy.created_resource(dbapi)
        return dbapi

    def record_close(dbapi, *args, **kwargs):
        nonlocal pool_closes
        pool_closes += 1
        if id(dbapi) in handles:
            token,_ = handles[id(dbapi)]
            checker.emit('retire',resource=token,note='SQLAlchemy _close_connection invoked')
            legacy.retired_resource(dbapi)
        return orig_close(dbapi,*args,**kwargs)

    pool._invoke_creator = record_create
    pool._close_connection = record_close

    @event.listens_for(engine, 'connect')
    def connect_event(dbapi, record):
        nonlocal manual_closes
        token,scope = handles[id(dbapi)]
        if mode=='healthy':
            checker.emit('transfer',resource=token,owner='pool:main',
                         note='Connected resource owned by live QueuePool')
            checker.emit('scope_finish',scope=scope)
            return
        if mode=='manual-close':
            dbapi.close()
            checker.emit('retire',resource=token,note='Application close during connect event')
            legacy.retired_resource(dbapi)
            manual_closes += 1
        checker.emit('scope_abort',scope=scope,note='Real SQLAlchemy connect-event exception')
        raise RuntimeError('Controlled failure in SQLAlchemy connect event')

    for _ in range(attempts):
        try:
            with engine.connect():
                pass
        except RuntimeError:
            failures += 1
    checker.emit('pool_observe',pool='main',available=pool.size()-pool.checkedout(),
                 note='Logical pool capacity is not a physical close proof')
    before_dispose = checker.finalize()
    legacy_before_dispose = legacy.snapshot()
    engine.dispose()
    after_dispose = checker.finalize()
    return {'case':mode,'library':'SQLAlchemy','version':sqlalchemy.__version__,
            'issue':'https://github.com/sqlalchemy/sqlalchemy/issues/13548',
            'attempts':attempts,'failures':failures,'created':allocations,
            'formal_pool_close_calls':pool_closes,'manual_dbapi_close_calls':manual_closes,
            'legacy_created_minus_retired_before_dispose':legacy_before_dispose['not_retired'],
            'protocol_before_dispose':before_dispose,
            'protocol_after_dispose':after_dispose,
            'events':checker.events,
            'limitation':'Uses installed QueuePool and real connect listener with SQLite; physical server sessions not observed.'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    cases={name:run_case(name) for name in ('connect-failure','manual-close','healthy')}
    if not (len(cases['connect-failure']['protocol_before_dispose']['findings'])==5
            and not cases['manual-close']['protocol_before_dispose']['findings']
            and not cases['healthy']['protocol_before_dispose']['findings']):
        raise AssertionError('Expected cross-layer protocol contrast not observed')
    output={'study':'cross-layer-obligation-counterexample', 'cases':cases,
            'limitations':['Installed pre-fix version, not new upstream defect',
                           'SQLite driver, not remote sessions',
                           'Healthy resources legitimately remain OPEN in pool until dispose',
                           'Not a comparative state-of-art tool benchmark']}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:{'created':v['created'],'found':len(v['protocol_before_dispose']['findings']),
                          'legacy_outstanding':v['legacy_created_minus_retired_before_dispose']}
                      for k,v in cases.items()},indent=2))

if __name__=='__main__':main()
