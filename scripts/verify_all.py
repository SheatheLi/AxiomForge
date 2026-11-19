"""分文件、可恢复的全量回归执行器。不会将超时、跳过或缺证据项伪称通过。

用法：python scripts/verify_all.py --mode full --per-file-seconds 180
      python scripts/verify_all.py --mode quick --per-file-seconds 40
结果：evidence/regression/summary.json 与各测试文件独立日志。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
HEAVY = {'test_composed_repair.py','test_core.py','test_localization.py','test_native_triage.py','test_review_export.py','test_v12_oracle_audit.py'}

def main() -> int:
    p = argparse.ArgumentParser(description='AxiomForge 全量/快速回归：按文件隔离并保留完整诊断')
    p.add_argument('--mode', choices=['quick','full'], default='full')
    p.add_argument('--per-file-seconds', type=int, default=180)
    p.add_argument('--output', type=Path, default=ROOT/'evidence'/'regression')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tests = sorted((ROOT/'tests').rglob('test_*.py'))
    if args.mode == 'quick': tests = [f for f in tests if f.name not in HEAVY]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT/'src') + os.pathsep + env.get('PYTHONPATH','')
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env.setdefault('OPENBLAS_NUM_THREADS','1')
    env.setdefault('OMP_NUM_THREADS','1')
    report = {'status':'in_progress','mode':args.mode,'started_at_utc':datetime.now(timezone.utc).isoformat(),
              'python':sys.version,'per_file_timeout_seconds':args.per_file_seconds,
              'total_test_files':len(tests),'results':[]}
    for i, f in enumerate(tests,1):
        rel = f.relative_to(ROOT).as_posix()
        cmd = [sys.executable,'-m','pytest','-q','--tb=short','--disable-warnings',rel]
        t0=time.monotonic()
        proc = subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                start_new_session=True)
        try:
            log, _ = proc.communicate(timeout=args.per_file_seconds)
            status='passed' if proc.returncode==0 else 'failed'
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGKILL)
            log, _=proc.communicate()
            status='timed_out'
        duration=round(time.monotonic()-t0,3)
        logname = '__'.join(f.relative_to(ROOT/'tests').parts).removesuffix('.py')+'.log'
        (args.output/logname).write_text(log,encoding='utf-8')
        row={'test_file':rel,'status':status,'duration_s':duration,'log':logname,
             'pytest_summary':next((x for x in reversed(log.splitlines()) if re.search(r'\b(passed|failed|skipped|error|ERRORS)\b',x)),None)}
        report['results'].append(row)
        (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'[{i}/{len(tests)}] {rel}: {status} ({duration:.1f}s)',flush=True)
    totals={'passed':0,'skipped':0,'failed':0,'timed_out_files':0}
    for row in report['results']:
        summary=row.get('pytest_summary') or ''
        for key in ('passed','skipped','failed'):
            m=re.search(rf'(?<![0-9])(\d+)\s+{key}\b',summary)
            if m: totals[key]+=int(m.group(1))
        if row['status']=='timed_out': totals['timed_out_files']+=1
    report['case_counts']=totals
    all_executed=all(row['status']=='passed' for row in report['results'])
    report['status']=('passed_with_skips' if totals['skipped'] else 'passed') if all_executed else 'incomplete'
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('FINAL STATUS: '+report['status']+' '+json.dumps(totals,ensure_ascii=False),flush=True)
    return 0 if report['status'] in {'passed','passed_with_skips'} else 1
if __name__ == '__main__':raise SystemExit(main())
