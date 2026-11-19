"""Inspectable offline HTML rendered from evidence; no dashboard backend."""
from __future__ import annotations
import html,json,pathlib
from axiomforge.evidence import verify

def create(evidence:str,output:str)->str:
    verification=verify(evidence,rerun=False)
    data=json.loads(pathlib.Path(evidence).read_text(encoding="utf8"))["payload"]
    rows=[]
    for search in data["searches"]:
        w=search["witnesses"]
        rows.append(f"<tr><td>{html.escape(search['contract']['title'])}</td><td>{search['evaluations']}</td><td>{sum(x['status']=='VERIFIED_RELATIONAL_VIOLATION' for x in search['observations'])}</td><td>{len(w)}</td><td>{search['elapsed_seconds']:.3f}</td></tr>")
    minis=[]
    for m in data["minimized"]:
        minis.append(f"<tr><td>{html.escape(m['target'])}</td><td>{m['before_rows']}</td><td>{m['after_rows']}</td><td>{m['replays']}</td></tr>")
    g=data["guard"]
    content=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AxiomForge · 真实测试证据</title><style>
:root{{color-scheme:light}}body{{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f1f5fa;color:#112438;line-height:1.7}}
main{{max-width:1000px;margin:auto;padding:45px 26px}}h1{{font-size:35px;margin-bottom:0}}small,.muted{{color:#61738b}}
.panel{{background:white;border:1px solid #d7e0ee;border-radius:12px;margin:18px 0;padding:26px;box-shadow:0 2px 8px #183b5410}}
strong{{color:#173568}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;border-bottom:1px solid #e8edf3;padding:11px}}
.kpi{{display:inline-block;margin:14px 25px 0 0}}.kpi b{{font-size:30px;display:block;color:#2456aa}}
code{{background:#eef2f8;padding:3px 6px;border-radius:4px}}.risk{{border-left:4px solid #d68b26;padding-left:18px}}
</style><main><small>ENGINEERING EVIDENCE · BOUNDED / KNOWN UPSTREAM BUGS</small><h1>AxiomForge</h1>
<p class="muted">适用条件合成 × 关系式故障见证 × 可重放证据</p>
<section class="panel"><h2>研究结果</h2>
<span class="kpi"><b>{g['total_domain_cases']}</b>有限域观测</span><span class="kpi"><b>{g['false_positives']}</b>有限域误准入</span>
<span class="kpi"><b>{sum(len(x['witnesses']) for x in data['searches'])}</b>不同输入的已知问题见证</span>
<p>合成适用式：<code>{html.escape(g['formula'])}</code></p>
<p>候选公式：{g['candidate_formulas_considered']}；CEGIS轮数：{g['cegis_rounds']}；完整域采纳：{g['accepted']} / {g['total_domain_cases']}。</p></section>
<section class="panel"><h2>真实库检测</h2><table><tr><th>关系契约</th><th>实际执行次数</th><th>违例观察</th><th>去重见证</th><th>秒数</th></tr>{''.join(rows)}</table>
<p class="muted">上游已知问题，并非本工具首次发现。重复执行不等于独立缺陷数量。</p></section>
<section class="panel"><h2>反例缩减</h2><table><tr><th>目标</th><th>初始数据行</th><th>剩余行</th><th>重放数</th></tr>{''.join(minis)}</table>
<p class="muted">只在删除输入行的语法空间内实现 1-minimal 近似，绝非全局最小证明。</p></section>
<section class="panel risk"><h2>可采性与研发限制</h2><p>{html.escape('；'.join(data['limitations']))}</p>
<p>JSON 哈希只能检查意外损坏，无法独立证明作者身份。发布前应完整执行 <code>axiomforge verify --rerun</code>。</p></section>
<footer class="muted">生成状态：{html.escape(verification['status'])}。原始 JSON 与校验器位于交付包内。</footer></main></html>'''
    p=pathlib.Path(output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding="utf8");return str(p)
