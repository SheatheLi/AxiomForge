"""Audit real CSV-backed LEFT JOIN -> GROUP BY pipelines.

Three independent execution backends check one narrowly specified transformation;
row-contribution provenance explains *why* a global sum may appear correct while
per-entity/record contracts fail. The contract, not a discovered business intent,
sets the meaning of a violation.
"""
from __future__ import annotations

import collections
import csv
import hashlib
import json
import pathlib
import sqlite3
from dataclasses import dataclass, asdict
from typing import Any

from .composed import compile_composite, inspect_composite, _validate


class AuditError(ValueError):
    pass


@dataclass(frozen=True)
class AuditConfig:
    join_key: str = 'sku'
    measure: str = 'cost'
    segment: str = 'type'
    dropna: bool = True
    operation: str = 'sum'
    max_rows: int = 5000
    max_join_rows: int = 30000

    def __post_init__(self):
        for name in ('join_key', 'measure', 'segment'):
            item = getattr(self, name)
            if not isinstance(item, str) or not item.isidentifier() or item.startswith('_'):
                raise AuditError(f'unsafe column identifier: {name}')
        if len({self.join_key, self.measure, self.segment}) < 3:
            raise AuditError('column roles must be distinct')
        if self.operation not in ('sum', 'count') or type(self.dropna) is not bool:
            raise AuditError('invalid aggregate/dropna option')
        if not (1 <= self.max_rows <= 10000 and 1 <= self.max_join_rows <= 100000):
            raise AuditError('row bounds outside supported domain')


