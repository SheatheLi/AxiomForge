"""Turn CSV extracts into a disposable SQLite database, run two candidate plans,
independently verify them with read-only SQL, and publish a small offline report.
"""
from __future__ import annotations
import argparse
import csv
from html import escape
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.semantics.grain_preflight import Contract, analyze, verify_readonly_database, _read_csv, _ident


def _load_table(db, name, path, numeric):
    fields,rows=_read_csv(path)
    coldecl=','.join(f'{_ident(k)} {"INTEGER" if k in numeric else "TEXT"}' for k in fields)
    db.execute(f'CREATE TABLE {_ident(name)} ({coldecl})')
    insert='INSERT INTO '+_ident(name)+' VALUES ('+','.join('?' for _ in fields)+')'
    values=[[int(row[k]) if k in numeric else row[k] for k in fields] for row in rows]
    db.executemany(insert,values)


def _html_report(raw,safe,source):
    a,b=raw['statistics'],safe['statistics']
    entries=''.join('<tr><td>'+escape(str(r['fact_id']))+'</td><td>'+str(r['surviving_copies'])+'</td><td>'+str(r['duplicated_amount'])+'</td></tr>' for r in raw['affected'][:15])
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AxiomForge / SQL 粒度回归</title>
<style>body{{font:16px system-ui,sans-serif;max-width:980px;margin:44px auto;padding:0 24px;line-height:1.55;color:#17212b}}h1,h2{{line-height:1.25}}.stats{{display:flex;gap:16px;flex-wrap:wrap}}.stat{{border:1px solid #cbd5e1;border-radius:12px;padding:18px;flex:1;min-width:200px}}strong{{font-size:30px}}.bad{{color:#a62525}}.good{{color:#137044}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid #ddd;padding:10px;text-align:left}}th{{background:#f2f5f7}}code,pre{{font:13px ui-monospace,monospace;white-space:pre-wrap;background:#f3f5f7;padding:12px;display:block}}.note{{color:#536271}}footer{{margin-top:35px}}</style>
<h1>AxiomForge｜SQL 改动粒度回归预检</h1><p>源数据：{escape(source)}。两种候选 JOIN 由同一数据集实际执行并核对，不代表上游项目存在生产缺陷。</p>
<div class="stats"><div class="stat">原始事实行数<br><strong>{a['fact_rows']}</strong></div><div class="stat">危险直接关联<br><strong class="bad">{a['candidate_join_rows']}</strong></div><div class="stat">安全预聚合<br><strong class="good">{b['candidate_join_rows']}</strong></div></div>
<div class="stats"><div class="stat">原始度量<br><strong>{a['fact_measure_before']}</strong></div><div class="stat">直接关联后度量<br><strong class="bad">{a['candidate_join_measure']}</strong></div><div class="stat">安全预聚合后度量<br><strong class="good">{b['candidate_join_measure']}</strong></div></div>
<h2>变更门禁</h2><p class="bad">直接 JOIN：{raw['status']}</p><p class="good">预聚合 JOIN：{safe['status']}</p>
<h2>受影响业务键（前 15）</h2><table><tr><th>事实 ID</th><th>关联副本数</th><th>度量额外贡献</th></tr>{entries}</table>
<h2>SQL 诊断设计</h2><pre>{escape(raw['live_database']['diagnostic_sql'])}</pre>
<footer class="note">⚠️ 本工具检查的是显式声明的事实粒度，不自动解析任意 dbt SQL；实际是否违反业务语义取决于已确认的数据合同。金额保持源文件单位，不能宣称为真实营业收入。</footer></html>'''


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--external',action='store_true',help='use upstream jaffle-shop CSVs downloaded separately')
    p.add_argument('--out-dir',default='artifacts/grain_demo')
    ns=p.parse_args(argv)
    d=ROOT/'examples'/('jaffle_shop' if ns.external else 'grain_demo')
    f=d/('raw_products.csv' if ns.external else 'facts.csv')
    r=d/('raw_supplies.csv' if ns.external else 'supplies.csv')
    fact_id='sku' if ns.external else 'id'
    out=ROOT/ns.out_dir
    out.mkdir(parents=True,exist_ok=True)
    dbfile=out/'preflight.sqlite'
    if dbfile.exists():dbfile.unlink()
    conn=sqlite3.connect(dbfile)
    try:
        _load_table(conn,'raw_products',f,{'price'})
        _load_table(conn,'raw_supplies',r,{'cost'})
        conn.commit()
    finally:conn.close()
    results={}
    for candidate in ('raw_join','preaggregated_join'):
        contract=Contract('sku','sku',fact_id,'price',True,True,candidate)
        result=analyze(f,r,contract)
        result['live_database']=verify_readonly_database(dbfile,'raw_products','raw_supplies',contract,result)
        (out/(candidate+'.json')).write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        results[candidate]=result
    assert results['raw_join']['status']=='BLOCK_GRAIN_REGRESSION'
    assert results['preaggregated_join']['status']=='PASS_GRAIN_CONTRACT'
    report=_html_report(results['raw_join'],results['preaggregated_join'],'jaffle-shop' if ns.external else 'self-owned offline demonstration')
    (out/'report.html').write_text(report,encoding='utf-8')
    print(json.dumps({'unsafe':results['raw_join']['statistics'],'safe':results['preaggregated_join']['statistics'],
                      'report':str(out/'report.html')},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
