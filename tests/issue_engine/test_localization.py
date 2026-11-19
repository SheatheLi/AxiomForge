"""Black-box fault localization and validation tests; projects are generated anew."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from axiomforge.issue_engine.core import IssueError, evaluate, load_contract
from axiomforge.issue_engine.localization import localize
from axiomforge.issue_engine.search import synthesize_repository


def make_example(tmp_path: Path):
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo/'unrelated.py').write_text('def healthy():\n    return 42\n', encoding='utf8')
    (repo/'service.py').write_text('''def value(n):
    if n > 0:
        return n - 1
    return n
''', encoding='utf8')
    (repo/'failure.py').write_text('''from service import value
assert value(4) == 5, "ISSUE-RECORDED-6207"
''', encoding='utf8')
    (repo/'pass_case.py').write_text('''from service import value
from unrelated import healthy
assert value(0) == 0
assert healthy() == 42
''', encoding='utf8')
    (repo/'holdout.py').write_text('''from service import value
assert value(8) == 9, "ISSUE-RECORDED-6207"
''', encoding='utf8')
    (repo/'negative_holdout.py').write_text('''from service import value
assert value(0) == 99
''', encoding='utf8')
    data = {'schema':'axiomforge.issue-contract.v1',
            'issue_url':'https://github.com/example/project/issues/6207',
            'checkout':'repo','reproduce':['${PYTHON}','failure.py'],
            'control':['${PYTHON}','pass_case.py'],
            'editable_files':['unrelated.py','service.py'],
            'failure_fingerprint':'ISSUE-RECORDED-6207',
            'repetitions':1,'timeout_seconds':8,
            'independent_checks': [['${PYTHON}','pass_case.py']],
            'independent_reproductions': [['${PYTHON}','holdout.py']]}
    path=tmp_path/'contract.json';path.write_text(json.dumps(data), encoding='utf8')
    return repo,path


def test_trace_ochiai_ranks_failure_line_and_does_not_change_sources(tmp_path):
    repo, contract_file = make_example(tmp_path)
    original = (repo/'service.py').read_bytes()
    result = localize(load_contract(contract_file))
    assert result['status']=='TRACED'
    assert result['contrast_available']
    assert result['sites'][0]['file']=='service.py'
    assert result['sites'][0]['line']==3
    assert result['sites'][0]['score']==1.0
    assert result['file_ranking'][0]['file']=='service.py'
    assert (repo/'service.py').read_bytes()==original


def test_repository_search_uses_fault_spectrum_and_independent_checks(tmp_path):
    repo, path = make_example(tmp_path)
    outcome = synthesize_repository(load_contract(path), max_candidates_per_file=6,
                                     max_seconds=35,use_spectrum=True)
    assert outcome['status']=='VERIFIED_CANDIDATE',outcome
    assert 'return n + 1' in outcome['patch']
    assert outcome['repository_search']['spectrum_guided']
    assert outcome['repository_search']['checked'][0]['file']=='service.py'
    assert outcome['receipt']['independent_check_count']==1
    assert outcome['receipt']['evidence_tier']=='INDEPENDENT_FAILURES_AND_CONTROLS'
    assert outcome['receipt']['independent_reproduction_count']==1
    assert (repo/'service.py').read_text().splitlines()[2]=='        return n - 1'


def test_independent_holdout_rejects_spurious_repair(tmp_path):
    repo,path=make_example(tmp_path)
    data=json.loads(path.read_text())
    data['independent_checks']=[['${PYTHON}','negative_holdout.py']]
    path.write_text(json.dumps(data))
    result=synthesize_repository(load_contract(path),max_candidates_per_file=3,max_seconds=10)
    assert result['status']=='NOT_SOLVED'
    assert result['attempts']>=1


def test_localize_rejects_untraced_failure(tmp_path):
    repo,path=make_example(tmp_path)
    data=json.loads(path.read_text());data['failure_fingerprint']='WRONG-FINGERPRINT'
    path.write_text(json.dumps(data))
    with pytest.raises(IssueError,match='instrumented failure'):
        localize(load_contract(path))


def test_instrumented_python_dash_c_entry_has_no_false_coverage(tmp_path):
    repo, path = make_example(tmp_path)
    cfg=json.loads(path.read_text())
    cfg['reproduce']=['${PYTHON}','-c',
                      'import service; assert service.value(4)==5, "ISSUE-RECORDED-6207"']
    cfg['control']=['${PYTHON}','-c',
                    'import service; assert service.value(0)==0']
    path.write_text(json.dumps(cfg))
    spectra=localize(load_contract(path))
    assert spectra['contrast_available']
    assert spectra['sites'][0]['line']==3


def test_shared_failure_and_control_spectra_disables_misleading_location(tmp_path):
    repo, path = make_example(tmp_path)
    # Both failing and passing paths execute exactly the same production code;
    # the assertion differs outside the allowed source file.
    cfg=json.loads(path.read_text())
    cfg['control']=['${PYTHON}','-c',
                    'import service; assert service.value(4)==3']
    path.write_text(json.dumps(cfg))
    spectra=localize(load_contract(path))
    assert not spectra['contrast_available']
    assert spectra['contrasting_sites']==0
    assert 'not evidence of a fault location' in spectra['limitations']


def test_independent_reproducer_must_fail_with_same_identity(tmp_path):
    repo, path = make_example(tmp_path)
    cfg=json.loads(path.read_text())
    cfg['independent_reproductions']=[['${PYTHON}','pass_case.py']]
    path.write_text(json.dumps(cfg))
    from axiomforge.issue_engine.synthesis import synthesize
    result=synthesize(load_contract(path),'service.py',max_candidates=5,max_seconds=25)
    assert result['status']=='NOT_SOLVED'
    assert any('baseline independent reproductions' in r['reason'] for r in result['rejections'])


def test_invalid_baseline_is_never_cached_between_candidates(tmp_path):
    from axiomforge.issue_engine.core import CandidateEvaluator
    from axiomforge.issue_engine.synthesis import to_patch
    repo,path=make_example(tmp_path)
    cfg=json.loads(path.read_text())
    cfg['independent_reproductions']=[['${PYTHON}','pass_case.py']]
    path.write_text(json.dumps(cfg))
    source=(repo/'service.py').read_text()
    patch=tmp_path/'candidate.patch'
    patch.write_text(to_patch('service.py',source,source.replace('return n - 1','return n + 1')))
    with CandidateEvaluator(load_contract(path)) as evaluator:
        for _ in range(2):
            with pytest.raises(IssueError,match='baseline independent reproductions'):
                evaluator.evaluate_patch(patch)


def test_cached_baseline_rejects_source_mutation_during_search(tmp_path):
    from axiomforge.issue_engine.core import CandidateEvaluator
    from axiomforge.issue_engine.synthesis import to_patch
    repo,path=make_example(tmp_path)
    source=(repo/'service.py').read_text()
    patch=tmp_path/'candidate.patch'
    patch.write_text(to_patch('service.py',source,source.replace('return n - 1','return n + 1')))
    with CandidateEvaluator(load_contract(path)) as evaluator:
        first=evaluator.evaluate_patch(patch)
        assert first['passed']
        (repo/'service.py').write_text(source+'# modified after baseline\n')
        with pytest.raises(IssueError,match='source files changed'):
            evaluator.evaluate_patch(patch)
