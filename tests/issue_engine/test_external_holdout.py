"""Fail closed when the external holdout has been tuned after its freeze."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'benchmarks'/'external_holdout'


def loader():
    spec=importlib.util.spec_from_file_location('axiomforge_benchmark_runner',DATA/'run_benchmark.py')
    module=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_eight_external_cases_across_three_upstreams_are_reproducibly_registered():
    doc=json.loads((DATA/'preregistered_cases.json').read_text('utf8'))
    scripts=json.loads((DATA/'case_scripts.json').read_text('utf8'))
    assert doc['schema']=='axiomforge.external-holdout.v1'
    assert len(scripts)==len(doc['cases'])==8
    assert {x['repo'] for x in scripts}=={'sqlfluff','sqlalchemy','httpcore'}
    assert len({x['id'] for x in scripts})==8
    for row,meta in zip(scripts,doc['cases']):
        assert row['id']==meta['id']
        assert hashlib.sha256(row['repro'].encode()).hexdigest()==meta['repro_sha256']
        assert hashlib.sha256(row['control'].encode()).hexdigest()==meta['control_sha256']
        assert '/issues/' in meta['url']
        assert not any(token in row['repro']+row['control'] for token in ('/mnt/data/','/tmp/'))


def test_original_frozen_operator_sources_are_archived_immutably():
    loader().check_historical_archive(json.loads((DATA/'preregistered_cases.json').read_text('utf8')))


def test_upgraded_engine_must_not_claim_original_operator_freeze():
    with pytest.raises(RuntimeError,match='FROZEN SOURCE MISMATCH'):
        loader().check_freeze(json.loads((DATA/'preregistered_cases.json').read_text('utf8')))


def test_operator_freeze_detects_modified_source():
    runner=loader()
    doc=json.loads((DATA/'preregistered_cases.json').read_text('utf8'))
    mutated=json.loads(json.dumps(doc))
    key=next(iter(mutated['frozen_operator_sha256']))
    mutated['frozen_operator_sha256'][key]='0'*64
    with pytest.raises(RuntimeError,match='FROZEN SOURCE MISMATCH'):
        runner.check_freeze(mutated)


def test_internal_generic_baseline_disables_structural_mutators_without_changing_code():
    from axiomforge.issue_engine import state_repair
    original=state_repair.borrowed_lookup_candidates
    with loader().no_structural_generators():
        assert list(state_repair.borrowed_lookup_candidates(None,'','x.py'))==[]
    assert state_repair.borrowed_lookup_candidates is original


def test_original_v08_results_remain_byte_identical_after_expanding_repair_grammar():
    frozen = DATA/'results.json'
    manifest = DATA/'preregistered_cases.json'
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == (
        '537acf57cbed67be8d8517af20bdcfa1b64ae6839183e3cd82dc0e39eb7fb032')
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == (
        '87882a0524db5f2edfbade91dcd34b0e6ab94b15403f3f5783e253b27609bd56')
    record=json.loads(frozen.read_text('utf8'))
    assert record['summary']['verified_full']==0
    assert record['summary']['verified_generic']==0
