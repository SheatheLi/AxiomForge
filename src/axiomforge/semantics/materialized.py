"""Audit a materialized group-by output against the executed query and contract.

This is an operator feature: the auditor reads a persisted downstream CSV,
independently executes the supported pipeline, and distinguishes stale or
altered results from errors already inherent in the transformation semantics.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .pipeline_audit import AuditConfig, AuditError


def _results_pandas(facts: list[dict], dims: list[dict], c: AuditConfig) -> dict[str | None, int]:
    import pandas as pd
    f = pd.DataFrame(facts)
    d = pd.DataFrame(dims, columns=[c.join_key, c.segment])
    merged = f.merge(d, on=c.join_key, how='left')
    group = merged.groupby(c.segment, dropna=c.dropna)[c.measure]
    values = group.count() if c.operation == 'count' else group.sum()
    return {(None if pd.isna(k) else str(k)): int(v) for k, v in values.items()}


def _results_sqlite(facts: list[dict], dims: list[dict], c: AuditConfig) -> dict[str | None, int]:
    import sqlite3
    conn = sqlite3.connect(':memory:')
    try:
        conn.execute('CREATE TABLE f (k TEXT NOT NULL, v INTEGER NOT NULL)')
        conn.execute('CREATE TABLE d (k TEXT NOT NULL, seg TEXT)')
        conn.executemany('INSERT INTO f VALUES (?,?)', [(f[c.join_key], f[c.measure]) for f in facts])
        conn.executemany('INSERT INTO d VALUES (?,?)', [(d[c.join_key], d[c.segment]) for d in dims])
        agg = 'COUNT(f.v)' if c.operation == 'count' else 'SUM(f.v)'
        where = 'WHERE d.seg IS NOT NULL' if c.dropna else ''
        return {k: int(v) for k, v in conn.execute(f'SELECT d.seg, {agg} FROM f LEFT JOIN d ON f.k=d.k {where} GROUP BY d.seg')}
    finally:
        conn.close()


def read_actual(path: str | Path, c: AuditConfig) -> dict[str | None, int]:
    p = Path(path)
    if not p.is_file() or p.stat().st_size > 3_000_000:
        raise AuditError('materialized output missing or oversized')
    result: dict[str | None, int] = {}
    try:
        with p.open('r', encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames is None or len(reader.fieldnames) != 2 or set(reader.fieldnames) != {c.segment, c.measure}:
                raise AuditError('materialized CSV must contain exactly segment and measure columns')
            for number, row in enumerate(reader, start=2):
                if number > c.max_rows + 1:
                    raise AuditError('materialized row bound exceeded')
                if None in row or row[c.segment] is None or row[c.measure] is None:
                    raise AuditError('malformed materialized CSV')
                key = row[c.segment] or None
                value = row[c.measure]
                if c.dropna and key is None:
                    raise AuditError('materialized data contains NULL key, but dropna=True')
                if key in result:
                    raise AuditError('materialized group key appears more than once')
                if not value or value.strip() != value or not value.lstrip('-').isascii() or not value.lstrip('-').isdigit():
                    raise AuditError(f'invalid materialized integer at row {number}')
                result[key] = int(value)
                if abs(result[key]) > 2**60:
                    raise AuditError('materialized integer out of supported range')
    except (UnicodeError, csv.Error) as exc:
        raise AuditError(f'materialized CSV read failure: {exc}') from exc
    return result


def check_materialized(facts: list[dict], dims: list[dict], c: AuditConfig,
                       output: str | Path) -> dict[str, Any]:
    expected_pandas = _results_pandas(facts, dims, c)
    expected_sqlite = _results_sqlite(facts, dims, c)
    if expected_pandas != expected_sqlite:
        raise AuditError('INCONCLUSIVE: materialized group output differs across independent Pandas/SQLite engines')
    observed = read_actual(output, c)
    variants = []
    for key in sorted(set(observed) | set(expected_sqlite), key=lambda k: (k is None, k or '')):
        actual = observed.get(key)
        target = expected_sqlite.get(key)
        if actual != target:
            variants.append({'segment': key if key is not None else '<NULL>',
                             'query_expected': target, 'file_observed': actual,
                             'delta': (actual - target if actual is not None and target is not None else None)})
    return {'status': 'EXACT_QUERY_OUTPUT' if not variants else 'MATERIALIZED_OUTPUT_DRIFT',
            'group_count_query': len(expected_sqlite), 'group_count_file': len(observed),
            'mismatched_groups': len(variants), 'group_differences': variants[:100],
            'validation': 'Pandas and native SQLite group aggregates agree; persisted CSV compared exactly',
            'note': 'A correct query output may still violate the once-per-fact contract; check audit verdict too'}
