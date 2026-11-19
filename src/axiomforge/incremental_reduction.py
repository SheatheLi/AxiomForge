"""Cross-table witness extraction for external incremental SQL issue mechanisms.

A *relative* single-row-deletion minimality claim is independently audited;
no claim of global shortest SQL/database or resolving upstream issue.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import zipfile

from axiomforge.incremental import IncrementalError, _assess, _ident, _items, inspect_database
from axiomforge.reduction import _Engine

SCHEMA = 'axiomforge.external_issue_witness.v1'


def _fingerprint(r: dict, flag: str):
    if flag not in r['flags']:
        raise IncrementalError(f'requested failure does not exist: {flag}')
    if flag in {'UNINTENDED_DELETE','MISSED_DELETE'}:
        field='unintended' if flag=='UNINTENDED_DELETE' else 'missed'
        return {'flag': flag, 'key': r['examples'][field][0]['key']}
    if flag=='STAGE_KEY_COLLISION':
        return {'flag':flag,'key':r['examples']['stage_collisions'][0]['key']}
    if flag=='MULTIPLE_SOURCE_MATCH':
        return {'flag':flag,'key':r['examples']['multiple_source_matches'][0]['key']}
    if flag=='NULL_MATCH_GAP':
        return {'flag':flag,'key':r['examples']['null_match_pairs'][0]['key']}
    raise IncrementalError(f'cannot identify stable witness for {flag}')


def _holds(report: dict, finger: dict)->bool:
    if finger['flag'] not in report['flags']:
        return False
    ex=report['examples']
    key=finger['key']
    if finger['flag'] in ('UNINTENDED_DELETE','MISSED_DELETE'):
        field='unintended' if finger['flag']=='UNINTENDED_DELETE' else 'missed'
        return any(x['key']==key for x in ex[field])
    if finger['flag']=='STAGE_KEY_COLLISION':
        return any(x['key']==key for x in ex['stage_collisions'])
    if finger['flag']=='MULTIPLE_SOURCE_MATCH':
        return any(x['key']==key for x in ex['multiple_source_matches'])
    if finger['flag']=='NULL_MATCH_GAP':
        return any(x['key']==key for x in ex['null_match_pairs'])
    return False


def reduce_issue(database: Path | str, *, target: str, stage: str, keys: list[str],
                 delete_sql: str, flag: str, grain: str='row', null_safe: bool=False,
                 max_calls: int = 1000, timeout: float=40.0,
                 audit: bool=True) -> tuple[sqlite3.Connection,dict]:
    if not (1 <= max_calls <= 250_000 and 0 < timeout <= 300):
        raise IncrementalError('invalid resource bounds')
    database=Path(database).resolve()
    if not database.is_file() or database.stat().st_size > 64_000_000:
        raise IncrementalError('missing or oversized database')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as original:
        db=sqlite3.connect(':memory:')
        original.backup(db)
    try:
        before=_assess(db,target=target,stage=stage,keys=keys,
            delete_sql=delete_sql,grain=grain,null_safe=null_safe,sample=50000)
        finger=_fingerprint(before,flag)
        initial={table:[rid for rid,_ in _items(db,table,keys)] for table in (target,stage)}
        def oracle(conn):
            try:
                report=_assess(conn,target=target,stage=stage,keys=keys,
                    delete_sql=delete_sql,grain=grain,null_safe=null_safe,sample=50000)
                return _holds(report,finger)
            except (IncrementalError,sqlite3.Error):
                return False
        if not oracle(db):
            raise IncrementalError('original fingerprint did not survive its own oracle')
        engine=_Engine(db,initial,{},oracle,max_calls=max_calls,
                       deadline=time.monotonic()+timeout,preserve_fks=True)
        engine.reduce()
        verified=oracle(db)
        if not verified:
            raise IncrementalError('post-reduction fingerprint disappeared')
        # A separate engine with fresh counters, applied to a copied snapshot:
        # exhaustively try each remaining single row, without relying on
        # reducer.one_minimal or the reduction event log.
        independent=False
        independent_calls=0
        if audit:
            full=[]
            for table in (target,stage):
                full += [(table,rid) for rid,_ in _items(db,table,keys)]
            if len(full)<=2_000:
                independent=True
                for table,rid in full:
                    db.execute('SAVEPOINT independent_one_row')
                    try:
                        db.execute(f'DELETE FROM {_ident(table)} WHERE _rowid_=?',(rid,))
                        independent_calls+=1
                        if not db.execute('PRAGMA foreign_key_check').fetchone() and oracle(db):
                            independent=False
                            break
                    finally:
                        db.execute('ROLLBACK TO SAVEPOINT independent_one_row')
                        db.execute('RELEASE SAVEPOINT independent_one_row')
        report={
            'schema':SCHEMA,'failure':finger,
            'contract':{'target':target,'stage':stage,'keys':keys,'grain':grain,'null_safe':null_safe},
            'before':before['counts'],
            'after':_assess(db,target=target,stage=stage,keys=keys,
                            delete_sql=delete_sql,grain=grain,null_safe=null_safe)['counts'],
            'reduction':{'oracle_calls':engine.calls,'initial_rows':sum(map(len,initial.values())),
                         'retained_rows':engine.remaining(),'algorithm_1_minimal':getattr(engine,'one_minimal',False),
                         'independent_1_minimal':independent,'independent_audit_calls':independent_calls,
                         'budget_exhausted':engine.exhausted},
            'limitations':'1-minimal w.r.t permitted individual table-row deletions, not global minimum',
        }
        return db,report
    except BaseException:
        db.close()
        raise


def emit_bundle(db:sqlite3.Connection, report:dict, *, candidate_sql:str, out:Path, replay_script:Path)->dict:
    if out.exists():raise IncrementalError('output bundle exists; refuse overwrite')
    replay=replay_script.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp)
        target=root/'scenario.sqlite'
        with closing(sqlite3.connect(target)) as on_disk:
            db.backup(on_disk)
        (root/'candidate.sql').write_text(candidate_sql,encoding='utf8')
        report['sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [target,root/'candidate.sql']}
        (root/'witness.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
        (root/'replay.py').write_bytes(replay)
        out.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(out,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for name in ('scenario.sqlite','candidate.sql','witness.json','replay.py'):
                z.write(root/name,name)
    return report


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description='Reduce a specific public incremental SQL failure into a self-contained witness')
    p.add_argument('--database',required=True)
    p.add_argument('--target',required=True)
    p.add_argument('--stage',required=True)
    p.add_argument('--keys',required=True)
    p.add_argument('--delete-file',required=True)
    p.add_argument('--flag',choices=['UNINTENDED_DELETE','MISSED_DELETE','NULL_MATCH_GAP','STAGE_KEY_COLLISION','MULTIPLE_SOURCE_MATCH'],required=True)
    p.add_argument('--grain',choices=['row','partition'],default='row')
    p.add_argument('--null-safe',action='store_true')
    p.add_argument('--max-calls',type=int,default=1000)
    p.add_argument('--timeout',type=float,default=40.)
    p.add_argument('--out',required=True)
    args=p.parse_args(argv)
    try:
        sql=Path(args.delete_file).read_text('utf8')
        if len(sql.encode('utf8'))>100_000:raise IncrementalError('oversized SQL')
        root=Path(__file__).resolve().parents[2]
        replay=root/'scripts'/'replay_incremental.py'
        db,report=reduce_issue(args.database,target=args.target,stage=args.stage,
            keys=[k.strip() for k in args.keys.split(',')],delete_sql=sql,flag=args.flag,
            grain=args.grain,null_safe=args.null_safe,max_calls=args.max_calls,timeout=args.timeout)
        try:emit_bundle(db,report,candidate_sql=sql,out=Path(args.out),replay_script=replay)
        finally:db.close()
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report['reduction']['independent_1_minimal'] else 2
    except (OSError,IncrementalError,sqlite3.Error) as ex:
        print('AxiomForge issue witness ERROR: '+str(ex),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
