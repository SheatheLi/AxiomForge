"""Cross-repository evidence, without equating candidate patches with PRs.

This is a bounded executable workload, not a list of hyperlinks or a scoring
spreadsheet. Every credited issue must pass its own before/after contract.
"""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from .core import IssueError, load_contract, evaluate


def review_portfolio(manifest: Path) -> dict:
    doc=json.loads(manifest.read_text(encoding='utf8'))
    if doc.get('schema')!='axiomforge.portfolio.v1':
        raise IssueError('unsupported portfolio manifest')
    cases=doc.get('cases')
    if not isinstance(cases,list) or not cases or len(cases)>20:
        raise IssueError('portfolio must have 1..20 explicit cases')
    outcomes=[];issues=set();projects=set()
    for item in cases:
        if not isinstance(item,dict) or not isinstance(item.get('contract'),str) or not isinstance(item.get('patch'),str):
            raise IssueError('each case must specify a contract and patch')
        evidence_source=item.get('patch_authority')
        if evidence_source not in ('user_provided','upstream_existing','assistant_authored','axiomforge_synthesized'):
            raise IssueError('patch_authority must explicitly acknowledge who proposed the repair')
        c=load_contract((manifest.parent/item['contract']).resolve())
        if c.issue_url in issues:
            raise IssueError('duplicate public issue cannot increase the evidence count')
        parts=urlsplit(c.issue_url).path.strip('/').split('/')
        project='/'.join(parts[:2])
        issues.add(c.issue_url);projects.add(project)
        patch_path = (manifest.parent/item['patch']).resolve()
        repair=evaluate(c,patch_path)
        # An arbitrary JSON label is NOT evidence of autonomous synthesis.
        # Re-run the generator on the original checkout and demand a byte-for-
        # byte match for the patch being credited. A human-provided patch may
        # pass all tests but must not inflate the generated-candidate count.
        generation_verified = False
        generation_details = None
        if evidence_source == 'axiomforge_synthesized':
            source_file = item.get('generated_source_file')
            if not isinstance(source_file, str) or source_file not in c.editable_files:
                raise IssueError('synthesized portfolio case requires generated_source_file in editable_files')
            from .synthesis import synthesize
            replay = synthesize(c, source_file,
                                max_candidates=int(item.get('max_candidates', 30)),
                                max_seconds=float(item.get('max_seconds', 90)))
            generation_verified = (
                replay.get('status') == 'VERIFIED_CANDIDATE' and
                hashlib.sha256(replay['patch'].encode('utf8')).digest() ==
                hashlib.sha256(patch_path.read_bytes()).digest())
            generation_details = {'status':replay.get('status'),
                                  'operator':replay.get('operator'),
                                  'attempts':replay.get('attempts'),
                                  'patch_exact_match':generation_verified}
        outcomes.append({'issue_url':c.issue_url,'external_project':project,
                         'patch_authority':evidence_source,
                         'candidate_validated':repair['passed'],
                         'generator_replayed':generation_verified,
                         'generator_replay':generation_details,
                         'full_evidence':repair})
    successful=sum(x['candidate_validated'] for x in outcomes)
    synthesized=sum(x['candidate_validated'] and x['generator_replayed']
                    for x in outcomes)
    return {'schema':'axiomforge.portfolio-evidence.v1',
            'issue_count':len(issues),'distinct_external_repositories':len(projects),
            'locally_verified_candidate_patches':successful,
            'axiomforge_synthesized_candidates':synthesized,
            'upstream_prs_submitted':0,'upstream_prs_merged':0,
            'all_candidate_checks_passed':successful==len(issues),
            'all_authorship_claims_replayed':all(
                e['patch_authority']!='axiomforge_synthesized' or e['generator_replayed']
                for e in outcomes),
            'evidence':outcomes,
            'warning':'Only byte-identical locally re-generated patches count as AxiomForge synthesis; local verification is not upstream adoption.'}
