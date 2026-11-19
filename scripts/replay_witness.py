"""Independent stdlib witness replay; included as replay.py in every reducer ZIP.

Run `python replay.py` from an unpacked bundle. Does not import AxiomForge.
Only verifies the declared finite contract and chosen target failure.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent


def fail(msg):
    print(json.dumps({'status':'NOT_REPRODUCED','error':str(msg)},ensure_ascii=False))
    return 3


def run():
    data=json.loads((ROOT/'witness.json').read_text(encoding='utf-8'))
    if data.get('schema')!='axiomforge.sql_reproducer.v1':
        return fail('unsupported schema')
    if set(data.get('sha256',{}))!={'reproducer.sqlite','before.sql','after.sql','replay.py'}:
        return fail('input digest inventory missing')
    for name,digest in data['sha256'].items():
        if name not in ('reproducer.sqlite','before.sql','after.sql','replay.py'):
            return fail('unexpected input name')
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
            return fail('input file digest mismatch')
    contract=data['contract']
    def ident(k):
        if not isinstance(k,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k):
            raise ValueError('invalid SQL identifier')
        return '"'+k+'"'
    def fact(k):
        if type(k) not in (str,int) or not str(k):
            raise ValueError('unsupported fact ID')
        return str(k)
    def measure(m):
        if type(m) is not int or abs(m)>10**12:
            raise ValueError('unsupported amount')
        return m
    if (ROOT/'reproducer.sqlite').stat().st_size > 64_000_000:
        return fail('database exceeds standalone replay size budget')
    if any((ROOT/name).stat().st_size>250_000 for name in ('before.sql','after.sql')):
        return fail('SQL input exceeds replay budget')
    uri=(ROOT/'reproducer.sqlite').resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as con:
        con.execute('PRAGMA query_only=ON')
        con.enable_load_extension(False)
        started=time.monotonic()
        steps=[0]
        def guard():
            steps[0]+=1000
            return 1 if steps[0]>40_000_000 or time.monotonic()-started>25 else 0
        con.set_progress_handler(guard,1000)
        allow={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION}
        if hasattr(sqlite3,'SQLITE_RECURSIVE'):allow.add(sqlite3.SQLITE_RECURSIVE)
        reject_funcs={'load_extension','random','randomblob','date','datetime','time',
                      'strftime','julianday','unixepoch','last_insert_rowid'}
        def auth(op,_first,second,_db,_trigger):
            if op not in allow or (op==sqlite3.SQLITE_FUNCTION and str(second).lower() in reject_funcs):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(auth)
        source_sql=(f'SELECT {ident(contract["fact_id"])} AS fid,'
                    f'{ident(contract["measure"])} AS val FROM {ident(contract["fact_table"])}')
        pairs=[(fact(x),measure(y)) for x,y in con.execute(source_sql)]
        expected=dict(pairs)
        if len(expected)!=len(pairs) or not expected:
            return fail('fact source is not unique and nonempty')
        max_rows=contract['max_rows']
        def query(filename):
            sql=(ROOT/filename).read_text(encoding='utf-8-sig').strip().rstrip(';')
            if not re.match(r'^(SELECT|WITH)\b',sql,re.I):
                raise ValueError('not a supported SELECT')
            cursor=con.execute(sql)
            names=[n[0] for n in cursor.description]
            if names.count('fact_id')!=1 or names.count('amount')!=1:
                raise ValueError('query must emit fact_id and amount')
            rows=[]
            for i,row in enumerate(cursor):
                if i>=max_rows: raise ValueError('query exceeds max_rows')
                rows.append((fact(row[names.index('fact_id')]),measure(row[names.index('amount')])))
            return rows
        before=query('before.sql');after=query('after.sql')
        def flags(rows,ident):
            relevant=[v for k,v in rows if k==ident]
            n=len(relevant)
            if ident not in expected:
                return {'UNEXPECTED_ID'} if n else set()
            output=set()
            if n==0: output.add('MISSING')
            if n>1: output.add('DUPLICATE')
            if n and sum(relevant)!=expected[ident]: output.add('MEASURE_CHANGED')
            return output
        before_good=(len(before)==len(expected) and
                     len({k for k,_ in before})==len(expected) and
                     all(not flags(before,k) for k in expected))
        target=data['witness']['fact_id']; flag=data['witness']['flag']
        detected=flags(after,target)
        if not before_good or flag not in detected:
            return fail(f'fingerprint not reproduced: before={before_good} after={sorted(detected)}')
        print(json.dumps({'status':'REPRODUCED','target':target,'failure_flag':flag,
                          'source_rows':len(expected),'before_rows':len(before),'after_rows':len(after)},
                         ensure_ascii=False))
        return 0


if __name__=='__main__':
    try:
        raise SystemExit(run())
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,json.JSONDecodeError) as exc:
        raise SystemExit(fail(exc))
