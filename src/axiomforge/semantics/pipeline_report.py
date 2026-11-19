"""Static, escaped and offline audit report (no external JS/CDN)."""
from __future__ import annotations
import html
from pathlib import Path


def esc(value):
    return html.escape(str(value), quote=True)


def write_html_report(audit: dict, target: str | Path):
    top = audit['top_affected_entities']
    terms = audit['top_provenance_rows']
    rows = ''.join('<tr>' + ''.join(f'<td>{esc(record.get(k, ""))}</td>' for k in ('entity', 'before', 'after', 'residual', 'affected_rows')) + '</tr>' for record in top)
    individual = ''.join('<tr>' + ''.join(f'<td>{esc(record.get(k, ""))}</td>' for k in ('row', 'entity', 'joined_rows', 'retained_after_groupby', 'contribution', 'delta')) + '</tr>' for record in terms)
    checks = ''.join(f'<li>{esc(k)}: {"PASS" if v else "FAIL"}</li>' for k, v in audit['invariant_levels'].items())
    status = audit['verdict']
    materialized = audit.get('materialized')
    materialized_block = ''
    if materialized:
        label = materialized['status']
        changed = materialized['group_differences']
        diffs = ''.join('<tr>' + ''.join(f'<td>{esc(entry.get(k, ""))}</td>' for k in ('segment', 'query_expected', 'file_observed', 'delta')) + '</tr>' for entry in changed)
        materialized_block = (f'<div class="panel"><h2>下游物化结果校核</h2><p><strong>{esc(label)}</strong>；不一致分组：{esc(materialized["mismatched_groups"])}。即使该项一致，业务记录合同仍可能失败。</p>'
                              f'<table><thead><tr><th>分组</th><th>查询结果</th><th>文件结果</th><th>差值</th></tr></thead><tbody>{diffs}</tbody></table></div>')
    ok = status == 'EXACTLY_ONCE_PRESERVED'
    doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AxiomForge 数据语义审计</title><style>
:root {{color-scheme:light}}body {{font-family:"Segoe UI","Microsoft YaHei",sans-serif;max-width:1100px;margin:30px auto;padding:0 24px;background:#f5f7fa;color:#1b2940;line-height:1.6}}
h1 {{font-size:30px}}h2 {{font-size:19px;margin-top:34px}}.panel {{background:white;border:1px solid #dee4ee;border-radius:14px;padding:23px;margin:16px 0;box-shadow:0 2px 5px #dbe2e955}}
.status {{color:{'#187551' if ok else '#ab3c31'};font-size:23px;font-weight:700}}.metrics {{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.metric {{background:#eef2f8;border-radius:8px;padding:15px}}.value {{font-size:24px;font-weight:700}}table {{width:100%;border-collapse:collapse;background:#fff;table-layout:fixed;word-break:break-all}}
th,td {{border-bottom:1px solid #e0e7ee;padding:10px;text-align:left;font-size:13px}}th {{background:#edf2f8}}.note {{color:#596981}} code {{background:#f3f5f9;padding:2px 5px}}
</style></head><body><header><h1>AxiomForge · 数据管道语义审计</h1><p class="note">真实执行：Pandas + SQLite + SQLAlchemy/SQLite。一次性离线 HTML，不加载远程资源。</p></header>
<div class="panel"><div class="status">{esc(status)}</div><p>合同：{esc(audit['contract'])}</p><ul>{checks}</ul></div>
<div class="metrics">{''.join(f'<div class="metric"><div>{esc(k)}</div><div class="value">{esc(v)}</div></div>' for k,v in [('输入事实记录',audit['counts']['fact_rows']),('维度记录',audit['counts']['dimension_rows']),('受影响事实记录',audit['counts']['affected_fact_rows']),('整体残差',audit['global']['residual'])])}</div>
<div class="panel"><h2>为什么总量可能掩盖错误</h2><p>全局前/后：{esc(audit['global']['before'])} / {esc(audit['global']['after'])}。每条事实记录的保留次数可能不同，即使正负残差正好抵消。</p>
<h2>受影响实体</h2><table><thead><tr>{''.join(f'<th>{esc(x)}</th>' for x in ['实体代号','原始值','变换后','残差','异常记录数'])}</tr></thead><tbody>{rows}</tbody></table>
<h2>关键记录的贡献</h2><table><thead><tr>{''.join(f'<th>{esc(x)}</th>' for x in ['行号','实体代号','匹配维度数','有效副本数','值','残差'])}</tr></thead><tbody>{individual}</tbody></table>
</div>{materialized_block}<div class="panel"><h2>范围与不承诺</h2><p class="note">{esc('；'.join(audit['scope']))}。本报告只验证显式的一次贡献合同，不自动判断业务是否允许重复计数。</p></div></body></html>'''
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc, encoding='utf-8')
