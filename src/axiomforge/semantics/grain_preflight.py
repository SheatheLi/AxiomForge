"""Audit a proposed many-to-one JOIN at the fact grain, against real CSV inputs.

This is a *contracted* regression preflight, not SQL semantic inference. It can
reject unsafe changes on a declared fact-grain boundary but does not label a
legitimate one-to-many join as a production bug without that declaration.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import itertools
import json
import pathlib
import re
import sqlite3
import sys
from urllib.parse import quote
from dataclasses import dataclass

MAX_ROWS = 50000
MAX_FILE = 5_000_000
MAX_ABS_MEASURE = 10**10
IDENT = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')

class PreflightError(ValueError):
    pass


def _ident(name: str) -> str:
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise PreflightError(f'unsafe identifier: {name!r}')
    return '"' + name + '"'


def _read_csv(path: pathlib.Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file() or path.stat().st_size > MAX_FILE:
        raise PreflightError(f'CSV missing or larger than {MAX_FILE} bytes: {path}')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames
        if not fields or len(fields) != len(set(fields)):
            raise PreflightError('invalid CSV header')
        for col in fields:
            _ident(col)
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise PreflightError('CSV row limit exceeded')
            if None in row or any(v is None for v in row.values()):
                raise PreflightError('CSV has ragged rows')
            rows.append(row)
    if not rows:
        raise PreflightError('empty CSV')
    return fields, rows


def _integer(raw: str) -> int:
    if not raw or raw.strip() != raw or raw.startswith('+') or not raw.lstrip('-').isascii() or not raw.lstrip('-').isdigit():
        raise PreflightError('integer measure required (no floats or locale strings)')
    value = int(raw)
    if abs(value) > MAX_ABS_MEASURE:
        raise PreflightError('amount outside safe range')
    return value


def candidate_keys(fields: list[str], rows: list[dict], max_width: int = 2) -> list[list[str]]:
    """Enumerate minimal observed uniqueness sets, not inferred logical keys."""
    result: list[list[str]] = []
    for size in range(1, min(max_width, len(fields)) + 1):
        for cols in itertools.combinations(fields, size):
            if any(set(prev).issubset(cols) for prev in result):
                continue
            tuples = [tuple(r[c] for c in cols) for r in rows]
            if all(all(part != '' for part in t) for t in tuples) and len(set(tuples)) == len(rows):
                result.append(list(cols))
    return result


@dataclass(frozen=True)
class Contract:
    left_key: str
    right_key: str
    fact_id: str
    measure: str
    expect_one_per_fact: bool = True
    require_dimension_match: bool = False
    candidate: str = 'raw_join'  # explicit SQL template; never inferred from arbitrary SQL


def _hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(left_csv: str | pathlib.Path, right_csv: str | pathlib.Path,
            contract: Contract, *, max_issues: int = 20) -> dict:
    left_path, right_path = pathlib.Path(left_csv), pathlib.Path(right_csv)
    lf, facts = _read_csv(left_path)
    rf, dims = _read_csv(right_path)
    for col in (contract.left_key, contract.fact_id, contract.measure):
        if col not in lf:
            raise PreflightError(f'missing left column: {col}')
    if contract.right_key not in rf:
        raise PreflightError(f'missing right column: {contract.right_key}')
    if contract.candidate not in ('raw_join','preaggregated_join'):
        raise PreflightError('unsupported candidate SQL template')
    if type(contract.expect_one_per_fact) is not bool or type(contract.require_dimension_match) is not bool:
        raise PreflightError('grain and match policy must be bool')
    ids = [f[contract.fact_id] for f in facts]
    if len(set(ids)) != len(ids) or any(not x for x in ids):
        raise PreflightError('fact_id must be non-null and unique to establish row grain')
    if any(not f[contract.left_key] for f in facts) or any(not d[contract.right_key] for d in dims):
        raise PreflightError('empty join keys not in supported domain')
    amounts = [_integer(f[contract.measure]) for f in facts]
    if sum(map(abs,amounts)) * max(len(dims),1) > 2**62:
        raise PreflightError('aggregate may exceed 64-bit SQLite integer range')
    counts = collections.Counter(d[contract.right_key] for d in dims)
    match_counts = [counts[f[contract.left_key]] for f in facts]
    factors = [max(1,n) for n in match_counts]
    before = sum(amounts)
    after = sum(n*v for n,v in zip(factors,amounts))
    projected = sum(factors)
    issues = [dict(fact_id=ids[i], join_key=facts[i][contract.left_key],
                   right_matches=match_counts[i], surviving_copies=factors[i],
                   amount=amounts[i], duplicated_amount=(factors[i]-1)*amounts[i])
              for i in range(len(facts)) if factors[i] != 1]
    issues.sort(key=lambda r:(-abs(r['duplicated_amount']),r['fact_id']))
    missing = [ids[i] for i, n in enumerate(match_counts) if n == 0]

    # The actual SQL execution is a separate check from the Counter model.
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE f (_fid TEXT PRIMARY KEY, _k TEXT NOT NULL, _v INTEGER NOT NULL)')
        db.execute('CREATE TABLE d (_k TEXT NOT NULL)')
        db.executemany('INSERT INTO f VALUES(?,?,?)',
                       [(fid, row[contract.left_key], v) for fid,row,v in zip(ids,facts,amounts)])
        db.executemany('INSERT INTO d VALUES(?)', [(d[contract.right_key],) for d in dims])
        sql = ('SELECT COUNT(*), COALESCE(SUM(f._v),0) FROM f '
               'LEFT JOIN d ON f._k=d._k')
        actual_rows,actual_sum=db.execute(sql).fetchone()
        one = db.execute('''WITH d_agg AS (SELECT _k, COUNT(*) AS n FROM d GROUP BY _k)
                           SELECT COUNT(*), COALESCE(SUM(f._v),0) FROM f
                           LEFT JOIN d_agg ON f._k=d_agg._k''').fetchone()
    finally:
        db.close()
    if (actual_rows,actual_sum)!=(projected,after) or one!=(len(facts),before):
        raise PreflightError('independent SQL execution disagreed with predicted grain')
    candidate_factors = factors if contract.candidate == 'raw_join' else [1]*len(facts)
    candidate_issues = issues if contract.candidate == 'raw_join' else []
    candidate_rows, candidate_amount = ((actual_rows,actual_sum) if contract.candidate == 'raw_join' else one)
    status = ('BLOCK_GRAIN_REGRESSION' if candidate_issues else 'PASS_GRAIN_CONTRACT') if contract.expect_one_per_fact else 'INFORMATIONAL_NO_GRAIN_CONTRACT'
    if missing and contract.require_dimension_match:
        status = 'BLOCK_MISSING_REFERENCE' if status == 'PASS_GRAIN_CONTRACT' else ('BLOCK_MULTIPLE_CONTRACTS' if status == 'BLOCK_GRAIN_REGRESSION' else 'BLOCK_MISSING_REFERENCE')
    return {
      'schema_version':1,'status':status,'declaration':{'fact_id':contract.fact_id,
        'left_key':contract.left_key,'right_key':contract.right_key,
        'measure':contract.measure,'expect_one_per_fact':contract.expect_one_per_fact,
        'require_dimension_match':contract.require_dimension_match,'candidate':contract.candidate},
      'source':{'left_csv':str(left_path),'right_csv':str(right_path),
        'left_sha256':_hash(left_path),'right_sha256':_hash(right_path)},
      'observed_keys':{'left':candidate_keys(lf,facts),'right':candidate_keys(rf,dims)},
      'statistics':{'fact_rows':len(facts),'dimension_rows':len(dims),
        'naive_join_rows':projected,'preaggregated_join_rows':len(facts),
        'candidate_join_rows':candidate_rows,'candidate_join_measure':candidate_amount,
        'fact_measure_before':before,'naive_join_measure':after,
        'preaggregated_join_measure':before,'naive_measure_delta':after-before,
        'unsafe_fact_rows':len(candidate_issues),'raw_source_fanout_rows':len(issues),
        'missing_reference_rows':len(missing),
        'max_join_fanout':max(factors),
        'independent_sql_execution':'VERIFIED'},
      'affected':candidate_issues[:max_issues], 'raw_join_affected':issues[:max_issues],'missing_reference_ids':missing[:max_issues],
      'full_affected_count':len(candidate_issues),
      'sql':{
        'unsafe_pattern':'SELECT f.fact_id, f.measure FROM fact f LEFT JOIN dimension d ON f.key=d.key',
        'safe_rewrite': 'WITH dimension_at_join_grain AS (SELECT key, COUNT(*) AS child_rows '
                        'FROM dimension GROUP BY key) SELECT f.fact_id, f.measure, '
                        'COALESCE(d.child_rows,0) AS child_rows FROM fact f LEFT JOIN '
                        'dimension_at_join_grain d ON f.key=d.key',
        'note':'SQL is a parameterized design template. Map fact/dimension/columns to real tables; '
               'aggregation of child measures must match actual business intent.'},
      'limits':['observed keys are data-dependent, not proven functional dependencies',
                'LEFT JOIN with no WHERE postfilter, exact integer SUM; no arbitrary SQL parser',
                'unsafe only if consumer declares one-result-row-per-fact invariant',
                'this verifies a proposed unsafe rewrite, not an upstream repository defect']}



def diagnostic_sql(left_table: str, right_table: str, contract: Contract) -> str:
    """Produce an executable read-only SQLite query for external raw tables.

    The query checks *the declared proposed join*, not an arbitrary SQL model.
    """
    lt, rt = _ident(left_table), _ident(right_table)
    lid,lk,rk,measure=map(_ident,(contract.fact_id,contract.left_key,contract.right_key,contract.measure))
    multiplier = 'CASE WHEN m.matches IS NULL OR m.matches=0 THEN 1 ELSE m.matches END'
    if contract.candidate == 'preaggregated_join':
        multiplier = '1'
    return (f'WITH matches AS (SELECT {rk} AS join_key, COUNT(*) AS matches FROM {rt} GROUP BY {rk}),\n'
            f'contributions AS (SELECT f.{lid} AS fact_id, f.{lk} AS join_key, '
            f'COALESCE(m.matches,0) AS right_matches, {multiplier} AS copies, '
            f'f.{measure} AS amount FROM {lt} AS f '
            f'LEFT JOIN matches AS m ON f.{lk}=m.join_key)\n'
            'SELECT fact_id, join_key, right_matches, copies, amount, '
            '(copies-1)*amount AS duplicated_amount FROM contributions '
            'ORDER BY fact_id;\n')


def verify_readonly_database(database: str | pathlib.Path, left_table: str,
                             right_table: str, contract: Contract, expected: dict) -> dict:
    """Independent read-only SQL evaluation; rejects mismatched CSV vs DB sources."""
    path=pathlib.Path(database).resolve()
    if not path.is_file():
        raise PreflightError('SQLite database does not exist')
    sql=diagnostic_sql(left_table,right_table,contract)
    uri='file:'+quote(str(path),safe='/')+'?mode=ro'
    db=sqlite3.connect(uri,uri=True,timeout=3)
    try:
        steps=[0]
        # Progress handler limits SQLite VM instruction steps (~20 million).
        def limit():
            steps[0]+=1
            return int(steps[0]>2000)
        db.set_progress_handler(limit,10000)
        rows=db.execute(sql).fetchall()
    except sqlite3.Error as exc:
        raise PreflightError(f'SQLite read-only validation failed: {exc}') from exc
    finally:
        db.close()
    if len(rows)>MAX_ROWS:
        raise PreflightError('live database fact row limit exceeded')
    expected_summary=expected['statistics']
    actual_sum=sum(int(row[4])*int(row[3]) for row in rows)
    actual_unsafe=sum(int(row[3])!=1 for row in rows)
    actual_missing=sum(int(row[2])==0 for row in rows)
    want=(expected_summary['fact_rows'],expected_summary['candidate_join_measure'],
          expected_summary['unsafe_fact_rows'],expected_summary['missing_reference_rows'])
    have=(len(rows),actual_sum,actual_unsafe,actual_missing)
    if want!=have:
        raise PreflightError(f'DB source mismatch / changed inputs: expected {want}, observed {have}')
    return {'status':'READONLY_DATABASE_AGREES','database':str(path),
            'fact_rows':have[0],'candidate_measure':have[1],
            'diagnostic_sql':sql,'evaluated_rows':len(rows)}


def _cmd(argv=None) -> int:
    p=argparse.ArgumentParser(description='AxiomForge data-grain SQL change preflight')
    p.add_argument('--left',required=True)
    p.add_argument('--right',required=True)
    p.add_argument('--left-key',default='sku')
    p.add_argument('--right-key',default='sku')
    p.add_argument('--fact-id',default='sku')
    p.add_argument('--measure',default='price')
    p.add_argument('--out')
    p.add_argument('--database',help='optional pre-existing SQLite database, read-only')
    p.add_argument('--left-table',default='raw_products')
    p.add_argument('--right-table',default='raw_supplies')
    p.add_argument('--sql-out',help='write executable read-only SQL for the declared table names')
    p.add_argument('--informational',action='store_true',help='do not assert one result row per fact')
    p.add_argument('--require-match',action='store_true',help='fail when any fact has no dimension match')
    p.add_argument('--candidate',choices=('raw_join','preaggregated_join'),default='raw_join',
                   help='explicit candidate JOIN template; does not parse arbitrary SQL')
    ns=p.parse_args(argv)
    result=analyze(ns.left,ns.right,Contract(ns.left_key,ns.right_key,ns.fact_id,ns.measure,not ns.informational,ns.require_match,ns.candidate))
    sql=diagnostic_sql(ns.left_table,ns.right_table,Contract(ns.left_key,ns.right_key,ns.fact_id,ns.measure,not ns.informational,ns.require_match,ns.candidate))
    if ns.sql_out:
        file=pathlib.Path(ns.sql_out);file.parent.mkdir(parents=True,exist_ok=True);file.write_text(sql,encoding='utf-8')
    if ns.database:
        result['live_database']=verify_readonly_database(ns.database,ns.left_table,ns.right_table,
            Contract(ns.left_key,ns.right_key,ns.fact_id,ns.measure,not ns.informational,ns.require_match,ns.candidate),result)
    payload=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)
    if ns.out:
        file=pathlib.Path(ns.out);file.parent.mkdir(parents=True,exist_ok=True);file.write_text(payload+'\n',encoding='utf-8')
    print(payload)
    return 2 if result['status'].startswith('BLOCK_') else 0


def main():
    try:
        raise SystemExit(_cmd())
    except (PreflightError, OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        print('PRECHECK_ERROR:',exc,file=sys.stderr)
        raise SystemExit(3)

if __name__=='__main__':
    main()
