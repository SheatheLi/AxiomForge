"""Evidence-only summarizer; rejects absent or contradictory receipts."""
from __future__ import annotations
import argparse, json, pathlib

HERE=pathlib.Path(__file__).resolve().parent

def gather():
    live=json.loads((HERE/'live_results.json').read_text())
    patched=json.loads((HERE/'patched_process_results.json').read_text())
    repeat=json.loads((HERE/'stability_results.json').read_text())
    sql=json.loads((HERE/'sqlalchemy_crosslayer_results.json').read_text())
    entries={(x['strategy'],x['mode']):x for x in live['strategies']}
    assert [x['active'] for x in entries['baseline','double']['rows'][:3]]==[1,2,3]
    assert entries['baseline','double']['blocked_runs']==1
    assert entries['shield-only','double']['blocked_runs']==1
    for strategy in ('rearm-after-cancellation','defer-commit','singleflight-commit'):
        item=entries[strategy,'double']
        assert item['blocked_runs']==0
        assert [x['active'] for x in item['rows']]==[0,0,0,0]
    assert patched['double_active']==[0,0,0,0] and patched['double_blocked']==0
    assert repeat['replications']==3
    for repl in repeat['data']:
        for s in repl['strategies']:
            if s['mode']!='double':continue
            if s['strategy'] in ('baseline','shield-only'):
                assert s['max_active']==3 and s['blocked']==1
            else: assert s['max_active']==0 and s['blocked']==0
    for k,expected in (('connect-failure',5),('manual-close',0),('healthy',0)):
        assert len(sql['cases'][k]['protocol_before_dispose']['findings'])==expected
    return {'project':'AxiomForge','version':'1.6.0','evidence_class':'fixed-version local engineering reproduction',
            'live':{'baseline_double_active':[1,2,3], 'baseline_fourth_timeout':True,
                    'shield_only_fourth_timeout':True,
                    'repair_candidate_4_step_active':[0,0,0,0],
                    'repair_candidates':['rearm-after-cancellation','defer-commit','singleflight-commit']},
            'repeat_experiments':3,'independent_patched_source_process':True,
            'sqlalchemy_orphan_event_count':5,
            'boundaries':['no upstream PR','not a novel formal algorithm',
                          'no industrial repair success rate','not a multi-backend production patch',
                          'historical full regression not confirmed']}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=pathlib.Path,required=True)
    args=ap.parse_args()
    out=gather();args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(out,indent=2,ensure_ascii=False))
