"""One-command, offline external-issue mechanism study with independent witnesses.

Use `python scripts/verify_external_issues.py`.  Deliberately avoids dbt adapter
claims and never mutates original databases. Exit nonzero if expected controls,
replays, independence or negative cases fail.
"""
from __future__ import annotations
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timezone
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.incremental import inspect_database
from axiomforge.incremental_reduction import reduce_issue,emit_bundle
from axiomforge.microbatch import inspect_microbatch

CASES=ROOT/'examples'/'external_issues'

CONFIGS=[
    ('dbt_11799','composite_key','num_column,str_column','composite_bug','UNINTENDED_DELETE',
     'https://github.com/dbt-labs/dbt-core/issues/11799','exact issue-provided SQLite-equivalent SQL/data logic'),
    ('dbt_5691','nullable_key','type,name','nullable_bug','NULL_MATCH_GAP',
     'https://github.com/dbt-labs/dbt-core/issues/5691','source-described two-day rows; SQLite matching-mechanism reproduction'),
    ('dbt_10432','source_collision','key','source_collision','MULTIPLE_SOURCE_MATCH',
     'https://github.com/dbt-labs/dbt-core/issues/10432','synthetic hypothesis only; reporter provided no reproducible data'),
]

def _baseline(db_file:Path,keys:list[str]):
    with closing(sqlite3.connect(db_file)) as c:
        result={}
        for table in ('target','stage'):
            vals=c.execute('SELECT '+','.join('"'+k+'"' for k in keys)+' FROM "'+table+'"').fetchall()
            count=Counter(vals)
            result[table]={'unique':len(count)==len(vals),'not_null':all(None not in k for k in vals)}
        return result


