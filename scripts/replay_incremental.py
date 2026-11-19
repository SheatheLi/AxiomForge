"""Independent, standard-library-only replay of an incremental SQL counterexample.

Intended to be copied into an AxiomForge witness ZIP; deliberately imports
NOTHING from AxiomForge, and verifies all bundled data against a hash receipt.
Do not run untrusted ZIP contents outside an isolated test environment.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time


def fail(msg: str):
    print('NOT_REPRODUCED: ' + msg, file=sys.stderr)
    raise SystemExit(1)


def safe_name(s: str) -> str:
    if not isinstance(s,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,127}',s):
        fail('invalid name')
    return '"'+s+'"'


def main() -> None:
    here=Path(__file__).resolve().parent
    meta=json.loads((here/'witness.json').read_text('utf8'))
    if meta.get('schema') != 'axiomforge.external_issue_witness.v1':
        fail('unknown schema')
    for name in ('scenario.sqlite','candidate.sql'):
        p=here/name
        if hashlib.sha256(p.read_bytes()).hexdigest() != meta['sha256'][name]:
            fail(f'input integrity failed: {name}')
    contract=meta['contract']
    t=contract['target'];s=contract['stage'];keys=contract['keys']
    for name in [t,s,*keys]:safe_name(name)
    candidate=(here/'candidate.sql').read_text('utf8').strip().rstrip(';').strip()
    m=re.match(r'^DELETE\s+FROM\s+(["A-Za-z_0-9]+)',candidate,re.I)
    if not m or m.group(1).strip('"') != t or ';' in candidate:
        fail('only a single declared target DELETE is supported')
    root=sqlite3.connect((here/'scenario.sqlite').as_uri()+'?mode=ro',uri=True)
    c=sqlite3.connect(':memory:')
    try:
        root.backup(c)
        root.close()
        def rows(table):
            q='SELECT _rowid_, '+','.join(safe_name(k) for k in keys)+' FROM '+safe_name(table)
            result=c.execute(q).fetchmany(50001)
            if len(result)>50000:fail('row cap')
            return [(x[0],tuple(x[1:])) for x in result]
        target=rows(t);stage=rows(s)
        sc=Counter(k for _,k in stage)
        oldkeys={k for _,k in stage if contract['null_safe'] or None not in k}
        expected={rid for rid,k in target if k in oldkeys and (contract['null_safe'] or None not in k)}
        before={i for i,_ in target}
        end=time.monotonic()+8
        c.set_progress_handler(lambda: 1 if time.monotonic()>end else 0, 1000)
        def auth(op,a,b,db,source):
            allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
            if hasattr(sqlite3,'SQLITE_RECURSIVE'):allowed.add(sqlite3.SQLITE_RECURSIVE)
            if op in allowed:
                if op==sqlite3.SQLITE_FUNCTION and str(b).lower() in ('random','randomblob','load_extension','date','time','datetime','sqlite_version'):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_OK if ((op==sqlite3.SQLITE_DELETE and a==t and source is None) or op==sqlite3.SQLITE_TRANSACTION) else sqlite3.SQLITE_DENY
        c.set_authorizer(auth)
        try:c.execute(candidate)
        finally:
            c.set_authorizer(None)
            c.set_progress_handler(None,0)
        actual=before - {x[0] for x in rows(t)}
        target_keys=dict(target)
        f=meta['failure']
        key=tuple(f['key'])
        if f['flag']=='UNINTENDED_DELETE':
            matched=any(target_keys.get(i)==key for i in actual-expected)
        elif f['flag']=='MISSED_DELETE':
            matched=any(target_keys.get(i)==key for i in expected-actual)
        elif f['flag']=='STAGE_KEY_COLLISION':
            matched=sc[key]>1
        elif f['flag']=='MULTIPLE_SOURCE_MATCH':
            matched=sc[key]>1 and key in (k for _,k in target)
        elif f['flag']=='NULL_MATCH_GAP':
            matched=(not contract['null_safe'] and None in key and key in sc and key in (k for _,k in target))
        else:fail('unsupported fingerprint')
        if not matched:fail('original failure fingerprint is gone')
        print('REPRODUCED', f['flag'],json.dumps(list(key),ensure_ascii=False),
              'target_rows='+str(len(target)),'stage_rows='+str(len(stage)))
    finally:
        c.close()

if __name__=='__main__':main()
