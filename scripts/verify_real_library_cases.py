"""Run genuine installed third-party APIs, not mocked bug outputs.

Historical behavior depends on library versions.  The invocation is a
version-pinned reproduction test, NOT a proof these bugs remain upstream.
"""
from __future__ import annotations
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.runner import run_one,environment
from axiomforge.targets.pandas_cases import input_for
from axiomforge.model import Status


def main():
    cases=[('group-sort',input_for('group-sort',0)),
           ('cow-alias',input_for('cow-alias',0)),
           ('sa-lifecycle',{'stage':'connect','attempts':3})]
    result=[]
    for label,params in cases:
        witness=run_one(label,params,isolated=False)
        assert witness.status==Status.VERIFIED_RELATIONAL_VIOLATION, (label,witness.failure_reason)
        negative_control={
            'group-sort':{'sort':True},
            'cow-alias':{'semantic_change':True},
            'sa-lifecycle':{'stage':'none'},
        }[label]
        # For controls, either abstention or consistent non-failure; never
        # count a semantically different input as a verified same-class bug.
        control=run_one(label,params,control=negative_control)
        assert control.status!=Status.VERIFIED_RELATIONAL_VIOLATION,(label,control)
        result.append({'target':label,'known_upstream_issue':witness.known_issue,
                       'real_API_status':witness.status.value,
                       'independently_sourced_contract':witness.contract,
                       'negative_control':control.status.value,
                       'scope': 'installed historical library version only; no upstream patch'})
    receipt={'schema':'axiomforge.external_library_repro.v1','versions':environment(),
             'cases':result,'warning':'Actual installed pandas / SQLAlchemy historical behaviors; no patches and no durable-server-session assertions'}
    out=ROOT/'results'/'historical_real_library.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(receipt,ensure_ascii=False,sort_keys=True,indent=2)+'\n','utf8')
    for case in result:
        print(case['target'],case['real_API_status'],case['known_upstream_issue'],
              'control='+case['negative_control'])
    print('receipt:',out)
    return 0

if __name__=='__main__':raise SystemExit(main())
