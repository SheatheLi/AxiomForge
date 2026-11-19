"""Read-only event-window lineage preflight using actual per-batch SQLite SQL.

Portable semantic reproduction of missing source-side event-time filters. Does
not parse/execute dbt Fusion, Snowflake, timezone session settings, or Trino.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import date,timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import re

from axiomforge.sql_review import ReviewError, _connection, _ident, _read_sql

class MicrobatchError(ValueError):pass


def _day(text):
    if type(text) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):
        raise MicrobatchError('strict YYYY-MM-DD TEXT event dates are required')
    try:return date.fromisoformat(text)
    except ValueError as ex: raise MicrobatchError('invalid calendar date') from ex


def _read_event_rows(db,sql:str,bind=None,id_col='event_id',date_col='event_at',max_rows=50000):
    try:
        cursor=db.execute(sql,bind or {})
        names=[x[0] for x in cursor.description or ()]
        if names.count(id_col)!=1 or names.count(date_col)!=1 or id_col==date_col:
            raise MicrobatchError('SELECT must expose exactly event_id and event_at')
        rows=cursor.fetchmany(max_rows+1)
        if len(rows)>max_rows:raise MicrobatchError('unbounded event count')
        i,j=names.index(id_col),names.index(date_col)
        out=[]
        for row in rows:
            if type(row[i]) not in (int,str) or str(row[i])=='':
                raise MicrobatchError('non-empty text or integer event ID is required')
            out.append((str(row[i]),_day(row[j])))
        return out
    except sqlite3.Error as ex:
        raise MicrobatchError(f'SQL rejected or failed: {ex}') from ex


def inspect_microbatch(database:str|Path,sql:str|Path,*,source:str,source_id:str,source_date:str,
                       start:str,end:str,stride_days:int=1,max_rows:int=50000) ->dict:
    if not 1<=stride_days<=31 or not 1<=max_rows<=50000:
        raise MicrobatchError('invalid batch/resource limit')
    first,last=_day(start),_day(end)
    if first>=last or (last-first).days>31:
        raise MicrobatchError('range must be 1–31 calendar days')
    for x in (source,source_id,source_date):_ident(x)
    database=Path(database).resolve()
    text=Path(sql).read_text('utf8') if isinstance(sql,Path) else str(sql)
    text=text.strip().rstrip(';').strip()
    if not text or len(text.encode('utf8'))>100000 or not re.match(r'^(SELECT|WITH)\b',text,re.I) or ';' in text:
        raise MicrobatchError('single read-only SELECT SQL required, at most 100KB')
    with closing(_connection(database)) as db:
        rows=_read_event_rows(db,f'SELECT {_ident(source_id)} AS event_id,{_ident(source_date)} AS event_at FROM {_ident(source)}',max_rows=max_rows)
        counter=Counter(i for i,_ in rows)
        if len(rows)!=len(counter):
            raise MicrobatchError('source event identities must be unique')
        if not rows:
            raise MicrobatchError('empty source cannot validate source-side filtering')
        expected={i:d for i,d in rows if first<=d<last}
        if not expected:
            raise MicrobatchError('no events in configured range')
        total=Counter(); windows=[]; all_source={i:d for i,d in rows}; changed_contents=[]
        current=first
        while current<last:
            nxt=min(current+timedelta(days=stride_days),last)
            actual=_read_event_rows(db,text,bind={'start':current.isoformat(),'end':nxt.isoformat()},max_rows=max_rows)
            leaking=[i for i,d in actual if not (current<=d<nxt)]
            in_window=[i for i,d in actual if current<=d<nxt]
            need={i for i,d in rows if current<=d<nxt}
            missing=sorted(need-set(in_window))
            duplicates=[i for i,c in Counter(in_window).items() if c>1]
            changed=[i for i,d in actual if i not in all_source or all_source[i]!=d]
            changed_contents.extend(changed)
            windows.append({'start':current.isoformat(),'end':nxt.isoformat(),
                'expected_source_rows':len(need),'actual_read_rows':len(actual),
                'out_of_window_rows':len(leaking),'missing_in_window':len(missing),
                'within_window_duplicates':len(duplicates),'content_mismatches':len(changed),
                'example_leaks':leaking[:10],
                'example_missing':missing[:10]})
            total.update(i for i,_ in actual)
            current=nxt
        duplicated={i:c for i,c in total.items() if c>1}
        leaked_global={i:c for i,c in total.items() if i not in expected}
        missing_global=[i for i in expected if not total[i]]
        faults=[]
        if any(x['out_of_window_rows'] for x in windows):faults.append('SOURCE_WINDOW_LEAK')
        if any(x['missing_in_window'] for x in windows):faults.append('WINDOW_SOURCE_MISSING')
        if any(x['within_window_duplicates'] for x in windows):faults.append('SOURCE_ID_DUPLICATED')
        if duplicated:faults.append('MULTIBATCH_DUPLICATED')
        if leaked_global:faults.append('OUTSIDE_RANGE_RESULT')
        if missing_global:faults.append('GLOBAL_MISSING')
        if changed_contents:faults.append('SOURCE_CONTENT_MISMATCH')
    return {'schema':'axiomforge.microbatch_preflight.v1','status':'RISK' if faults else 'PASS',
        'scope':'SQLite event-date / source-side query semantics; NOT dbt Fusion/Snowflake engine parity',
        'range':{'start':start,'end':end,'stride_days':stride_days},
        'counts':{'source_rows':len(rows),'expected_range_rows':len(expected),
            'total_batch_read_rows':sum(x['actual_read_rows'] for x in windows),
            'cross_batch_duplicated_id_count':len(duplicated),
            'global_missing_id_count':len(missing_global),
            'outside_range_id_count':len(leaked_global),
            'content_mismatch_rows':len(changed_contents)},
        'flags':faults,'windows':windows,
        'evidence':{'sqlite_sha256':hashlib.sha256(database.read_bytes()).hexdigest(),
            'query_sha256':hashlib.sha256(text.encode('utf8')).hexdigest()},
        'external_issue':'https://github.com/dbt-labs/dbt/issues/14525',
        'limits':['Date-only UTC-agnostic comparison. No Snowflake timestamp/session timezone simulation.',
                  'Caller must supply event_id and event_at columns in output.',
                  'Read-only bounded SQLite query; no real dbt materialization.']}


def main(argv=None):
    p=argparse.ArgumentParser(description='Actual SQLite batch source-query window contract preflight')
    p.add_argument('--database',required=True);p.add_argument('--sql',required=True)
    p.add_argument('--source',required=True);p.add_argument('--source-id',default='event_id')
    p.add_argument('--source-date',default='event_at')
    p.add_argument('--start',required=True);p.add_argument('--end',required=True)
    p.add_argument('--stride-days',type=int,default=1);p.add_argument('--out')
    a=p.parse_args(argv)
    try:
        report=inspect_microbatch(a.database,Path(a.sql),source=a.source,source_id=a.source_id,
                                   source_date=a.source_date,start=a.start,end=a.end,stride_days=a.stride_days)
        data=json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
        if a.out:
            with open(a.out,'x',encoding='utf8') as f:f.write(data)
        print(data)
        return 0 if report['status']=='PASS' else 2
    except (OSError,ReviewError,MicrobatchError) as ex:
        print('AxiomForge microbatch ERROR: '+str(ex),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