def _sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _load(path: str | pathlib.Path, *, columns: tuple[str, str], null_second: bool,
          int_second: bool, max_rows: int) -> list[dict[str, Any]]:
    file = pathlib.Path(path)
    if not file.is_file() or file.stat().st_size > 3_000_000:
        raise AuditError('missing dataset or CSV size limit exceeded (3 MB)')
    records = []
    try:
        with file.open('r', encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames is None or not set(columns).issubset(reader.fieldnames) or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise AuditError('CSV header missing columns or contains duplicates')
            for i, entry in enumerate(reader):
                if i >= max_rows:
                    raise AuditError('CSV row limit exceeded')
                if None in entry or any(entry[c] is None for c in columns):
                    raise AuditError(f'malformed CSV at row {i + 2}')
                key = entry[columns[0]]
                raw = entry[columns[1]]
                if not key:
                    raise AuditError(f'NULL/empty join key unsupported at CSV row {i + 2}')
                if int_second:
                    # Reject floating-point parsing, locale formats, and booleans.
                    if not raw or raw.strip() != raw or raw.startswith('+') or not (raw.lstrip('-').isascii() and raw.lstrip('-').isdigit()):
                        raise AuditError(f'noninteger amount at CSV row {i + 2}')
                    value = int(raw)
                    if abs(value) > 10**10:
                        raise AuditError('measure magnitude outside supported domain')
                else:
                    value = (None if raw == '' and null_second else raw)
                records.append({columns[0]: key, columns[1]: value})
    except (UnicodeError, csv.Error) as exc:
        raise AuditError(f'CSV decoding/format error: {exc}') from exc
    if not records:
        raise AuditError('input CSV requires at least one row')
    return records


def read_datasets(facts_path: str | pathlib.Path, dimensions_path: str | pathlib.Path,
                  config: AuditConfig) -> tuple[list[dict], list[dict]]:
    return (
        _load(facts_path, columns=(config.join_key, config.measure), null_second=False,
              int_second=True, max_rows=config.max_rows),
        _load(dimensions_path, columns=(config.join_key, config.segment), null_second=True,
              int_second=False, max_rows=config.max_rows),
    )


def _plan(c: AuditConfig):
    op = c.operation
    left = f'df["{c.measure}"].{op}()'
    right = (f'df.merge(dim, on="{c.join_key}", how="left")'
             f'.groupby("{c.segment}", dropna={c.dropna})'
             f'["{c.measure}"].{op}().sum()')
    return compile_composite(left, right)


def _sizes(facts: list[dict], dims: list[dict], c: AuditConfig) -> int:
    counts = collections.Counter(d[c.join_key] for d in dims)
    projected = sum(max(1, counts[f[c.join_key]]) for f in facts)
    if len(facts) > c.max_rows or len(dims) > c.max_rows or projected > c.max_join_rows:
        raise AuditError(f'projected join size {projected} exceeds configured execution budget')
    return projected


def _backend_execute(backend: str, facts: list[dict], dims: list[dict], c: AuditConfig):
    """Independent materialized execution of the actual join, including row ids.

    Output (global_before, global_after, per_key_before, per_key_after,
    surviving_copy_count_per_fact_row). No use of provenance transfer formula.
    """
    key, measure, group = c.join_key, c.measure, c.segment
    if backend == 'pandas':
        import pandas as pd
        f = pd.DataFrame(facts)
        f['_audit_id'] = list(range(len(f)))
        d = pd.DataFrame(dims, columns=[key, group])
        merged = f.merge(d, on=key, how='left', sort=False)
        if c.dropna:
            merged = merged[merged[group].notna()]
        left = len(f) if c.operation == 'count' else int(f[measure].sum())
        right = len(merged) if c.operation == 'count' else int(merged[measure].sum())
        l_by = {str(k): int(v) for k, v in (f.groupby(key)[measure].count() if c.operation == 'count' else f.groupby(key)[measure].sum()).items()}
        r_by = {str(k): int(v) for k, v in (merged.groupby(key)[measure].count() if c.operation == 'count' else merged.groupby(key)[measure].sum()).items()}
        rows = collections.Counter(int(i) for i in merged['_audit_id'].tolist())
        return left, right, l_by, r_by, [rows[i] for i in range(len(f))]
    if backend == 'sqlite':
        cdb = sqlite3.connect(':memory:')
        try:
            cdb.execute('CREATE TABLE f (rid INTEGER PRIMARY KEY, k TEXT NOT NULL, v INTEGER NOT NULL)')
            cdb.execute('CREATE TABLE d (k TEXT NOT NULL, seg TEXT)')
            cdb.executemany('INSERT INTO f VALUES (?,?,?)', [(i, f[key], f[measure]) for i, f in enumerate(facts)])
            cdb.executemany('INSERT INTO d VALUES (?,?)', [(d[key], d[group]) for d in dims])
            expr = 'COUNT(*)' if c.operation == 'count' else 'COALESCE(SUM(f.v), 0)'
            suffix = 'WHERE d.seg IS NOT NULL' if c.dropna else ''
            left = int(cdb.execute(f'SELECT {expr} FROM f').fetchone()[0])
            query = f'FROM f LEFT JOIN d ON f.k = d.k {suffix}'
            right = int(cdb.execute(f'SELECT {expr} {query}').fetchone()[0])
            l_by = dict(cdb.execute(f'SELECT f.k,{expr} FROM f GROUP BY f.k').fetchall())
            r_by = dict(cdb.execute(f'SELECT f.k,{expr} {query} GROUP BY f.k').fetchall())
            byrow = dict(cdb.execute(f'SELECT f.rid,COUNT(*) {query} GROUP BY f.rid').fetchall())
            return left, right, l_by, r_by, [byrow.get(i, 0) for i in range(len(facts))]
        finally:
            cdb.close()
    if backend == 'sqlalchemy':
        import sqlalchemy as sa
        engine = sa.create_engine('sqlite+pysqlite:///:memory:')
        meta = sa.MetaData()
        f = sa.Table('f', meta, sa.Column('rid', sa.Integer, primary_key=True), sa.Column('k', sa.String), sa.Column('v', sa.Integer))
        d = sa.Table('d', meta, sa.Column('k', sa.String), sa.Column('seg', sa.String))
        meta.create_all(engine)
        try:
            with engine.begin() as conn:
                conn.execute(f.insert(), [{'rid': i, 'k': v[key], 'v': v[measure]} for i, v in enumerate(facts)])
                if dims:
                    conn.execute(d.insert(), [{'k': v[key], 'seg': v[group]} for v in dims])
                expr = sa.func.count() if c.operation == 'count' else sa.func.coalesce(sa.func.sum(f.c.v), 0)
                join = f.outerjoin(d, f.c.k == d.c.k)
                right_query = sa.select(expr).select_from(join)
                grouped = sa.select(f.c.k, expr).select_from(join)
                rowquery = sa.select(f.c.rid, sa.func.count()).select_from(join)
                if c.dropna:
                    right_query = right_query.where(d.c.seg.is_not(None))
                    grouped = grouped.where(d.c.seg.is_not(None))
                    rowquery = rowquery.where(d.c.seg.is_not(None))
                left = int(conn.scalar(sa.select(expr).select_from(f)))
                right = int(conn.scalar(right_query))
                l_by = dict(conn.execute(sa.select(f.c.k, expr).group_by(f.c.k)).all())
                r_by = dict(conn.execute(grouped.group_by(f.c.k)).all())
                byrow = dict(conn.execute(rowquery.group_by(f.c.rid)).all())
                return left, right, l_by, r_by, [byrow.get(i, 0) for i in range(len(facts))]
        finally:
            engine.dispose()
    raise AuditError('unknown backend')


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _normalized(result, keys: list[str]) -> tuple:
    l, r, before, after, counts = result
    return l, r, tuple((k, before.get(k, 0), after.get(k, 0)) for k in keys), tuple(counts)


def audit(facts: list[dict], dims: list[dict], config: AuditConfig, *, top: int = 15,
          diagnostic: bool = True) -> dict[str, Any]:
    if not isinstance(top, int) or not 1 <= top <= 100:
        raise AuditError('top must be 1..100')
    plan = _plan(config)
    _validate(plan, facts, dims)
    projected = _sizes(facts, dims, config)
    theory = inspect_composite(plan, facts, dims)
    keys = sorted({f[config.join_key] for f in facts})
    refs = {key: f'entity_{i + 1:04d}' for i, key in enumerate(keys)}
    executions = {backend: _backend_execute(backend, facts, dims, config)
                  for backend in ('pandas', 'sqlite', 'sqlalchemy')}
    baseline = _normalized(executions['sqlite'], keys)
    mismatches = [name for name, value in executions.items() if _normalized(value, keys) != baseline]
    predicted = theory['trace']
    if tuple(executions['sqlite'][:2]) != tuple(theory['reference']) or [p['retained_after_groupby'] for p in predicted] != executions['sqlite'][4]:
        mismatches.append('provenance_model')
    if mismatches:
        raise AuditError('INCONCLUSIVE_BACKEND_OR_MODEL_MISMATCH: ' + ','.join(mismatches))
    before, after, key_records, row_copies = baseline
    segments = []
    for key, l, r in key_records:
        segments.append({'entity': refs[key], 'before': l, 'after': r, 'residual': l - r,
                         'affected_rows': sum(1 for i, f in enumerate(facts) if f[config.join_key] == key and row_copies[i] != 1)})
    affected = [i for i, count in enumerate(row_copies) if count != 1]
    if before != after:
        verdict = 'GLOBAL_VALUE_DRIFT'
    elif any(entry['residual'] != 0 for entry in segments):
        verdict = 'CANCELLED_AT_GLOBAL_TOTAL'
    elif affected:
        verdict = 'HIDDEN_AT_ENTITY_AND_GLOBAL_TOTAL'
    else:
        verdict = 'EXACTLY_ONCE_PRESERVED'
    residuals = [dict(row, entity=refs[facts[row['row']][config.join_key]]) for row in predicted]
    for row in residuals:
        row.pop('fact_key', None)
    risky = sorted((x for x in residuals if x['coefficient'] != 0),
                   key=lambda x: (-abs(x['delta']), x['row']))[:top]
    impact = sorted((x for x in segments if x['residual'] != 0 or x['affected_rows']),
                    key=lambda x: (-abs(x['residual']), -x['affected_rows'], x['entity']))[:top]
    duplicate = collections.Counter(d[config.join_key] for d in dims)
    unused = set(duplicate) - set(keys)
    return {
        'schema': 'axiomforge.pipeline_audit.v1', 'contract': 'fact row must contribute exactly once to post-join aggregate',
        'config': asdict(config), 'plan_fingerprint': plan.fingerprint(),
        'counts': {'fact_rows': len(facts), 'dimension_rows': len(dims), 'projected_join_rows': projected,
                   'distinct_fact_entities': len(keys), 'affected_fact_rows': len(affected),
                   'affected_entities': sum(bool(x['affected_rows']) for x in segments),
                   'unreferenced_duplicate_dimension_keys': sum(duplicate[k] > 1 for k in unused)},
        'global': {'before': before, 'after': after, 'residual': before - after,
                   'global_only_baseline_reports_problem': before != after},
        'verdict': verdict,
        'invariant_levels': {'global_pass': before == after,
                             'per_entity_pass': all(x['residual'] == 0 for x in segments),
                             'per_record_exactly_once_pass': not affected},
        'top_affected_entities': impact, 'top_provenance_rows': risky,
        'backend_cross_check': {'backends': list(executions), 'status': 'THREE_EXECUTION_PATHS_AGREE',
                                'per_entity_and_row_copy_counts_verified': True},
        'diagnostics': {
            'join_multiplicity_anomalies': sum(r['joined_rows'] > 1 for r in predicted),
            'groupby_dropped_fact_rows': sum(r['retained_after_groupby'] == 0 for r in predicted),
            'offsetting_nonzero_contributions': before == after and sum(abs(r['delta']) for r in predicted) > 0,
            'possible_remediation': ('inspect dimension key multiplicity and groupby NULL policy; '
                                     'do not modify financial amounts to force the residual to zero')},
        'scope': ['integer additive SUM or COUNT only', 'non-null string join keys',
                  'explicit contract of once-per-fact contribution',
                  'SQLite-backed SQLAlchemy is not independent database engine validation'],
    }


def audit_files(facts_path: str | pathlib.Path, dims_path: str | pathlib.Path,
                config: AuditConfig, *, top: int = 15, actual_path: str | pathlib.Path | None = None) -> dict[str, Any]:
    facts, dims = read_datasets(facts_path, dims_path, config)
    result = audit(facts, dims, config, top=top)
    result['inputs'] = {'facts_sha256': _sha(pathlib.Path(facts_path)),
                        'dimensions_sha256': _sha(pathlib.Path(dims_path))}
    if actual_path is not None:
        from .materialized import check_materialized
        result['materialized'] = check_materialized(facts, dims, config, actual_path)
        result['inputs']['materialized_sha256'] = _sha(pathlib.Path(actual_path))
        result['release_gate'] = ('PASS' if result['verdict'] == 'EXACTLY_ONCE_PRESERVED'
                                  and result['materialized']['status'] == 'EXACT_QUERY_OUTPUT' else 'FAIL')
    else:
        result['release_gate'] = 'PASS' if result['verdict'] == 'EXACTLY_ONCE_PRESERVED' else 'FAIL'
    return result


def write_receipt(path: str | pathlib.Path, payload: dict[str, Any]) -> None:
    file = pathlib.Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    envelope = {'payload': payload, 'digest': hashlib.sha256(_canonical(payload)).hexdigest()}
    file.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def verify_receipt(path: str | pathlib.Path, facts_path: str | pathlib.Path,
                   dims_path: str | pathlib.Path, actual_path: str | pathlib.Path | None = None) -> dict[str, Any]:
    envelope = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    if set(envelope) != {'payload', 'digest'} or hashlib.sha256(_canonical(envelope['payload'])).hexdigest() != envelope['digest']:
        raise AuditError('evidence envelope digest mismatch')
    stored = envelope['payload']
    if stored.get('schema') != 'axiomforge.pipeline_audit.v1' or 'inputs' not in stored:
        raise AuditError('unsupported or incomplete evidence')
    config = AuditConfig(**stored['config'])
    # Even if an attacker re-hashes modified evidence, execution will detect it.
    if ('materialized_sha256' in stored['inputs']) != (actual_path is not None):
        raise AuditError('materialized output required exactly when original receipt includes it')
    current = audit_files(facts_path, dims_path, config, top=15, actual_path=actual_path)
    if current != stored:
        raise AuditError('source changed or evidence differs from real cross-backend execution')
    return {'status': 'REAL_CSV_THREE_BACKENDS_REEXECUTED', 'verdict': current['verdict'],
            'fact_rows': current['counts']['fact_rows']}


def minimize_witness(facts: list[dict], dims: list[dict], config: AuditConfig,
                     *, max_replays: int = 500) -> dict[str, Any]:
    """Two-relation delta debugging with three-backend confirmation.

    Candidate exploration uses the independent provenance formula. The final
    reduced witness must preserve the exact class on *actual* Pandas, SQLite,
    and SQLAlchemy execution. Deletion-only reduction, not proof of global size
    optimality across row edits/reorderings.
    """
    p = _plan(config)
    _validate(p, facts, dims)

    def signature(fr, dr):
        state = inspect_composite(p, fr, dr)
        terms = state['trace']
        if state['residual'] != 0:
            return 'GLOBAL_VALUE_DRIFT'
        grouped = collections.defaultdict(int)
        for i, term in enumerate(terms):
            grouped[fr[i][config.join_key]] += term['delta']
        if any(grouped.values()):
            return 'CANCELLED_AT_GLOBAL_TOTAL'
        if any(x['retained_after_groupby'] != 1 for x in terms):
            return 'HIDDEN_AT_ENTITY_AND_GLOBAL_TOTAL'
        return 'EXACTLY_ONCE_PRESERVED'

    target = signature(facts, dims)
    if target == 'EXACTLY_ONCE_PRESERVED':
        return {'status': 'NO_FAILURE_TO_MINIMIZE', 'target': target}
    if len(facts) > 250 or len(dims) > 250 or max_replays < 1:
        raise AuditError('minimization bounds: <=250 rows per relation and >=1 replay')
    active_facts = list(enumerate(facts))
    active_dims = list(enumerate(dims))
    replays = 0

    # Alternate the two input relations until no single row can be removed.
    # Large deletion chunks are tried first, then singleton removals.
    changed = True
    while changed and replays < max_replays:
        changed = False
        for relation in ('facts', 'dimensions'):
            active = active_facts if relation == 'facts' else active_dims
            stride = max(1, len(active) // 2)
            while stride and replays < max_replays:
                made_progress = False
                for start in range(0, len(active), stride):
                    trial = active[:start] + active[start + stride:]
                    if relation == 'facts' and not trial:
                        continue
                    new_facts = [r for _, r in (trial if relation == 'facts' else active_facts)]
                    new_dims = [r for _, r in (trial if relation == 'dimensions' else active_dims)]
                    replays += 1
                    if signature(new_facts, new_dims) == target:
                        active = trial
                        made_progress = changed = True
                        break
                    if replays >= max_replays:
                        break
                if not made_progress:
                    stride //= 2
            if relation == 'facts':
                active_facts = active
            else:
                active_dims = active
    reduced_facts = [r for _, r in active_facts]
    reduced_dims = [r for _, r in active_dims]
    final = audit(reduced_facts, reduced_dims, config)
    if final['verdict'] != target:
        raise AssertionError('reduced witness did not survive actual engine replay')
    return {
        'status': 'REPLAYED_DELTA_DEBUG_CANDIDATE' if replays < max_replays else 'BUDGET_EXHAUSTED_CANDIDATE',
        'target': target,
        'original_rows': len(facts), 'witness_rows': len(reduced_facts),
        'original_dimension_rows': len(dims), 'witness_dimension_rows': len(reduced_dims),
        'retained_fact_indices': [i for i, _ in active_facts],
        'retained_dimension_indices': [i for i, _ in active_dims],
        'replays': replays, 'three_backend_verdict': final['verdict'],
        'warning': 'deletion-minimal candidate for this predicate, not a global optimum or a safe data repair',
    }
