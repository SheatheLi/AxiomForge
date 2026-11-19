"""Read-only, issue-driven incremental delete/merge preflight on SQLite snapshots.

Validates the *effect* of a candidate DELETE against tuple-key membership; not a
Trino/Redshift/Snowflake/dbt emulator. Multiple different external issue families
are represented by independently reproducible SQLite semantic counterexamples.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

ISSUES = {
    'composite': 'https://github.com/dbt-labs/dbt-core/issues/11799',
    'null': 'https://github.com/dbt-labs/dbt-core/issues/5691',
    'source_collision': 'https://github.com/dbt-labs/dbt-core/issues/10432',
    'partition': 'https://github.com/dbt-labs/dbt-core/issues/11397',
}
MAX_BYTES = 64_000_000
MAX_ROWS = 50_000

class IncrementalError(ValueError):
    pass


def _ident(s: str) -> str:
    if not isinstance(s, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,127}', s):
        raise IncrementalError(f'invalid SQLite identifier: {s!r}')
    return '"' + s + '"'


def _require_tables(conn: sqlite3.Connection, target: str, stage: str, keys: list[str]) -> None:
    if target == stage:
        raise IncrementalError('target and stage must differ')
    for table in (target, stage):
        obj = conn.execute('SELECT type,sql FROM sqlite_master WHERE name=?', (table,)).fetchone()
        if not obj or obj[0] != 'table' or not (obj[1] or '').upper().startswith('CREATE TABLE'):
            raise IncrementalError(f'only ordinary SQLite tables are supported: {table}')
        ddl = (obj[1] or '').upper()
        if 'COLLATE NOCASE' in ddl or 'COLLATE RTRIM' in ddl:
            raise IncrementalError('non-BINARY collations are not yet supported')
        if 'WITHOUT ROWID' in ddl or 'VIRTUAL TABLE' in ddl:
            raise IncrementalError(f'ROWID table required: {table}')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchone():
            raise IncrementalError(f'triggers are not supported on {table}')
        columns = {r[1] for r in conn.execute(f'PRAGMA table_xinfo({_ident(table)})')}
        if '_rowid_' in {str(x).lower() for x in columns}:
            raise IncrementalError(f'shadowed physical row ID: {table}')
        for k in keys:
            if k not in columns:
                raise IncrementalError(f'key {k!r} absent from {table}')


def _items(conn: sqlite3.Connection, table: str, keys: list[str]) -> list[tuple[int, tuple]]:
    sql = 'SELECT _rowid_,' + ','.join(_ident(c) for c in keys) + ' FROM ' + _ident(table)
    data = conn.execute(sql).fetchmany(MAX_ROWS + 1)
    if len(data) > MAX_ROWS:
        raise IncrementalError(f'table {table} exceeds {MAX_ROWS} rows; refuses unbounded scan')
    if any(type(row[0]) is not int for row in data):
        raise IncrementalError('expected physical integer row IDs')
    return [(row[0], tuple(row[1:])) for row in data]


def _present(k: tuple) -> list[str | int | float | None]:
    return list(k)


def _source_key_counts(rows: list[tuple[int, tuple]]) -> Counter:
    return Counter(k for _, k in rows)


def _authorizer(target: str):
    read = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, 'SQLITE_RECURSIVE'):
        read.add(sqlite3.SQLITE_RECURSIVE)
    def auth(op, arg1, arg2, db, src):
        if op in read:
            if op == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {
                'random', 'randomblob', 'load_extension', 'changes', 'total_changes',
                'last_insert_rowid', 'datetime', 'date', 'time', 'strftime',
                'unixepoch', 'julianday', 'sqlite_version', 'sqlite_source_id',
            }:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if op == sqlite3.SQLITE_DELETE and arg1 == target and src is None:
            return sqlite3.SQLITE_OK
        if op == sqlite3.SQLITE_TRANSACTION:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return auth


def _run_candidate(db: sqlite3.Connection, sql: str, target: str) -> set[int]:
    """Temporarily execute the real candidate inside a nested savepoint.

    Works even when called by a separate outer reduction savepoint.  The only
    durable database involved is an in-memory copy of the read-only source.
    """
    before = {x[0] for x in db.execute(f'SELECT _rowid_ FROM {_ident(target)}')}
    db.execute('SAVEPOINT axiom_candidate_eval')
    try:
        db.set_progress_handler(lambda: 1 if time.monotonic() > end else 0, 1000)
        db.set_authorizer(_authorizer(target))
        end = time.monotonic() + 8.0
        try:
            db.execute(sql)
        finally:
            db.set_authorizer(None)
            db.set_progress_handler(None, 0)
        after = {x[0] for x in db.execute(f'SELECT _rowid_ FROM {_ident(target)}')}
        return before - after
    except sqlite3.Error as ex:
        raise IncrementalError(f'candidate DELETE refused or failed: {ex}') from ex
    finally:
        db.set_authorizer(None)
        db.set_progress_handler(None, 0)
        db.execute('ROLLBACK TO SAVEPOINT axiom_candidate_eval')
        db.execute('RELEASE SAVEPOINT axiom_candidate_eval')


def _assess(db: sqlite3.Connection, *, target: str, stage: str, keys: list[str],
            delete_sql: str, grain: str = 'row', null_safe: bool = False,
            sample: int = 8) -> dict:
    if grain not in {'row', 'partition'}:
        raise IncrementalError('grain must be row or partition')
    if not 1 <= len(keys) <= 8 or len(set(keys)) != len(keys):
        raise IncrementalError('supply 1–8 distinct key columns')
    for s in [target, stage, *keys]:
        _ident(s)
    _require_tables(db, target, stage, keys)
    source_aff = {r[1]: str(r[2]).upper() for r in db.execute(f'PRAGMA table_xinfo({_ident(target)})')}
    stage_aff = {r[1]: str(r[2]).upper() for r in db.execute(f'PRAGMA table_xinfo({_ident(stage)})')}
    if any(source_aff[k] != stage_aff[k] for k in keys):
        raise IncrementalError('key columns must have matching declared SQLite types and affinity')
    candidate = delete_sql.strip().rstrip(';').strip()
    prefix = re.match(r'^DELETE\s+FROM\s+(["A-Za-z_0-9]+)', candidate, flags=re.IGNORECASE)
    if not prefix or prefix.group(1).strip('"') != target:
        raise IncrementalError('one plain DELETE FROM the declared target table is required')
    # Semicolons and multiple statements disallowed. SQLite executes exactly one statement.
    if ';' in candidate or re.search(r'\b(RETURNING|COLLATE)\b', candidate, flags=re.IGNORECASE):
        raise IncrementalError('multi-statement or RETURNING SQL is unsupported')
    existing, incoming = _items(db, target, keys), _items(db, stage, keys)
    for _, row in existing + incoming:
        for value in row:
            if value is not None and type(value) not in (int, float, str):
                raise IncrementalError('unsupported key value type')
    duplicates_stage = _source_key_counts(incoming)
    duplicates_target = _source_key_counts(existing)
    null_old = [(rid, key) for rid, key in existing if None in key]
    null_new = [(rid, key) for rid, key in incoming if None in key]
    source_keys = {k for _, k in incoming if null_safe or None not in k}
    expected = {rid for rid, key in existing if key in source_keys and (null_safe or None not in key)}
    deleted = _run_candidate(db, candidate, target)
    excess, missed = deleted - expected, expected - deleted
    target_map = dict(existing)
    source_collisions = [(key, count) for key, count in duplicates_stage.items() if count > 1]
    target_collisions = [(key, count) for key, count in duplicates_target.items() if count > 1]
    null_pairs = [k for _, k in incoming if None in k and k in duplicates_target]
    source_target_multiple = [(key, count) for key,count in source_collisions if key in duplicates_target]
    problems: list[str] = []
    if excess:
        problems.append('UNINTENDED_DELETE')
    if missed:
        problems.append('MISSED_DELETE')
    if (null_new or null_old) and not null_safe:
        problems.append('NULL_KEY')
    if grain == 'row' and source_collisions:
        problems.append('STAGE_KEY_COLLISION')
    if grain == 'row' and target_collisions:
        problems.append('TARGET_KEY_COLLISION')
    if grain == 'row' and source_target_multiple:
        problems.append('MULTIPLE_SOURCE_MATCH')
    # Distinguish matching contract from upsert behavior. A null-safe opt-in
    # changes expected delete semantics but does not assert adapter equivalence.
    if null_pairs and not null_safe:
        problems.append('NULL_MATCH_GAP')
    status = 'RISK' if problems else 'PASS'
    predicates = ' AND '.join(f's.{_ident(k)} {"IS" if null_safe else "="} t.{_ident(k)}' for k in keys)
    repair = f'DELETE FROM {_ident(target)} AS t WHERE EXISTS (SELECT 1 FROM {_ident(stage)} AS s WHERE {predicates})'
    return {
        'schema': 'axiomforge.incremental_preflight.v1', 'status': status,
        'scope': 'SQLite semantic reproduction; NOT dbt adapter execution',
        'contract': {'target': target, 'stage': stage, 'keys': keys, 'grain': grain, 'null_safe': null_safe},
        'counts': {'target_rows': len(existing), 'stage_rows': len(incoming),
                   'expected_deletes': len(expected), 'actual_deletes': len(deleted),
                   'unintended_deletes': len(excess), 'missed_deletes': len(missed),
                   'null_target_rows': len(null_old), 'null_stage_rows': len(null_new),
                   'duplicate_stage_keys': len(source_collisions),
                   'duplicate_target_keys': len(target_collisions)},
        'flags': problems,
        'examples': {
            'unintended': [{'rowid': i, 'key': _present(target_map[i])} for i in sorted(excess)[:sample]],
            'missed': [{'rowid': i, 'key': _present(target_map[i])} for i in sorted(missed)[:sample]],
            'stage_collisions': [{'key': _present(k), 'rows': n} for k, n in source_collisions[:sample]],
            'target_collisions': [{'key': _present(k), 'rows': n} for k, n in target_collisions[:sample]],
            'multiple_source_matches': [{'key': _present(k), 'source_rows': n, 'target_rows': duplicates_target[k]} for k,n in source_target_multiple[:sample]],
            'nullable_stage': [{'rowid': i, 'key': _present(k)} for i, k in null_new[:sample]],
            'null_match_pairs': [{'key': _present(k)} for k in dict.fromkeys(null_pairs)][:sample],
        },
        'safe_tuple_delete_example': repair,
        'issue_families': ISSUES,
    }


def inspect_database(database: Path | str, *, target: str, stage: str, keys: list[str],
                     delete_sql: str, grain: str = 'row', null_safe: bool = False) -> dict:
    database = Path(database).resolve()
    if not database.is_file() or database.stat().st_size > MAX_BYTES:
        raise IncrementalError('database not found or exceeds 64 MB bound')
    # Open explicitly read-only; original bytes never mutated by the engine.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
        snapshot = sqlite3.connect(':memory:')
        try:
            source.backup(snapshot)
            report = _assess(snapshot, target=target, stage=stage, keys=keys,
                             delete_sql=delete_sql, grain=grain, null_safe=null_safe)
        finally:
            snapshot.close()
    report['input'] = {'sqlite_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
                       'candidate_sha256': hashlib.sha256(delete_sql.encode('utf8')).hexdigest()}
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Preflight incremental delete safety on a SQLite snapshot')
    p.add_argument('--database', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--stage', required=True)
    p.add_argument('--keys', required=True, help='comma separated exact composite key columns')
    p.add_argument('--delete-file', required=True, help='one candidate SQLite DELETE FROM target statement')
    p.add_argument('--grain', choices=['row', 'partition'], default='row')
    p.add_argument('--null-safe', action='store_true', help='opt in to SQLite IS null equality in expected deletion')
    p.add_argument('--out', help='JSON report; must not already exist')
    args = p.parse_args(argv)
    try:
        query = Path(args.delete_file).read_text('utf8')
        if len(query.encode('utf8')) > 100_000:
            raise IncrementalError('SQL file exceeds 100 KB')
        report = inspect_database(args.database, target=args.target, stage=args.stage,
            keys=[k.strip() for k in args.keys.split(',')], delete_sql=query,
            grain=args.grain, null_safe=args.null_safe)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
        if args.out:
            with open(args.out, 'x', encoding='utf8') as f:
                f.write(encoded)
        print(encoded)
        return 0 if report['status'] == 'PASS' else 2
    except (OSError, IncrementalError, UnicodeError, sqlite3.Error) as ex:
        print('AxiomForge incremental ERROR: ' + str(ex), file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
