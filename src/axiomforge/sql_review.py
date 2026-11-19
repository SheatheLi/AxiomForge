"""Read-only, fact-grain regression review of real SQLite SELECT statements.

User supplies the invariant: one result row per original fact, with the same
integer measure. This module does not infer SQL semantics or business intent.
An explicit invariant and a known fact identity make errors observable even
when row count or global sums cancel.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import collections
import hashlib
import html
import json
import pathlib
import re
import sqlite3
import sys
import time

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_SQL_BYTES = 250_000
MAX_DB_BYTES = 512_000_000
DEFAULT_MAX_ROWS = 100_000
MAX_ABS_MEASURE = 10**12
VM_STEP_BUDGET = 40_000_000
TIME_BUDGET_SECONDS = 25
REPORT_SCHEMA = "axiomforge.sql_review.v1"


class ReviewError(ValueError):
    """Input rejected or execution inconclusive; never silently PASS."""


def _ident(value: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ReviewError(f"SQL identifier rejected (letters/digits/underscore only): {value!r}")
    return '"' + value + '"'


def _sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _read_sql(path: pathlib.Path) -> str:
    if not path.is_file() or path.stat().st_size > MAX_SQL_BYTES:
        raise ReviewError('SQL file missing or exceeds 250 KB')
    sql = path.read_text(encoding='utf-8-sig').strip().rstrip(';').strip()
    if not re.match(r'^(SELECT|WITH)\b', sql, re.I):
        raise ReviewError('SQL must start with SELECT or WITH (no multi-statement, DDL or DML)')
    return sql


def _normalize_id(value: object) -> str:
    if type(value) not in (str, int) or str(value) == '':
        raise ReviewError('fact identity must be nonempty TEXT or INTEGER')
    return str(value)


def _amount(value: object) -> int:
    if type(value) is not int or abs(value) > MAX_ABS_MEASURE:
        raise ReviewError('measures must be SQLite INTEGER in the supported range')
    return value


def _connection(path: pathlib.Path) -> sqlite3.Connection:
    path = path.resolve()
    if not path.is_file() or path.stat().st_size > MAX_DB_BYTES:
        raise ReviewError('SQLite database missing or exceeds 512 MB')
    uri = path.as_uri() + '?mode=ro'
    try:
        db = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.Error as exc:
        raise ReviewError(f'cannot open SQLite read-only: {exc}') from exc
    db.execute('PRAGMA query_only = ON')
    db.execute('BEGIN')  # one consistent read snapshot for source, before and after
    if hasattr(db, 'enable_load_extension'):
        db.enable_load_extension(False)
    started = time.monotonic()
    count = [0]

    def limiter():
        count[0] += 1000
        return int(count[0] > VM_STEP_BUDGET or time.monotonic() - started > TIME_BUDGET_SECONDS)
    db.set_progress_handler(limiter, 1000)

    allowed_ops = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, 'SQLITE_RECURSIVE'):
        allowed_ops.add(sqlite3.SQLITE_RECURSIVE)
    deny_functions = {'load_extension', 'random', 'randomblob', 'changes', 'total_changes',
                      'last_insert_rowid', 'sqlite_version', 'sqlite_source_id', 'date',
                      'time', 'datetime', 'julianday', 'strftime', 'unixepoch'}

    def authorizer(op, _first, second, _db_name, _trigger):
        if op not in allowed_ops:
            return sqlite3.SQLITE_DENY
        if op == sqlite3.SQLITE_FUNCTION and str(second).lower() in deny_functions:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorizer)
    return db


def _read_rows(db: sqlite3.Connection, sql: str, id_col: str, measure_col: str,
               max_rows: int) -> list[tuple[str, int]]:
    try:
        cursor = db.execute(sql)
        columns = [x[0] for x in cursor.description or []]
        if columns.count(id_col) != 1 or columns.count(measure_col) != 1 or id_col == measure_col:
            raise ReviewError(f'query must emit exactly one {id_col!r} and {measure_col!r} column')
        id_pos, amount_pos = columns.index(id_col), columns.index(measure_col)
        result: list[tuple[str, int]] = []
        while True:
            batch = cursor.fetchmany(min(1000, max_rows + 1 - len(result)))
            if not batch:
                break
            for row in batch:
                result.append((_normalize_id(row[id_pos]), _amount(row[amount_pos])))
            if len(result) > max_rows:
                raise ReviewError(f'SQL result exceeds --max-rows {max_rows}; no PASS permitted')
        return result
    except sqlite3.Error as exc:
        raise ReviewError(f'read-only query rejected or failed: {exc}') from exc


def _fingerprint_rows(rows: list[tuple[str, int]]) -> str:
    # Order independent, but keeps duplicates and different integer amounts.
    payload = json.dumps(sorted(rows), ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _evaluate(rows: list[tuple[str, int]], baseline: dict[str, int], *, limit: int) -> dict:
    sums = collections.defaultdict(int)
    counts = collections.Counter()
    for ident, amount in rows:
        sums[ident] += amount
        counts[ident] += 1
    missing, duplicated, changed, unexpected = [], [], [], []
    for ident, amount in baseline.items():
        n = counts[ident]
        if n == 0:
            missing.append(ident)
        if n > 1:
            duplicated.append(ident)
        if n and sums[ident] != amount:
            changed.append(ident)
    unexpected = sorted(set(sums).difference(baseline))
    issues = []
    for ident in sorted(set(missing + duplicated + changed + unexpected)):
        is_fact = ident in baseline
        expected = baseline.get(ident)
        issues.append({'fact_id': ident, 'expected_rows': 1 if is_fact else 0,
                       'actual_rows': counts[ident], 'expected_measure': expected,
                       'actual_measure': sums[ident],
                       'measure_delta': sums[ident] - (expected or 0),
                       'flags': [flag for flag, condition in (
                           ('MISSING', ident in missing),
                           ('DUPLICATE', ident in duplicated),
                           ('MEASURE_CHANGED', ident in changed),
                           ('UNEXPECTED_ID', ident in unexpected)) if condition]})
    # Sort by business impact while preserving deterministic order.
    issues.sort(key=lambda x: (-abs(x['measure_delta']), x['fact_id']))
    correct = not issues
    return {
        'status': 'PASS' if correct else 'BLOCK',
        'counts': {'baseline_facts': len(baseline), 'result_rows': len(rows),
                   'missing_facts': len(missing), 'duplicated_facts': len(duplicated),
                   'changed_measure_facts': len(changed), 'unexpected_result_ids': len(unexpected),
                   'affected_id_count': len(issues)},
        'measures': {'baseline_total': sum(baseline.values()),
                     'result_total': sum(sums.values()),
                     'total_delta': sum(sums.values()) - sum(baseline.values()),
                     'sum_abs_per_id_delta': sum(abs(x['measure_delta']) for x in issues)},
        'result_rows_sha256': _fingerprint_rows(rows),
        'affected': issues[:limit],
        'affected_truncated': len(issues) > limit,
        # Do not infer provenance from opaque SQL. Explicit output IDs are required.
        'limits': ['explicit once-per-fact and unchanged-measure contract',
                   'SQLite SELECT only; not PostgreSQL/dbt dialect semantics',
                   'no proof of business correctness or original SQL causality',
                   'aggregate identities must be retained in query output'],
    }


def inspect(database: str | pathlib.Path, fact_table: str, fact_id: str, measure: str,
            sql_files: list[str | pathlib.Path], *, result_id='fact_id',
            result_measure='amount', max_rows=DEFAULT_MAX_ROWS, issue_limit=50) -> dict:
    if not (1 <= max_rows <= 1_000_000 and 1 <= issue_limit <= 500):
        raise ReviewError('row and issue limits out of range')
    if len(sql_files) not in (1, 2):
        raise ReviewError('inspect requires one candidate or old + new SQL files')
    for name in (fact_table, fact_id, measure, result_id, result_measure):
        _ident(name)
    database = pathlib.Path(database).resolve()
    queries = [pathlib.Path(p).resolve() for p in sql_files]
    texts = [_read_sql(path) for path in queries]
    with closing(_connection(database)) as db:
        source_sql = f'SELECT {_ident(fact_id)} AS "__id", {_ident(measure)} AS "__amount" FROM {_ident(fact_table)}'
        source = _read_rows(db, source_sql, '__id', '__amount', max_rows)
        baseline: dict[str, int] = {}
        for ident, amount in source:
            if ident in baseline:
                raise ReviewError(f'fact table identity is not unique: {ident!r}')
            baseline[ident] = amount
        if not baseline:
            raise ReviewError('fact table is empty; cannot establish contract')
        evaluations = []
        for sql in texts:
            rows = _read_rows(db, sql, result_id, result_measure, max_rows)
            evaluations.append(_evaluate(rows, baseline, limit=issue_limit))
    if len(evaluations) == 1:
        state = evaluations[0]['status']
        comparison = None
    else:
        old, new = evaluations
        old_faults = {x['fact_id']: (x['actual_rows'], x['measure_delta'], tuple(x['flags']))
                      for x in old['affected']}
        new_faults = {x['fact_id']: (x['actual_rows'], x['measure_delta'], tuple(x['flags']))
                      for x in new['affected']}
        # Counts are authoritative even when issue details are truncated.
        if new['status'] == 'PASS':
            state = 'PASS' if old['status'] == 'PASS' else 'IMPROVED'
        elif old['status'] == 'PASS':
            state = 'REGRESSION'
        else:
            state = 'STILL_FAILING'
        comparison = {'status': state,
                      'before_affected': old['counts']['affected_id_count'],
                      'after_affected': new['counts']['affected_id_count'],
                      'new_affected_ids_in_visible_sample': sorted(set(new_faults) - set(old_faults))[:issue_limit],
                      'resolved_ids_in_visible_sample': sorted(set(old_faults) - set(new_faults))[:issue_limit],
                      'warning': 'before/after ID sets are based on visible samples only' if old['affected_truncated'] or new['affected_truncated'] else None}
    return {
        'schema': REPORT_SCHEMA,
        'status': state,
        'contract': {'fact_table': fact_table, 'fact_id': fact_id, 'measure': measure,
                     'result_id': result_id, 'result_measure': result_measure,
                     'max_rows': max_rows, 'issue_limit': issue_limit,
                     'invariant': 'one query result row per baseline fact with the identical integer measure'},
        'inputs': {'database': str(database), 'database_sha256': _sha(database),
                   'sql_files': [{'path': str(path), 'sha256': _sha(path)} for path in queries]},
        'evaluations': evaluations,
        'comparison': comparison,
        'basis': 'one declared fact table; queries actually executed in read-only SQLite',
    }


def _canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def save_receipt(path: pathlib.Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {'payload': report, 'digest': hashlib.sha256(_canonical(report)).hexdigest()}
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')


def verify(path: str | pathlib.Path) -> dict:
    path = pathlib.Path(path)
    envelope = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(envelope, dict) or set(envelope) != {'payload', 'digest'}:
        raise ReviewError('invalid receipt structure')
    previous = envelope['payload']
    if hashlib.sha256(_canonical(previous)).hexdigest() != envelope['digest']:
        raise ReviewError('receipt digest is invalid')
    if previous.get('schema') != REPORT_SCHEMA:
        raise ReviewError('unsupported receipt schema')
    contract, inputs = previous['contract'], previous['inputs']
    files = [s['path'] for s in inputs['sql_files']]
    current = inspect(inputs['database'], contract['fact_table'], contract['fact_id'],
                      contract['measure'], files, result_id=contract['result_id'],
                      result_measure=contract['result_measure'], max_rows=contract['max_rows'],
                      issue_limit=contract['issue_limit'])
    if current != previous:
        raise ReviewError('receipt mismatch: inputs or computed results have changed')
    return {'status': 'REPLAY_VERIFIED', 'review_status': current['status'],
            'database_sha256': inputs['database_sha256'], 'sql_files': len(files)}


def make_html(path: pathlib.Path, report: dict) -> None:
    def e(s):
        return html.escape(str(s), quote=True)
    pages = []
    for label, ev in zip(('Candidate',) if len(report['evaluations']) == 1 else ('Before', 'After'),
                         report['evaluations']):
        counts, amounts = ev['counts'], ev['measures']
        row_html = ''.join('<tr>' + ''.join('<td>' + e(v) + '</td>' for v in
                           (item['fact_id'], item['actual_rows'], item['expected_measure'],
                            item['actual_measure'], item['measure_delta'], ', '.join(item['flags']))) + '</tr>'
                           for item in ev['affected'])
        pages.append(f'<section><h2>{label}: {e(ev["status"])}</h2><p>Facts: {counts["baseline_facts"]}; '
                     f'Query rows: {counts["result_rows"]}; affected IDs: {counts["affected_id_count"]}; '
                     f'missing: {counts["missing_facts"]}; duplicate: {counts["duplicated_facts"]}; '
                     f'measure changed: {counts["changed_measure_facts"]}; unexpected: {counts["unexpected_result_ids"]}.</p>'
                     f'<p>Baseline sum: {amounts["baseline_total"]}; query sum: {amounts["result_total"]}; '
                     f'net delta: {amounts["total_delta"]}; sum of absolute per-ID deltas: {amounts["sum_abs_per_id_delta"]}.</p>'
                     '<table><thead><tr><th>ID</th><th>Rows</th><th>Expected</th><th>Observed</th><th>Delta</th><th>Flags</th></tr></thead><tbody>'
                     + row_html + '</tbody></table>' + ('<p>Issue rows truncated.</p>' if ev['affected_truncated'] else '') + '</section>')
    body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>AxiomForge SQL Review</title><style>body{font:16px/1.55 system-ui,sans-serif;max-width:1050px;margin:32px auto;padding:0 20px;color:#172c3b}'
            'h1{border-bottom:3px solid #245a78;padding-bottom:14px}section{margin:28px 0}table{border-collapse:collapse;width:100%;font-size:14px}'
            'td,th{border-bottom:1px solid #ccd7dc;text-align:left;padding:9px;overflow-wrap:anywhere}th{background:#e9f1f5}'
            'p{max-width:950px}code{background:#e8eef1;padding:2px 5px}</style><h1>AxiomForge · SQL Fact-Grain Review</h1>'
            f'<p>Verdict: <strong>{e(report["status"])}</strong></p>' + '\n'.join(pages)
            + '<p>Contract is user-declared. This tool does not infer arbitrary SQL semantics.</p>')
    # Keep an explicit limitations statement visible on offline reports.
    body += '<p>Results are independent of an aggregate-only checksum; no claim of novelty or production correctness.</p></html>'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def _arg_parser():
    p = argparse.ArgumentParser(prog='axiomforge-review', description='read-only SQL fact-grain review and regression')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('check', 'compare'):
        cmd = sub.add_parser(command)
        cmd.add_argument('--database', required=True)
        cmd.add_argument('--fact-table', required=True)
        cmd.add_argument('--fact-id', required=True)
        cmd.add_argument('--measure', required=True)
        cmd.add_argument('--result-id', default='fact_id')
        cmd.add_argument('--result-measure', default='amount')
        cmd.add_argument('--sql', required=command == 'check', help='SELECT file with result aliases')
        cmd.add_argument('--before', required=command == 'compare', help='original SQL file')
        cmd.add_argument('--after', required=command == 'compare', help='proposed SQL file')
        cmd.add_argument('--max-rows', type=int, default=DEFAULT_MAX_ROWS)
        cmd.add_argument('--issue-limit', type=int, default=50)
        cmd.add_argument('--out', required=True, help='JSON receipt')
        cmd.add_argument('--html', help='optional standalone HTML summary')
    sub.add_parser('verify').add_argument('--receipt', required=True)
    return p


def main(argv=None) -> int:
    args = _arg_parser().parse_args(argv)
    try:
        if args.command == 'verify':
            output = verify(args.receipt)
            code = 0
        else:
            paths = [args.sql] if args.command == 'check' else [args.before, args.after]
            report = inspect(args.database, args.fact_table, args.fact_id, args.measure,
                             paths, result_id=args.result_id, result_measure=args.result_measure,
                             max_rows=args.max_rows, issue_limit=args.issue_limit)
            save_receipt(pathlib.Path(args.out), report)
            if args.html:
                make_html(pathlib.Path(args.html), report)
            output = {'status': report['status'], 'counts': report['evaluations'][-1]['counts'],
                      'measures': report['evaluations'][-1]['measures'],
                      'receipt': str(args.out), 'html': args.html}
            code = 0 if report['status'] in ('PASS', 'IMPROVED') else 2
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return code
    except (ReviewError, sqlite3.Error, UnicodeError, OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
        print(json.dumps({'status': 'REJECTED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
