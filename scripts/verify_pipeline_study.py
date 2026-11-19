"""Fail closed: checksum and reexecute *all* declared scenario trials."""
from __future__ import annotations
import argparse,hashlib,json,pathlib
from run_pipeline_study import run


def verify(path):
    raw=json.loads(pathlib.Path(path).read_text(encoding='utf8'))
    if set(raw)!={'sha256','result'}:raise ValueError('unsupported study envelope')
    result=raw['result']
    data=json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf8')
    if raw['sha256']!=hashlib.sha256(data).hexdigest():raise ValueError('study hash mismatch')
    if result['schema']!='axiomforge.pipeline_calibration.v1':raise ValueError('study schema mismatch')
    reproduced=run(result['seed_count'])
    # Runtime is observational and must not be compared byte-for-byte.
    for obj in (reproduced,result):obj.pop('elapsed_s_observed',None)
    if reproduced!=result:raise ValueError('study mismatch after full independent scenario reexecution')
    return {'status':'STUDY_ALL_WORLDS_REPLAYED','worlds':len(result['trials']),
            'backend_runs':len(result['trials'])*3}


def main():
    p=argparse.ArgumentParser();p.add_argument('path')
    args=p.parse_args()
    print(json.dumps(verify(args.path),indent=2))

if __name__=='__main__':main()