def run(out:Path):
    if out.exists():
        raise FileExistsError('output folder already exists; choose a new location')
    out.mkdir(parents=True)
    observations=[]
    for case_name,db_stem,keys,sql_name,flag,url,source_grade in CONFIGS:
        start=time.perf_counter()
        db_path=CASES/(db_stem+'.sqlite')
        before_hash=hashlib.sha256(db_path.read_bytes()).hexdigest()
        sql=(CASES/(sql_name+'.sql')).read_text('utf8')
        key_columns=keys.split(',')
        report=inspect_database(db_path,target='target',stage='stage',keys=key_columns,delete_sql=sql)
        assert flag in report['flags'], (case_name,report['flags'])
        reduced,receipt=reduce_issue(db_path,target='target',stage='stage',keys=key_columns,
                                     delete_sql=sql,flag=flag,max_calls=2000,timeout=60)
        archive=out/(case_name+'.zip')
        try:
            emit_bundle(reduced,receipt,candidate_sql=sql,out=archive,
                        replay_script=ROOT/'scripts'/'replay_incremental.py')
        finally:reduced.close()
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(archive) as z:z.extractall(tmp)
            proc=subprocess.run([sys.executable,'-I','replay.py'],cwd=tmp,
                                capture_output=True,text=True,timeout=15)
            assert proc.returncode==0,(case_name,proc.stdout,proc.stderr)
        assert before_hash==hashlib.sha256(db_path.read_bytes()).hexdigest()
        assert receipt['reduction']['independent_1_minimal']
        observations.append({
            'case':case_name,'source_issue':url,'evidence_level':source_grade,
            'expected_flag':flag,'observed_flags':report['flags'],
            'initial_counts':report['counts'], 'baseline_not_null_unique':_baseline(db_path,key_columns),
            'witness_rows_before':receipt['reduction']['initial_rows'],
            'witness_rows_after':receipt['reduction']['retained_rows'],
            'oracle_calls':receipt['reduction']['oracle_calls'],
            'independent_1_minimal':True,'standard_library_replay':'REPRODUCED',
            'elapsed_seconds':round(time.perf_counter()-start,4)
        })
    # Fourth distinct public fault family: dbt Fusion #14525 reports a 10-row
    # source read in each of four microbatches (40 rows instead of 10). The
    # source dates below are independent synthetic test data, not upstream data.
    broken=inspect_microbatch(CASES/'microbatch.sqlite',CASES/'microbatch_bug.sql',
        source='source',source_id='event_id',source_date='event_at',
        start='2020-01-01',end='2020-01-05')
    assert broken['counts']['source_rows']==10
    assert broken['counts']['total_batch_read_rows']==40
    assert 'SOURCE_WINDOW_LEAK' in broken['flags']
    assert 'MULTIBATCH_DUPLICATED' in broken['flags']
    observations.append({
        'case':'dbt_14525',
        'source_issue':'https://github.com/dbt-labs/dbt/issues/14525',
        'evidence_level':'source reports 10 source rows / 4 windows / 40 output rows; SQLite data independently generated',
        'expected_flag':'SOURCE_WINDOW_LEAK',
        'observed_flags':broken['flags'],
        'initial_counts':broken['counts'],
        'windows':broken['windows'],
        'witness':'self-contained microbatch.sqlite + microbatch_bug.sql; no standalone reducer yet',
    })
    # Fixed forms and a deliberately nonunique but valid partition-key contract.
    controls=[]
    for name,db_stem,keys,sql,grain,nullable in [
        ('composite_tuple_fix','composite_key','num_column,str_column','composite_fixed','row',False),
        ('nullable_nullsafe_fix','nullable_key','type,name','nullable_fixed','row',True),
        ('partition_not_row_key','partition_key','date_day','partition_replace','partition',False),
    ]:
        r=inspect_database(CASES/(db_stem+'.sqlite'),target='target',stage='stage',
            keys=keys.split(','),delete_sql=(CASES/(sql+'.sql')).read_text('utf8'),
            grain=grain,null_safe=nullable)
        assert r['status']=='PASS',(name,r)
        controls.append({'case':name,'status':r['status'],'counts':r['counts']})
    fixed_micro=inspect_microbatch(CASES/'microbatch.sqlite',CASES/'microbatch_fixed.sql',
        source='source',source_id='event_id',source_date='event_at',start='2020-01-01',end='2020-01-05')
    assert fixed_micro['status']=='PASS'
    assert fixed_micro['counts']['total_batch_read_rows']==10
    controls.append({'case':'microbatch_source_window_fixed','status':'PASS','counts':fixed_micro['counts']})
    report={
        'schema':'axiomforge.external_issue_study.v1',
        'claim':'Offline SQLite reproduction of published mechanisms; not upstream bug repair, adapter parity, or external adoption',
        'observations':observations, 'negative_and_fixed_controls':controls,
        'strength':'Dynamic executed DELETE impact is compared to full composite-tuple contract, beyond per-column unique/not-null checks',
        'limitations':['No dbt/Trino/Redshift/Snowflake adapter run',
                       'Issue 10432 is a mechanism-level hypothesis, not reporter-data reproduction',
                       'Witness mininality is only relative to single-row deletion',
                       'Input SQLite may contain sensitive data; use only in an isolated/trusted environment'],
    }
    (out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
    return report

if __name__=='__main__':
    dest=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'results'/('external_issue_study_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    try:
        summary=run(dest)
        for r in summary['observations']:
            if 'witness_rows_after' in r:
                print(r['case'],r['expected_flag'],
                      f"{r['witness_rows_before']}->{r['witness_rows_after']} rows",
                      f"oracle={r['oracle_calls']}",'independent replay=REPRODUCED')
            else:
                print(r['case'],r['expected_flag'],
                      f"rows={r['initial_counts']['source_rows']} -> reads={r['initial_counts']['total_batch_read_rows']}")
        print('fixed/negative controls:',len(summary['negative_and_fixed_controls']), 'PASS')
        print('evidence:',dest/'validation.json')
    except Exception as ex:
        print('FAILED:',ex,file=sys.stderr)
        raise SystemExit(1)
