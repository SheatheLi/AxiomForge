"""Apply candidate diff to a throwaway copy of pinned HTTPcore and run real TCP reproduction.

Never writes to installed site-packages or the user's checked-out HTTPcore.
"""
from __future__ import annotations
import argparse,hashlib,importlib,json,os,pathlib,shutil,subprocess,sys,tempfile
from axiomforge.issue_engine.transactional_cleanup import upstream_rearm_patch

ROOT=pathlib.Path(__file__).resolve().parents[2]
def run(repetitions:int=4):
    import httpcore
    if httpcore.__version__!='1.0.9':raise RuntimeError('requires httpcore 1.0.9')
    installed=pathlib.Path(httpcore.__file__).parent
    with tempfile.TemporaryDirectory(prefix='axiomforge-source-patch-') as tmp:
        staging=pathlib.Path(tmp)
        shutil.copytree(installed,staging/'httpcore')
        patch=(staging/'candidate.patch')
        patch.write_text(upstream_rearm_patch(),encoding='utf8')
        check=subprocess.run(['patch','--dry-run','-p1'],input=patch.read_bytes(),cwd=staging,capture_output=True)
        if check.returncode:raise RuntimeError(check.stderr.decode()+check.stdout.decode())
        applied=subprocess.run(['patch','-p1'],input=patch.read_bytes(),cwd=staging,capture_output=True,check=True)
        runner=ROOT/'benchmarks/resource_protocol/httpx_double_cancel_local.py'
        out=staging/'patched_results.json'
        env={**os.environ,'PYTHONPATH':str(staging)+os.pathsep+str(ROOT/'src')}
        command=[sys.executable,str(runner),'--out',str(out),'--repetitions',str(repetitions)]
        process=subprocess.run(command,env=env,cwd=ROOT,capture_output=True,text=True,timeout=35)
        if process.returncode:
            raise RuntimeError(process.stderr[-3000:])
        raw=json.loads(out.read_text())
        return {'repetitions':repetitions,'applied_diff_sha256':hashlib.sha256(patch.read_bytes()).hexdigest(),
                'patch_dry_run':True,'independent_python_process':True,
                'single_active':[e.get('active_pool_connections') for e in raw['single']['rows']],
                'double_active':[e.get('active_pool_connections') for e in raw['double']['rows']],
                'double_blocked':sum(bool(e.get('inconclusive_or_blocked')) for e in raw['double']['rows']),
                'scope':'Throwaway patched HTTPcore source copy; independent process; NOT upstream merged or production-proven'}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=pathlib.Path,required=True)
    ap.add_argument('--repetitions',type=int,default=4)
    args=ap.parse_args();result=run(args.repetitions)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(result,indent=2))
