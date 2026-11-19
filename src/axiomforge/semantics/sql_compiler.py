"""Generate factorized, read-only SQL diagnostics for a real warehouse extract.

Instead of joining all dimension rows into a potentially enormous relation,
aggregate dimension key cardinalities first, then multiply survivor counts.
Proof scope: independent fact-key LEFT JOINs and exact additive aggregates.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from .pipeline_audit import AuditError

_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*$')


def ident(name: str, *, dotted: bool = False) -> str:
    if not isinstance(name, str) or not name or any(not _IDENTIFIER.fullmatch(p) for p in (name.split('.') if dotted else [name])):
        raise AuditError('SQL identifiers must be simple allowlisted names (optionally schema.table)')
    return '.'.join('"'+p+'"' for p in name.split('.')) if dotted else '"'+name+'"'


def compile_factorized_sql(*, facts_table: str, dimension_tables: list[str],
                           key: str, measure: str, segments: list[str],
                           drops: list[bool], operation: str = 'sum') -> str:
    if not (1 <= len(dimension_tables) <= 8 and len(dimension_tables) == len(segments) == len(drops)):
        raise AuditError('must provide 1..8 dimension tables with one segment and dropna policy each')
    if any(type(x) is not bool for x in drops) or operation not in ('sum','count'):
        raise AuditError('invalid aggregation or dropna types')
    fk = ident(key)
    metric = 'COUNT(*)' if operation == 'count' else f'SUM({ident(measure)})'
    ctes = [f'base AS (SELECT {fk} AS k, COUNT(*) AS fact_rows, {metric} AS contribution FROM {ident(facts_table,dotted=True)} GROUP BY {fk})']
    factors = []
    joins = []
    for i,(table,segment,drop) in enumerate(zip(dimension_tables,segments,drops)):
        group_column = ident(segment)
        ctes.append(f'd{i} AS (SELECT {fk} AS k, COUNT(*) AS matches, '
                    f'SUM(CASE WHEN {group_column} IS NOT NULL THEN 1 ELSE 0 END) AS nonnull_matches '
                    f'FROM {ident(table,dotted=True)} GROUP BY {fk})')
        joins.append(f'LEFT JOIN d{i} ON base.k=d{i}.k')
        factors.append(f'COALESCE(d{i}.nonnull_matches,0)' if drop else f'CASE WHEN d{i}.k IS NULL THEN 1 ELSE d{i}.matches END')
    multi = '('+' * '.join(f'({x})' for x in factors)+')'
    sql = (f'WITH {", ".join(ctes)}\n'
           f'SELECT base.k AS entity_key, base.fact_rows AS input_row_count, '
           f'base.contribution AS input_contribution, {multi} AS output_multiplier, '
           f'(1 - {multi}) * base.contribution AS residual\n'
           f'FROM base {" ".join(joins)}\n'
           f'ORDER BY ABS((1 - {multi}) * base.contribution) DESC, base.k;')
    return sql


def compile_manifest_query(manifest: str | Path, facts_table: str, dimension_tables: list[str]) -> str:
    from .factorized import load_manifest
    facts, stages, key, measure, operation = load_manifest(manifest)
    if len(dimension_tables) != len(stages):
        raise AuditError('table count differs from manifest stages')
    return compile_factorized_sql(facts_table=facts_table, dimension_tables=dimension_tables,
                                  key=key,measure=measure,segments=[s.segment for s in stages],
                                  drops=[s.dropna for s in stages],operation=operation)


def execute_readonly(db: str | Path, query: str, *, max_entities: int = 5000,
                     preflight: tuple[str,list[str],str,str,list[str]] | None = None) -> dict[str, Any]:
    import urllib.parse
    p = Path(db).resolve()
    if not p.is_file() or not (1 <= max_entities <= 10000):
        raise AuditError('read-only SQLite file missing or invalid entity budget')
    uri = p.as_uri() + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    try:
        if preflight is not None:
            facts_table, dimension_tables, key, measure, segments = preflight
            if len(dimension_tables) != len(segments):
                raise AuditError('SQL preflight table/segment count mismatch')
            key_col = ident(key)
            value_col = ident(measure)
            fact_source = ident(facts_table, dotted=True)
            bad_fact = conn.execute(
                f"SELECT COUNT(*) FROM {fact_source} WHERE {key_col} IS NULL OR typeof({key_col}) != 'text' "
                f"OR {key_col} = '' OR typeof({value_col}) != 'integer' "
                f"OR {value_col} < -10000000000 OR {value_col} > 10000000000"
            ).fetchone()[0]
            if bad_fact:
                raise AuditError(f'SQL FACT DOMAIN REJECTED: {bad_fact} invalid rows')
            for table, segment in zip(dimension_tables, segments):
                source = ident(table, dotted=True)
                col = ident(segment)
                bad = conn.execute(
                    f"SELECT COUNT(*) FROM {source} WHERE {key_col} IS NULL OR typeof({key_col}) != 'text' "
                    f"OR {key_col} = '' OR ({col} IS NOT NULL AND typeof({col}) != 'text')"
                ).fetchone()[0]
                if bad:
                    raise AuditError(f'SQL DIMENSION DOMAIN REJECTED: {bad} rows in {table}')
        cursor=conn.execute(query)
        result=[]
        for number,row in enumerate(cursor):
            if number >= max_entities:
                raise AuditError('entity output limit reached')
            if row[0] is None or any(x is None or type(x) not in (int,float) for x in row[1:]):
                raise AuditError('unknown NULL or nonnumeric output; no signed certificate')
            if any(type(x) is float for x in row[1:]):
                raise AuditError('floating-point SQL output not supported')
            result.append({'entity':f'entity_{number+1:04d}', 'input_rows':row[1],
                           'input_contribution':row[2], 'output_multiplier':row[3], 'residual':row[4]})
        # The result does not include raw business keys unless the user executes SQL directly.
        return {'status':'READONLY_SQLITE_QUERY_EXECUTED', 'preflight':'PASSED' if preflight is not None else 'NOT_PERFORMED', 'entities':len(result),
                'global_residual':sum(x['residual'] for x in result),
                'affected_entities':sum(x['output_multiplier']!=1 for x in result),
                'top_entities':result[:25],
                'warning':'database snapshot must be consistent; business key identifiers redacted in this JSON'}
    except sqlite3.DatabaseError as exc:
        raise AuditError('SQLite diagnostic query could not run: ' + str(exc)) from exc
    finally:
        conn.close()
