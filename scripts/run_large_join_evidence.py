"""Exercise genuinely large join multiplicity without materializing the join.

A real SQLite query over aggregated per-key dimension counts validates the
computed multiplier. No claimed benchmark versus a full 312M-row SQL join:
that deliberately is NOT executed.
"""
from __future__ import annotations
import hashlib,json,pathlib,sqlite3,sys,time
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.semantics.factorized import Stage,factorized
from axiomforge.semantics.sql_compiler import compile_factorized_sql,execute_readonly


def main():
    base=ROOT/'artifacts'/'pipeline_audit'
    base.mkdir(parents=True,exist_ok=True)
    db=base/'large_joins.sqlite'
    if db.exists():db.unlink()
    facts=[{'sku':'A','cost':1}]
    stages=[]
    conn=sqlite3.connect(db)
    try:
        conn.execute('CREATE TABLE facts (sku TEXT NOT NULL, cost INTEGER NOT NULL)')
        conn.execute('INSERT INTO facts VALUES (?,?)',('A',1))
        for i in range(5):
            table=f'dim_{i}';segment=f'segment_{i}'
            conn.execute(f'CREATE TABLE {table} (sku TEXT NOT NULL, "{segment}" TEXT NOT NULL)')
            batch=[('A',f'group_{j}') for j in range(50)]
            conn.executemany(f'INSERT INTO {table} VALUES (?,?)',batch)
            stages.append(Stage(f's{i}',segment,False,[{'sku':'A',segment:row[1]} for row in batch]))
        conn.commit()
    finally:conn.close()
    start=time.perf_counter()
    model=factorized(facts,stages,materialize_limit=30000)
    model_s=time.perf_counter()-start
    sql=compile_factorized_sql(facts_table='facts',dimension_tables=[f'dim_{i}' for i in range(5)],
                               key='sku',measure='cost',segments=[f'segment_{i}' for i in range(5)],
                               drops=[False]*5)
    sql_file=base/'large_join_diagnostic.sql'
    sql_file.write_text(sql+'\n',encoding='utf8')
    start=time.perf_counter()
    actual=execute_readonly(db,sql,preflight=('facts',[f'dim_{i}' for i in range(5)],'sku','cost',[f'segment_{i}' for i in range(5)]))
    sql_s=time.perf_counter()-start
    expected=50**5
    if model['would_materialize_rows']!=expected or actual['top_entities'][0]['output_multiplier']!=expected:
        raise AssertionError('compiled SQL and factorized calculation disagree')
    report={'schema':'axiomforge.large_join_no_materialization.v1',
            'source_rows':1+50*5,'join_stages':5,'multiplicity_per_stage':50,
            'hypothetical_materialized_rows':expected,
            'python_factorized_model_after':model['model_after'],
            'python_mode':model['mode'],
            'sqlite_factorized_sql_multiplier':actual['top_entities'][0]['output_multiplier'],
            'sqlite_result':actual['status'],
            'time_python_model_seconds_observed':round(model_s,6),
            'time_sql_query_seconds_observed':round(sql_s,6),
            'sqlite_version':sqlite3.sqlite_version,
            'database_sha256':hashlib.sha256(db.read_bytes()).hexdigest(),
            'query_sha256':hashlib.sha256(sql_file.read_bytes()).hexdigest(),
            'limitations':['full materialized join deliberately not executed',
                           'star schema independent same-key joins only',
                           'these timing measurements are illustrative, not competitive speedup estimates',
                           'no unknown third-party production bug discovered']}
    (base/'large_join.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
