"""Re-execute a grain preflight receipt rather than trusting embedded statistics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from .grain_preflight import Contract, analyze, PreflightError


def verify_receipt(receipt: str | Path, left: str | Path, right: str | Path) -> dict:
    try:
        original=json.loads(Path(receipt).read_text(encoding='utf-8'))
        d=original['declaration']
        contract=Contract(d['left_key'],d['right_key'],d['fact_id'],d['measure'],
                          d['expect_one_per_fact'],d['require_dimension_match'],d['candidate'])
        current=analyze(left,right,contract,max_issues=20)
        expected={k:v for k,v in original.items() if k not in ('live_database',)}
        # Paths may move across machines. Only compare content hash and signed-off
        # computation, not the original absolute filesystem location.
        expected['source']={k:v for k,v in expected['source'].items() if k.endswith('sha256')}
        current['source']={k:v for k,v in current['source'].items() if k.endswith('sha256')}
        if expected != current:
            raise PreflightError('replay mismatch: statistics, witnesses, input SHA or contract were changed')
    except (TypeError,KeyError,ValueError,json.JSONDecodeError,OSError) as exc:
        raise PreflightError(f'invalid receipt: {exc}') from exc
    return {'status':'EXTERNAL_GRAIN_EVIDENCE_REPLAY_VERIFIED',
            'fact_rows':current['statistics']['fact_rows'],
            'candidate_measure':current['statistics']['candidate_join_measure']}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('receipt');p.add_argument('--left',required=True);p.add_argument('--right',required=True)
    ns=p.parse_args()
    try:
        print(json.dumps(verify_receipt(ns.receipt,ns.left,ns.right),ensure_ascii=False))
    except (PreflightError,OSError) as exc:
        print('REPLAY_REJECTED:',exc,file=sys.stderr)
        raise SystemExit(3)

if __name__=='__main__':main()
