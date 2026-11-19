"""Contract-preserving reduction of multi-table SQLite SQL regressions.

The reducer is *not* an optimizer proof and makes no promise of global minimum.
Its oracle re-evaluates the original good query and the target failure against
real SQLite data after each removal. Only tables explicitly named by the user
are edited, and all mutations are performed in a private in-memory snapshot.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import zipfile

from axiomforge.sql_review import (
    ReviewError, _amount, _evaluate, _ident, _normalize_id, _read_rows,
    _read_sql, _sha, _connection, inspect, MAX_DB_BYTES,
)

SCHEMA = 'axiomforge.sql_reproducer.v1'
MEMBER_NAMES = ('reproducer.sqlite', 'before.sql', 'after.sql', 'witness.json', 'replay.py')
MAX_BUNDLE_BYTES = 64_000_000
MAX_REDUCIBLE_ROWS = 20_000


def _canon(obj: object) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _read_authorizer(op, _first, second, _database, _trigger):
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, 'SQLITE_RECURSIVE'):
        allowed.add(sqlite3.SQLITE_RECURSIVE)
    if op not in allowed:
        return sqlite3.SQLITE_DENY
    if op == sqlite3.SQLITE_FUNCTION and str(second).lower() in {
        'load_extension', 'random', 'randomblob', 'date', 'datetime', 'time',
        'changes', 'total_changes', 'last_insert_rowid', 'strftime', 'julianday',
        'unixepoch', 'sqlite_version', 'sqlite_source_id',
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _oracle(db: sqlite3.Connection, fact_table: str, fact_id: str, measure: str,
            before: str, after: str, target: str, expected_flag: str,
            *, max_rows: int) -> bool:
    """Require before globally correct AND the same target regression flag after."""
    db.set_authorizer(_read_authorizer)
    try:
        source = _read_rows(db, f'SELECT {_ident(fact_id)} AS "__id", '
                              f'{_ident(measure)} AS "__amount" FROM {_ident(fact_table)}',
                            '__id', '__amount', max_rows)
        baseline = {}
        for ident, amount in source:
            if ident in baseline:
                return False
            baseline[ident] = amount
        if target not in baseline:
            return False
        old = _evaluate(_read_rows(db, before, 'fact_id', 'amount', max_rows), baseline, limit=1)
        if old['status'] != 'PASS':
            return False
        new_rows = _read_rows(db, after, 'fact_id', 'amount', max_rows)
        witness = _evaluate([r for r in new_rows if r[0] == target],
                            {target: baseline[target]}, limit=1)
        return any(item['fact_id'] == target and expected_flag in item['flags'] for item in witness['affected'])
    except (ReviewError, sqlite3.Error, OverflowError):
        return False
    finally:
        db.set_authorizer(None)


def _rowids(db: sqlite3.Connection, tables: list[str]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for table in tables:
        if not isinstance(table, str):
            raise ReviewError('table names must be strings')
        ident = _ident(table)
        exists = db.execute("SELECT type, sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if not exists or exists[0] != 'table':
            raise ReviewError(f'not an ordinary base table: {table!r}')
        table_sql=(exists[1] or '').strip().upper()
        if not table_sql.startswith('CREATE TABLE'):
            raise ReviewError(f'virtual or nonstandard SQLite table cannot be reduced: {table}')
        if db.execute('SELECT 1 FROM sqlite_master WHERE type=\'trigger\' AND tbl_name=? LIMIT 1', (table,)).fetchone():
            raise ReviewError(f'table has triggers; cannot safely remove isolated rows: {table}')
        if 'WITHOUT ROWID' in table_sql:
            raise ReviewError(f'WITHOUT ROWID tables are not supported for reduction: {table}')
        declared = {str(x[1]).lower() for x in db.execute(f'PRAGMA table_xinfo({_ident(table)})')}
        if '_rowid_' in declared:
            raise ReviewError(f'shadowed _rowid_ cannot be reduced safely: {table}')
        # Hidden rowid is required to delete a chosen physical row exactly.
        result[table] = [x[0] for x in db.execute(f'SELECT _rowid_ FROM {ident} ORDER BY _rowid_')]
        if any(type(x) is not int for x in result[table]):
            raise ReviewError('table _rowid_ must resolve to SQLite hidden integer rowid')
    return result


def _target_rowids(db: sqlite3.Connection, table: str, fact_id: str, target: str) -> set[int]:
    sql = f'SELECT _rowid_, {_ident(fact_id)} FROM {_ident(table)}'
    return {rowid for rowid, ident in db.execute(sql) if _normalize_id(ident) == target}


def _baseline_target(report: dict, explicit: str | None, flag: str | None) -> tuple[str, str]:
    if report['status'] != 'REGRESSION' or report['evaluations'][0]['status'] != 'PASS':
        raise ReviewError('reduction needs a globally correct before query and a failing after query')
    defects = report['evaluations'][-1]['affected']
    if explicit is not None:
        defects = [x for x in defects if x['fact_id'] == explicit]
    defects = [x for x in defects if x['expected_rows'] == 1]
    if flag:
        defects = [x for x in defects if flag in x['flags']]
    if not defects:
        raise ReviewError('no visible source fact has the requested regression flag; increase issue_limit or choose another target')
    d = defects[0]
    return d['fact_id'], flag or d['flags'][0]


def _reject_unsafe_db(db: sqlite3.Connection) -> None:
    bad = db.execute('PRAGMA foreign_key_check').fetchone()
    if bad:
        raise ReviewError('input database already violates declared foreign keys; reduction refuses ambiguous integrity')
    # Views/triggers may reference removed data; queries still reexecute with original SQL.
    db.execute('PRAGMA foreign_keys=OFF')


class _Engine:
    def __init__(self, db, tables, protected, oracle, *, max_calls, deadline, preserve_fks):
        self.db, self.tables, self.protected, self.oracle = db, tables, protected, oracle
        self.max_calls, self.deadline, self.preserve_fks = max_calls, deadline, preserve_fks
        self.calls = 0
        self.cache_hits = 0
        self.exhausted = False
        self.active = {t: set(rows) for t, rows in tables.items()}
        self.events = []

    def can_call(self):
        if self.calls >= self.max_calls or time.monotonic() >= self.deadline:
            self.exhausted = True
            return False
        return True

    def _trial(self, removal: list[tuple[str, int]]) -> bool:
        if not removal:
            return False
        if not self.can_call():
            return False
        self.db.execute('SAVEPOINT axiom_trial')
        try:
            by_table: dict[str, list[int]] = {}
            for table, rowid in removal:
                by_table.setdefault(table, []).append(rowid)
            for table, ids in by_table.items():
                for pos in range(0, len(ids), 400):
                    chunk = ids[pos:pos + 400]
                    marks = ','.join('?' * len(chunk))
                    self.db.execute(f'DELETE FROM {_ident(table)} WHERE _rowid_ IN ({marks})', chunk)
            self.calls += 1
            integrity_ok = not self.preserve_fks or not self.db.execute('PRAGMA foreign_key_check').fetchone()
            keep = integrity_ok and self.oracle(self.db)
            if keep:
                self.db.execute('RELEASE SAVEPOINT axiom_trial')
                for t, r in removal:
                    self.active[t].remove(r)
                self.events.append({'trial': self.calls, 'removed_rows': len(removal),
                                    'rows_remaining': self.remaining()})
                return True
            self.db.execute('ROLLBACK TO SAVEPOINT axiom_trial')
            self.db.execute('RELEASE SAVEPOINT axiom_trial')
            return False
        except BaseException:
            self.db.execute('ROLLBACK TO SAVEPOINT axiom_trial')
            self.db.execute('RELEASE SAVEPOINT axiom_trial')
            raise

    def remaining(self):
        return sum(map(len, self.active.values()))

    def candidates(self):
        return [(t, rid) for t in self.active for rid in sorted(self.active[t])
                if rid not in self.protected.get(t, set())]

    def reduce(self, mode='hierarchical'):
        if mode == 'greedy':
            self._one_minimize()
            return
        # Table-hierarchical partitioning: first remove whole irrelevant groups,
        # then adaptively halve the remaining rows of each table.
        for table in self.active:
            deletable = [(table, x) for x in sorted(self.active[table])
                         if x not in self.protected.get(table, set())]
            n = 2
            while deletable and self.can_call():
                size = math.ceil(len(deletable) / n)
                changed = False
                for start in range(0, len(deletable), size):
                    chunk = deletable[start:start + size]
                    if self._trial(chunk):
                        deleted = set(chunk)
                        deletable = [x for x in deletable if x not in deleted]
                        n = max(2, n - 1)
                        changed = True
                        break
                    if self.exhausted:
                        return
                if not changed:
                    if n >= len(deletable):
                        break
                    n = min(len(deletable), n * 2)
        self._one_minimize()

    def _one_minimize(self):
        # Re-scan after successful changes until an entire pass removes nothing.
        # A completed final pass proves 1-minimality relative to the declared
        # admissible single-row deletions and oracle; NOT a global minimum.
        while self.can_call():
            changed = False
            for candidate in self.candidates():
                if self._trial([candidate]):
                    changed = True
                if self.exhausted:
                    return
            if not changed:
                self.one_minimal = True
                return
        self.one_minimal = False


def _snapshot(input_db: Path) -> sqlite3.Connection:
    with closing(sqlite3.connect(input_db.as_uri() + '?mode=ro', uri=True)) as source:
        target = sqlite3.connect(':memory:')
        try:
            source.backup(target)
        except BaseException:
            target.close()
            raise
    target.execute('PRAGMA foreign_keys=OFF')
    target.enable_load_extension(False)
    return target


def _make_bundle(db, before: Path, after: Path, output: Path, manifest: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ReviewError('output bundle already exists; choose another path to avoid overwriting evidence')
    with tempfile.TemporaryDirectory(prefix='axiomforge-witness-') as d:
        target = Path(d) / 'reproducer.sqlite'
        with closing(sqlite3.connect(target)) as dest:
            db.backup(dest)
        replay_script = Path(__file__).resolve().parents[2] / 'scripts' / 'replay_witness.py'
        if not replay_script.is_file():
            raise ReviewError('missing independent replay script in distribution')
        sources = {'reproducer.sqlite': target, 'before.sql': before,
                   'after.sql': after, 'replay.py': replay_script}
        hashes = {name: _sha(file) for name, file in sources.items()}
        manifest['sha256'] = hashes
        metadata = Path(d) / 'witness.json'
        metadata.write_bytes(_canon(manifest) + b'\n')
        temporary = output.with_name(output.name + '.partial')
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, file in [*sources.items(), ('witness.json', metadata)]:
                    archive.write(file, name)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def reduce(database: str | Path, before: str | Path, after: str | Path,
           fact_table: str, fact_id: str, measure: str, *, tables: list[str],
           output: str | Path, target_id: str | None = None,
           failure_flag: str | None = None, mode='hierarchical', max_calls=500,
           timeout_seconds=90, max_rows=100_000, preserve_fks=True) -> dict:
    if mode not in ('hierarchical', 'greedy'):
        raise ReviewError('mode must be hierarchical or greedy')
    if not (1 <= max_calls <= 100_000 and 1 <= timeout_seconds <= 3600):
        raise ReviewError('reduction budget out of range')
    if not tables or len(set(tables)) != len(tables):
        raise ReviewError('supply unique, explicitly allowed base table names')
    for name in (fact_table, fact_id, measure, *tables):
        _ident(name)
    input_db, before, after, output = map(lambda x: Path(x).resolve(), (database, before, after, output))
    if input_db.stat().st_size > MAX_DB_BYTES:
        raise ReviewError('input DB too large')
    sql_before, sql_after = _read_sql(before), _read_sql(after)
    report = inspect(input_db, fact_table, fact_id, measure, [before, after],
                     max_rows=max_rows, issue_limit=500)
    if target_id is not None:
        # The parent report intentionally truncates displayed issues at 500;
        # a specifically requested ID must not silently disappear from search.
        with closing(_connection(input_db)) as checked:
            baseline_rows = _read_rows(checked, f'SELECT {_ident(fact_id)} AS "__id", '
                                       f'{_ident(measure)} AS "__amount" FROM {_ident(fact_table)}',
                                       '__id', '__amount', max_rows)
            source_ids = dict(baseline_rows)
            if target_id not in source_ids:
                raise ReviewError(f'requested target is not present in the fact table: {target_id!r}')
            candidate_rows = _read_rows(checked, sql_after, 'fact_id', 'amount', max_rows)
        target_view = _evaluate([r for r in candidate_rows if r[0] == target_id],
                                {target_id: source_ids[target_id]}, limit=1)
        details = target_view['affected']
        if not details or (failure_flag and failure_flag not in details[0]['flags']):
            raise ReviewError('target fact does not have the requested regression flag')
        if report['status'] != 'REGRESSION' or report['evaluations'][0]['status'] != 'PASS':
            raise ReviewError('reduction requires the before query to globally pass')
        target, flag = target_id, failure_flag or details[0]['flags'][0]
    else:
        target, flag = _baseline_target(report, None, failure_flag)
    started = time.monotonic()
    with closing(_snapshot(input_db)) as db:
        _reject_unsafe_db(db)
        found = _rowids(db, tables)
        original_rows = sum(len(v) for v in found.values())
        if original_rows > MAX_REDUCIBLE_ROWS:
            raise ReviewError(f'too many reducible rows ({original_rows}); maximum is {MAX_REDUCIBLE_ROWS}')
        protected = {fact_table: _target_rowids(db, fact_table, fact_id, target)} if fact_table in found else {}
        def predicate(con):
            return _oracle(con, fact_table, fact_id, measure, sql_before, sql_after, target, flag,
                           max_rows=max_rows)
        if not predicate(db):
            raise ReviewError('source failure cannot be independently reproduced under reducer oracle')
        engine = _Engine(db, found, protected, predicate,
                         max_calls=max_calls, deadline=started + timeout_seconds, preserve_fks=preserve_fks)
        engine.one_minimal = False
        engine.reduce(mode)
        if not predicate(db):
            raise ReviewError('internal consistency error: final witness no longer preserves failure')
        result = {'schema': SCHEMA, 'kind': 'SQL regression witness',
                  'contract': {'fact_table': fact_table, 'fact_id': fact_id, 'measure': measure,
                               'result_id': 'fact_id', 'result_measure': 'amount', 'max_rows': max_rows},
                  'witness': {'fact_id': target, 'flag': flag,
                              'before_query_globally_passes': True, 'after_query_breaks_target': True},
                  'reduction': {'mode': mode, 'source_table_rows': {t: len(v) for t,v in found.items()},
                                'remaining_table_rows': {t: len(v) for t,v in engine.active.items()},
                                'source_reducible_rows': original_rows,
                                'remaining_reducible_rows': engine.remaining(),
                                'removed_rows': original_rows-engine.remaining(),
                                'oracle_calls': engine.calls, 'one_minimal': engine.one_minimal,
                                'budget_exhausted': engine.exhausted,
                                'seconds': round(time.monotonic() - started, 4),
                                'foreign_keys_preserved': preserve_fks,
                                'scope': '1-minimal only under allowed single-row deletions and witness predicate; no global minimum guarantee'},
                  'original': {'database_sha256': _sha(input_db),
                               'before_sha256': _sha(before), 'after_sha256': _sha(after)},
                  'source': 'explicit SQLite one-fact-one-row invariant; before query globally passes',
                  'limitations': ['only named rowid tables are reduced',
                                  'SQLite and retained fact_id/amount aliases only',
                                  'not an automatic diagnosis of the first faulty CTE',
                                  'not a security sandbox for untrusted SQLite database files']}
        _make_bundle(db, before, after, output, result)
    verification = verify_bundle(output, audit_minimal=result['reduction']['one_minimal'])
    if verification['status'] != 'REPLAY_VERIFIED':
        raise ReviewError('bundle failed self-verification')
    result['bundle'] = str(output)
    return result


def verify_bundle(path: str | Path, *, audit_minimal: bool = False) -> dict:
    archive = Path(path)
    if not archive.is_file() or archive.stat().st_size > MAX_BUNDLE_BYTES:
        raise ReviewError('witness bundle not found or too large')
    with tempfile.TemporaryDirectory(prefix='axiomforge-verify-') as directory:
        d = Path(directory)
        try:
            with zipfile.ZipFile(archive, 'r') as bundle:
                if sorted(bundle.namelist()) != sorted(MEMBER_NAMES):
                    raise ReviewError('unexpected/missing files in witness archive')
                for info in bundle.infolist():
                    if info.file_size > MAX_BUNDLE_BYTES or info.compress_size > MAX_BUNDLE_BYTES:
                        raise ReviewError('unsafe witness archive file size')
                    (d/info.filename).write_bytes(bundle.read(info.filename))
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ReviewError(f'corrupted witness archive: {exc}') from exc
        data = json.loads((d/'witness.json').read_text(encoding='utf-8'))
        if data.get('schema') != SCHEMA:
            raise ReviewError('unsupported witness schema')
        if set(data.get('sha256', {})) != {'reproducer.sqlite','before.sql','after.sql','replay.py'}:
            raise ReviewError('witness hash inventory incomplete')
        for name, digest in data['sha256'].items():
            if name not in ('reproducer.sqlite','before.sql','after.sql','replay.py') or _sha(d/name) != digest:
                raise ReviewError(f'bundle hash mismatch: {name}')
        contract = data['contract']
        current = inspect(d/'reproducer.sqlite', contract['fact_table'], contract['fact_id'],
                          contract['measure'], [d/'before.sql', d/'after.sql'],
                          max_rows=contract['max_rows'], issue_limit=500)
        if current['status'] != 'REGRESSION' or current['evaluations'][0]['status'] != 'PASS':
            raise ReviewError('reducer witness no longer reproduces the global regression')
        found = next((x for x in current['evaluations'][-1]['affected']
                      if x['fact_id'] == data['witness']['fact_id']), None)
        if not found or data['witness']['flag'] not in found['flags']:
            raise ReviewError('original target regression fingerprint is not preserved')
        with closing(sqlite3.connect((d/'reproducer.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
            for table, n in data['reduction']['remaining_table_rows'].items():
                if db.execute(f'SELECT COUNT(*) FROM {_ident(table)}').fetchone()[0] != n:
                    raise ReviewError(f'row count mismatch in {table}')
            if data['reduction']['foreign_keys_preserved'] and db.execute('PRAGMA foreign_key_check').fetchone():
                raise ReviewError('foreign key integrity is not preserved')
        audit = None
        if audit_minimal:
            before_text, after_text = _read_sql(d/'before.sql'), _read_sql(d/'after.sql')
            target, flag = data['witness']['fact_id'], data['witness']['flag']
            with closing(_snapshot(d/'reproducer.sqlite')) as scratch:
                rows = _rowids(scratch, list(data['reduction']['remaining_table_rows']))
                protected = ({contract['fact_table']: _target_rowids(
                    scratch, contract['fact_table'],contract['fact_id'],target)}
                    if contract['fact_table'] in rows else {})
                oracle = lambda con: _oracle(con, contract['fact_table'],contract['fact_id'],
                                              contract['measure'],before_text,after_text,
                                              target,flag,max_rows=contract['max_rows'])
                engine = _Engine(scratch,rows,protected,oracle,max_calls=MAX_REDUCIBLE_ROWS+1,
                                 deadline=time.monotonic()+120,
                                 preserve_fks=data['reduction']['foreign_keys_preserved'])
                for candidate in engine.candidates():
                    if engine._trial([candidate]):
                        raise ReviewError('one-minimality falsified: a removable row still exists')
                    if engine.exhausted:
                        raise ReviewError('independent minimality audit ran out of budget')
                audit = {'independently_checked_single_row_removals':engine.calls,
                         'one_minimal_under_declared_scope':True}
        return {'status': 'REPLAY_VERIFIED', 'fact_id': data['witness']['fact_id'],
                'flag': data['witness']['flag'], 'rows': data['reduction']['remaining_reducible_rows'],
                'one_minimal_claim': data['reduction']['one_minimal'],
                'independent_minimality_audit': audit,
                'note': 'replay verifies defect; explicit --audit-minimal rechecks 1-minimality'}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='axiomforge-reduce', description='SQL regression counterexample minimizer')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('reduce')
    for k in ('database', 'before', 'after', 'fact-table', 'fact-id', 'measure', 'out'):
        p.add_argument('--' + k, required=True)
    p.add_argument('--tables', required=True, help='comma-separated base tables allowed to shrink')
    p.add_argument('--target-id')
    p.add_argument('--failure-flag', choices=['DUPLICATE', 'MISSING', 'MEASURE_CHANGED'])
    p.add_argument('--mode', choices=['hierarchical', 'greedy'], default='hierarchical')
    p.add_argument('--max-calls', type=int, default=500)
    p.add_argument('--timeout', type=int, default=90)
    p.add_argument('--max-rows', type=int, default=100_000)
    p.add_argument('--allow-broken-fks', action='store_true')
    v = sub.add_parser('verify')
    v.add_argument('--bundle', required=True)
    v.add_argument('--audit-minimal', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'verify':
            result = verify_bundle(args.bundle, audit_minimal=args.audit_minimal)
            code = 0
        else:
            result = reduce(args.database, args.before, args.after, args.fact_table,
                            args.fact_id, args.measure, tables=[x.strip() for x in args.tables.split(',')],
                            output=args.out, target_id=args.target_id,
                            failure_flag=args.failure_flag, mode=args.mode,
                            max_calls=args.max_calls, timeout_seconds=args.timeout,
                            max_rows=args.max_rows, preserve_fks=not args.allow_broken_fks)
            code = 0 if result['reduction']['one_minimal'] else 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (ReviewError, sqlite3.Error, OSError, ValueError, KeyError, TypeError,
            json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(json.dumps({'status': 'REJECTED', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
