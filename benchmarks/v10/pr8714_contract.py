"""Construct stronger, independent checks for the public SQLFluff RF03 DML issue.

These scripts execute real SQLFluff's parser/formatter and a real SQLite state
transition. Not a substitute for project full tests or upstream reviewer approval.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

PREFIX = '''from sqlfluff import fix\nfrom sqlfluff.core import FluffConfig\nimport sqlite3\ncfg=FluffConfig(configs={'core':{'dialect':'postgres','rules':'RF03'},'rules':{'references.consistent':{'single_table_references':'unqualified'}}})\n'''


def execute_dml(query: str, expected: list[tuple[int, int]], finger: str) -> str:
    return PREFIX + f'''q={query!r}\nsql=fix(q,config=cfg,rules=['RF03'])\nassert "id = id" not in sql, {finger!r}\ncon=sqlite3.connect(':memory:')\ncon.executescript("CREATE TABLE orders(id integer primary key,x integer); CREATE TABLE lines(id integer primary key); INSERT INTO orders VALUES(1,0),(2,0),(3,0); INSERT INTO lines VALUES(2);")\ntry:\n con.execute(sql)\n rows=con.execute('SELECT id,x FROM orders ORDER BY id').fetchall()\n assert rows == {expected!r}, ({finger!r},sql,rows)\nexcept (sqlite3.Error, AssertionError) as exc:\n raise AssertionError({finger!r}) from exc\n'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--upstream',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();repo=a.upstream.resolve();a.out.parent.mkdir(parents=True,exist_ok=True)
    d={
        'schema':'axiomforge.issue-contract.v1','issue_url':'https://github.com/sqlfluff/sqlfluff/issues/8714',
        'checkout':str(repo), 'editable_files':['src/sqlfluff/rules/references/RF03.py'],
        'failure_fingerprint':'ISSUE-8714-CORRELATED-DML-DRIFT',
        'reproduce':['${PYTHON}','-c',execute_dml('UPDATE orders AS o SET x = 1 WHERE EXISTS (SELECT 1 FROM lines AS l WHERE l.id = o.id);',[(1,0),(2,1),(3,0)], 'ISSUE-8714-CORRELATED-DML-DRIFT')],
        'control':['${PYTHON}','-c',PREFIX+'''out=fix('SELECT t.a FROM t',config=cfg,rules=['RF03'])\nassert 'SELECT a FROM t' in out and 't.a' not in out, out\n'''],
        'independent_reproductions':[['${PYTHON}','-c',execute_dml('DELETE FROM orders AS o WHERE EXISTS (SELECT 1 FROM lines AS l WHERE l.id = o.id);',[(1,0),(3,0)],'ISSUE-8714-CORRELATED-DML-DRIFT')]],
        'independent_checks':[['${PYTHON}','-c',PREFIX+'''for q in ('SELECT t.a FROM t','SELECT x FROM t'):\n out=fix(q,config=cfg,rules=['RF03'])\n assert 'id = id' not in out, (q,out)\n''']],
        'timeout_seconds':20,'repetitions':2,'isolated_checks':True,
    }
    a.out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(a.out)

if __name__=='__main__':main()
