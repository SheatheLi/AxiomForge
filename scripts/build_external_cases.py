"""Deterministic offline re-creations of publicly reported SQL problem mechanisms.

We deliberately do NOT claim that SQLite emulates dbt-core's adapter runtime.
The scenario sources, distinguishing assumptions, and limitations are in
examples/external_issues/来源与边界.md.
"""
from __future__ import annotations
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]/'examples'/'external_issues'

def make() -> None:
    ROOT.mkdir(exist_ok=True,parents=True)
    def db(name, ddl, rows):
        file=ROOT/name
        file.unlink(missing_ok=True)
        c=sqlite3.connect(file)
        with c:
            c.executescript(ddl)
            for table, items in rows.items():
                if items:
                    c.executemany('INSERT INTO '+table+' VALUES ('+','.join('?'*len(items[0]))+')', items)
        c.close()

    # Issue 11799: issue itself supplies concrete table values and incorrect vs
    # correct WHERE clauses. These are faithfully translated to SQLite syntax.
    db('composite_key.sqlite', 'CREATE TABLE target (num_column INTEGER, str_column TEXT); CREATE TABLE stage (num_column INTEGER, str_column TEXT);',
        {'target':[(i,'tst') for i in range(1,14)],
         'stage':[(i,'tst' if i<=7 else 'tst2') for i in range(1,14)]})
    (ROOT/'composite_bug.sql').write_text('''DELETE FROM target
WHERE str_column IN (SELECT str_column FROM stage)
  AND num_column IN (SELECT num_column FROM stage);
''', encoding='utf8')
    (ROOT/'composite_fixed.sql').write_text('''DELETE FROM target
WHERE (str_column,num_column) IN (SELECT str_column,num_column FROM stage);
''', encoding='utf8')

    # Issue 5691: day-1 and day-2 values as stated by the reporter, representing
    # the deletion half of an incremental upsert. Not an adapter-level repro.
    db('nullable_key.sqlite', 'CREATE TABLE target (type TEXT, name TEXT, count_values INTEGER); CREATE TABLE stage (type TEXT, name TEXT, count_values INTEGER);',
        {'target':[(None,'onion',5),('fruit','apple',10)],
         'stage':[(None,'onion',7)]})
    (ROOT/'nullable_bug.sql').write_text('''DELETE FROM target
WHERE EXISTS (SELECT 1 FROM stage AS s
 WHERE s.type = target.type AND s.name = target.name);
''', encoding='utf8')
    (ROOT/'nullable_fixed.sql').write_text('''DELETE FROM target
WHERE EXISTS (SELECT 1 FROM stage AS s
 WHERE s.type IS target.type AND s.name IS target.name);
''', encoding='utf8')

    # Issue 10432: reporter didn't supply reproducible warehouse input.
    # Scenario below is a transparent, hypothesis-driven demonstration only.
    db('source_collision.sqlite', 'CREATE TABLE target (key INTEGER, value TEXT); CREATE TABLE stage (key INTEGER, value TEXT);',
        {'target':[(9,'old'),(10,'ok')], 'stage':[(9,'new-1'),(9,'new-2')]})
    (ROOT/'source_collision.sql').write_text('''DELETE FROM target
WHERE key IN (SELECT key FROM stage);
''', encoding='utf8')

    # Issue 11397 makes a critical distinction: a configured unique_key can be
    # the DATE/PARTITION REPLACEMENT key, not a unique single-row ID.
    db('partition_key.sqlite', 'CREATE TABLE target (date_day TEXT, event_id INTEGER, event_type TEXT); CREATE TABLE stage (date_day TEXT, event_id INTEGER, event_type TEXT);',
        {'target':[('2024-02-02',1,'signup'),('2024-02-02',2,'signup'),('2024-02-01',3,'purchase')],
         'stage':[('2024-02-02',1,'signup'),('2024-02-02',2,'signup')]})
    (ROOT/'partition_replace.sql').write_text('''DELETE FROM target
WHERE date_day IN (SELECT date_day FROM stage);
''', encoding='utf8')


    # dbt Fusion #14525: missing source event-time filter, source 10 records
    # emitted in every one of four batches. Row values below are synthetic.
    db('microbatch.sqlite',
       'CREATE TABLE source (event_id INTEGER, event_at TEXT, amount INTEGER);',
       {'source':[(i, f'2020-01-{1+(i%4):02d}', 10*i) for i in range(1,11)]})
    (ROOT/'microbatch_bug.sql').write_text(
       'SELECT event_id,event_at,amount FROM source;\n',encoding='utf8')
    (ROOT/'microbatch_fixed.sql').write_text(
       'SELECT event_id,event_at,amount FROM source WHERE event_at >= :start AND event_at < :end;\n',
       encoding='utf8')

if __name__=='__main__':
    make()
    print('Created independently replayable SQLite issue-mechanism scenarios at',ROOT)
