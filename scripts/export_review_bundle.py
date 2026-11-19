"""Export a local review candidate only after strong executable evidence.

This is NOT a GitHub PR, a CI result, or a claim of maintainer endorsement.
All user-provided commands execute: only use trusted upstream checkouts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from axiomforge.issue_engine.core import IssueError, evaluate, load_contract
from axiomforge.issue_engine.ablation import audit_hunk_necessity


def build_bundle(contract_path:Path, patch_path:Path, output:Path, *,
                 title:str,summary:str, max_seconds:float=180)->dict:
    contract=load_contract(contract_path.resolve())
    evidence=evaluate(contract,patch_path.resolve())
    basic={
      'case':contract.issue_url,
      'patch_sha256':hashlib.sha256(patch_path.read_bytes()).hexdigest(),
      'isolated_checks':contract.isolated_checks,
      'repetitions':contract.repetitions,
      'independent_check_count':len(contract.independent_checks),
      'independent_failure_count':len(contract.independent_reproductions),
      'verified_declared_tests':bool(evidence['passed'])}
    reasons=[]
    if not evidence['passed']:reasons.append('the patch failed executable before/after checks')
    if not contract.isolated_checks:reasons.append('isolated_checks must be true')
    if contract.repetitions<2:reasons.append('repetitions must be at least 2')
    if not contract.independent_checks:reasons.append('add independent normal-behavior checks')
    if not contract.independent_reproductions:reasons.append('add independent failure variants')
    if reasons:
      return {**basic,'status':'BLOCKED_EVIDENCE','reasons':reasons,
        'claim':'Not suitable for automatic PR-ready labeling'}
    ablation=audit_hunk_necessity(contract,patch_path,max_hunks=12,max_seconds=max_seconds)
    if not ablation.get('evidence_relative_necessary'):
      return {**basic,'status':'BLOCKED_ABLATION','ablation':ablation,
        'claim':'Some changes lack necessary evidence under this contract'}
    if output.exists():raise IssueError('refusing to overwrite existing evidence bundle')
    output.mkdir(parents=True)
    shutil.copyfile(patch_path,output/'candidate.patch')
    (output/'contract.json').write_text(json.dumps(json.loads(contract_path.read_text('utf8')),
                        ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (output/'execution_receipt.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (output/'hunk_audit.json').write_text(json.dumps(ablation,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    issue=contract.issue_url
    markdown=(f'# {title}\n\n'
       f'**Public issue:** {issue}\n\n'
       f'## Why this matters\n\n{summary}\n\n'
       '## Local evidence (not an upstream endorsement)\n\n'
       '- Reproduced the specific original failure with its fingerprint.\n'
       '- Candidate repaired the declared reproductions and independent failure variants.\n'
       '- Baseline and patched normal behavior were checked independently.\n'
       f'- Ran {contract.repetitions} reproduction repetitions with isolated test snapshots.\n'
       '- Individual diff hunks survived evidence-relative removal checks.\n'
       '- See execution_receipt.json and hunk_audit.json for scope and exceptions.\n\n'
       '## Before filing upstream\n\n'
       '- [ ] Review every changed line and independently understand the mechanism.\n'
       '- [ ] Run the target repository full test suite and linters on current main.\n'
       '- [ ] Confirm no equivalent active PR and obtain maintainer consent if needed.\n'
       '- [ ] Verify the patch rebases and builds against current default branch.\n'
       '- [ ] Inspect the bundle for local paths, potentially sensitive logs, or tokens.\n'
       '- [ ] Identify actual human author and disclose material AI assistance accurately.\n\n'
       '## AI assistance disclosure\n\n'
       'This repair candidate was produced or validated with AI-assisted tooling. '
       'The human submitter must inspect, test, and take responsibility for it. '
       'Do not claim upstream acceptance or merge before it happens.\n')
    (output/'PR_DRAFT.md').write_text(markdown,encoding='utf8')
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}
    (output/'FILES_SHA256.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf8')
    return {**basic,'status':'LOCAL_REVIEW_PACKAGE','bundle':str(output),
        'ablation_passed':True,'files_sha256':hashes,
        'claim':'Ready for *human review*; not an accepted upstream PR; CI and maintainers not checked.'}


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--contract',required=True,type=Path)
    p.add_argument('--patch',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--title',required=True)
    p.add_argument('--summary',required=True)
    p.add_argument('--allow-exec',action='store_true',required=True)
    p.add_argument('--max-seconds',type=float,default=180)
    a=p.parse_args()
    try:
        receipt=build_bundle(a.contract,a.patch,a.out,title=a.title,summary=a.summary,
                             max_seconds=a.max_seconds)
        print(json.dumps(receipt,ensure_ascii=False,indent=2))
        return 0 if receipt['status']=='LOCAL_REVIEW_PACKAGE' else 2
    except (IssueError,OSError,ValueError) as e:
        print(f'Cannot export candidate: {e}',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
