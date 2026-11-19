"""No post-hoc rewrite of the v1.1 operator source or public-Issue cohort."""
from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parents[2]

# The distributable source snapshot does not contain the frozen v1.1 proof
# artifacts. Never synthesize a historical checksum/benchmark to make a
# provenance test pass. Preserve these tests and run them when the original
# archive is supplied; until then they are explicitly unverified.
import pytest
_FROZEN_EVIDENCE = (
    'artifacts/v11_cross_repository/v11_operator_freeze.json',
    'artifacts/v11_cross_repository/development_evaluation.json',
    'artifacts/v11_cross_repository/equal_budget_comparison.json',
    'benchmarks/crossrepo_v11/manifest.json',
    'benchmarks/unseen_v11/manifest.json',
    'benchmarks/unseen_v11/results.json',
)
_missing_evidence = [name for name in _FROZEN_EVIDENCE if not (ROOT / name).is_file()]
pytestmark = pytest.mark.skipif(
    bool(_missing_evidence),
    reason='historical v1.1 provenance not bundled: ' + ', '.join(_missing_evidence),
)


def test_v11_historical_freeze_is_immutable_but_is_not_the_v12_live_engine():
    # V1.1's SHA-256 is evidence of *its* operator snapshot, not a command to
    # keep the live repair engine frozen forever. Never silently update it.
    old_file=ROOT/'artifacts/v11_cross_repository/v11_operator_freeze.json'
    prereg=json.loads((ROOT/'benchmarks/unseen_v11/manifest.json').read_text())
    assert hashlib.sha256(old_file.read_bytes()).hexdigest()==prereg['source_freeze_sha256']
    payload=json.loads(old_file.read_text())
    assert payload['version']=='1.1.0'
    assert len(payload['files'])>=1
    assert all(len(digest)==64 for digest in payload['files'].values())
    from axiomforge import __version__
    declared = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (ROOT/'pyproject.toml').read_text())
    assert declared is not None
    assert __version__ == declared.group(1)
    assert __version__ != payload['version']
    # A v1.2 holdout must not reuse the old 'unseen' classification.
    assert any(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=digest
               for rel,digest in payload['files'].items())


def test_benchmark_spans_four_projects_and_keeps_nonreproducible_case():
    payload=json.loads((ROOT/'benchmarks/crossrepo_v11/manifest.json').read_text())
    assert len(payload['cases'])==6
    repos=[entry['repo'] for entry in payload['cases']]
    assert set(repos)=={'requests','urllib3','sqlalchemy','httpcore'}
    assert 'sqlfluff' not in repos
    results=json.loads((ROOT/'artifacts/v11_cross_repository/development_evaluation.json').read_text())
    assert len(results['cases'])==6
    assert results['summary']=={'VERIFIED_CANDIDATE':5,'NOT_REPRODUCIBLE':1}
    for item in results['cases']:
        if item['status']=='VERIFIED_CANDIDATE':
            assert item['receipt']['passed'] is True
            assert item['patch_sha256']==hashlib.sha256(item['patch'].encode()).hexdigest()


def test_equal_budget_report_does_not_claim_blind_generalization():
    p=json.loads((ROOT/'artifacts/v11_cross_repository/equal_budget_comparison.json').read_text())
    assert p['blind_score']=='NOT_MEASURED'
    assert p['v1_0']=={'NOT_REPRODUCIBLE':1,'NOT_SOLVED':5}
    assert p['v1_1']=={'NOT_REPRODUCIBLE':1,'VERIFIED_CANDIDATE':5}
    assert p['accepted_pr']==p['upstream_pr_submitted']==0


def test_v11_frozen_holdout_keeps_negative_results():
    manifest=json.loads((ROOT/'benchmarks/unseen_v11/manifest.json').read_text())
    result=json.loads((ROOT/'benchmarks/unseen_v11/results.json').read_text())
    assert len(manifest['cases'])==len(result['cases'])==2
    assert {x['repo'] for x in manifest['cases']}=={'requests','httpcore'}
    assert {x['status'] for x in result['cases']}=={'NOT_SOLVED'}
