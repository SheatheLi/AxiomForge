"""Executable, repository-agnostic proof that patches fix the specified issue.

Includes adversarial controls: different failure, timeout, unsafe patch,
control regression, unstable output and explicitly budgeted minimization.
"""
from pathlib import Path
import json
import sys
import textwrap
import pytest

from axiomforge.issue_engine.core import IssueError, IssueContract, load_contract, evaluate, minimize


def make_project(tmp_path: Path, *, fixed: bool = True, control_pass: bool = True,
                 replicate: bool = False):
    repo=tmp_path/'repo'; repo.mkdir()
    (repo/'module.py').write_text('def compute(x):\n    return x - 1\n',encoding='utf8')
    (repo/'input.txt').write_text('noise one\nTARGET 4\nnoise two\nnoise three\n',encoding='utf8')
    (repo/'repro.py').write_text(textwrap.dedent('''\
        import module
        from pathlib import Path
        text=Path('input.txt').read_text()
        if 'TARGET 4' not in text:
            raise ValueError('invalid input, not the claimed defect')
        actual=module.compute(4)
        if actual != 5:
            raise AssertionError(f'ISSUE-1423: incorrect computation {actual}')
    '''),encoding='utf8')
    (repo/'control.py').write_text('import module\nassert module.compute(10) % 2 == 1\n' if control_pass else 'raise RuntimeError("regression")\n',encoding='utf8')
    patch=tmp_path/'fix.patch'
    if fixed:
        patch.write_text(textwrap.dedent('''\
            diff --git a/module.py b/module.py
            --- a/module.py
            +++ b/module.py
            @@ -1,2 +1,2 @@
             def compute(x):
            -    return x - 1
            +    return x + 1
        '''),encoding='utf8')
    else:
        patch.write_text(textwrap.dedent('''\
            diff --git a/module.py b/module.py
            --- a/module.py
            +++ b/module.py
            @@ -1,2 +1,2 @@
             def compute(x):
            -    return x - 1
            +    return x - 2
        '''),encoding='utf8')
    contract=tmp_path/'contract.json'
    contract.write_text(json.dumps({
        'schema':'axiomforge.issue-contract.v1',
        'issue_url':'https://github.com/demo/pkg/issues/1423',
        'checkout':'repo',
        'reproduce':[sys.executable,'repro.py'],
        'control':[sys.executable,'control.py'],
        'editable_files':['module.py'],
        'failure_fingerprint':'ISSUE-1423: incorrect computation',
        'repetitions':2,'timeout_seconds':10
    }),encoding='utf8')
    return repo,patch,contract


def test_full_patch_receipt_and_original_immutability(tmp_path):
    repo,patch,json_spec=make_project(tmp_path)
    old=(repo/'module.py').read_bytes()
    result=evaluate(load_contract(json_spec),patch)
    assert result['passed']
    assert result['baseline']['consistent_fail']
    assert result['baseline']['controls_pass']
    assert result['patched']['repaired']
    assert result['patched']['controls_pass']
    assert len(result['patched']['reproducer'])==2
    assert (repo/'module.py').read_bytes()==old
    assert result['patch_sha256']


def test_wrong_patch_must_not_be_counted(tmp_path):
    _,patch,c=make_project(tmp_path,fixed=False)
    receipt=evaluate(load_contract(c),patch)
    assert not receipt['passed']
    assert not receipt['patched']['repaired']


def test_baseline_wrong_fingerprint_rejected(tmp_path):
    _,p,c=make_project(tmp_path)
    obj=json.loads(c.read_text());obj['failure_fingerprint']='This-will-never-match'
    c.write_text(json.dumps(obj))
    with pytest.raises(IssueError,match='SPECIFIC issue'):
        evaluate(load_contract(c),p)


def test_baseline_control_failure_rejected(tmp_path):
    _,p,c=make_project(tmp_path,control_pass=False)
    with pytest.raises(IssueError,match='baseline control'):
        evaluate(load_contract(c),p)


def test_patch_control_regression_rejected(tmp_path):
    _,p,c=make_project(tmp_path)
    src=p.read_text()
    p.write_text(src+'diff --git a/control.py b/control.py\n--- a/control.py\n+++ b/control.py\n@@ -1,2 +1,2 @@\n import module\n-assert module.compute(10) % 2 == 1\n+assert module.compute(10) % 2 == 0\n')
    with pytest.raises(IssueError,match='unapproved file'):
        evaluate(load_contract(c),p)


def test_candidate_patch_must_apply_cleanly(tmp_path):
    _,p,c=make_project(tmp_path)
    p.write_text(p.read_text().replace('return x - 1','return y - 1'))
    with pytest.raises(IssueError,match='patch rejected'):
        evaluate(load_contract(c),p)


def test_contract_validation_rejects_unsafe_paths(tmp_path):
    r,p,c=make_project(tmp_path)
    obj=json.loads(c.read_text());obj['checkout']='missing'
    c.write_text(json.dumps(obj))
    with pytest.raises(IssueError,match='does not exist'):
        load_contract(c)
    # Also reject a plausible-looking but malicious patch path.
    c.write_text(json.dumps({**obj,'checkout':'repo'}))
    p.write_text('diff --git a/../../x b/../../x\n--- a/../../x\n+++ b/../../x\n@@ -0,0 +1 @@\n+BAD\n')
    with pytest.raises(IssueError,match='unsafe relative path'):
        evaluate(load_contract(c),p)


def test_hierarchical_minimizer_preserves_identity_and_patch(tmp_path):
    repo,p,c=make_project(tmp_path)
    result=minimize(load_contract(c),p,'input.txt',max_evaluations=35)
    assert result['remaining_lines']==1
    assert result['original_lines']==4
    assert result['content']=='TARGET 4\n'
    assert result['one_minimal']
    assert (repo/'input.txt').read_text().count('\n')==4


def test_budget_limits_minimality_claim(tmp_path):
    _,p,c=make_project(tmp_path)
    out=minimize(load_contract(c),p,'input.txt',max_evaluations=2)
    assert not out['one_minimal']
    assert out['budget_exhausted']


def test_false_positive_when_new_error_same_exit_code(tmp_path):
    repo,p,c=make_project(tmp_path)
    (repo/'repro.py').write_text('raise RuntimeError("unrelated failure")\n')
    with pytest.raises(IssueError,match='SPECIFIC issue'):
        evaluate(load_contract(c),p)


def test_command_array_instead_of_shell(tmp_path):
    _,p,c=make_project(tmp_path)
    obj=json.loads(c.read_text());obj['reproduce']='python repro.py'
    c.write_text(json.dumps(obj))
    with pytest.raises(IssueError,match='argv arrays'):
        load_contract(c)


def test_timeout_not_confused_with_issue(tmp_path):
    repo,p,c=make_project(tmp_path)
    (repo/'repro.py').write_text('import time\ntime.sleep(0.5)\n')
    obj=json.loads(c.read_text());obj['timeout_seconds']=0.2
    c.write_text(json.dumps(obj))
    with pytest.raises(IssueError,match='SPECIFIC issue'):
        evaluate(load_contract(c),p)


def test_synthesis_generates_real_repair_without_hardcoded_solution(tmp_path):
    from axiomforge.issue_engine.synthesis import synthesize
    repo,patch,contract=make_project(tmp_path)
    out=synthesize(load_contract(contract),'module.py',max_candidates=6,max_seconds=25,
                   line_start=2,line_end=2,knobs=())
    assert out['status']=='VERIFIED_CANDIDATE',out
    assert out['operator']=='arithmetic'
    assert 'return x + 1' in out['patch']
    assert out['receipt']['passed']
    assert (repo/'module.py').read_text().endswith('return x - 1\n')


def test_synthesis_rejects_unsolvable_contract_without_claim(tmp_path):
    from axiomforge.issue_engine.synthesis import synthesize
    repo,patch,contract=make_project(tmp_path)
    out=synthesize(load_contract(contract),'module.py',max_candidates=1,max_seconds=25,
                   line_start=1,line_end=1,knobs=())
    assert out['status']=='NOT_SOLVED'
    assert out['attempts']==0


def test_portfolio_has_distinct_repository_gate(tmp_path):
    from axiomforge.issue_engine.portfolio import review_portfolio
    _,patch,c=make_project(tmp_path)
    manifest=tmp_path/'portfolio.json'
    manifest.write_text(json.dumps({'schema':'axiomforge.portfolio.v1','cases':[
        {'contract':'contract.json','patch':'fix.patch','patch_authority':'assistant_authored'}]}))
    result=review_portfolio(manifest)
    assert result['locally_verified_candidate_patches']==1
    assert result['distinct_external_repositories']==1
    assert result['upstream_prs_submitted']==0
    assert result['axiomforge_synthesized_candidates']==0
    payload=json.loads(manifest.read_text())
    payload['cases'].append(payload['cases'][0])
    manifest.write_text(json.dumps(payload))
    with pytest.raises(IssueError,match='duplicate public issue'):
        review_portfolio(manifest)


def test_patch_must_not_modify_reproduction_script(tmp_path):
    _,patch,c=make_project(tmp_path)
    p=patch.read_text()
    p+='diff --git a/repro.py b/repro.py\n--- a/repro.py\n+++ b/repro.py\n@@ -1,1 +1,1 @@\n-import module\n+pass  # cheat\n'
    patch.write_text(p)
    with pytest.raises(IssueError,match='unapproved file'):
        evaluate(load_contract(c),patch)


def test_acquire_then_initialize_operator_is_not_project_specific():
    from axiomforge.issue_engine.lifecycle_repair import initialization_cleanup_candidates
    source = '''class ResourceRecord:
    def __init__(self, registry):
        self.registry = registry
        self.socket = None

    def __close(self):
        self.registry.release_socket(self.socket)
        self.socket = None

    def initialize(self):
        self.socket = None
        try:
            self.socket = self.registry.acquire_socket()
        except BaseException as exc:
            with util.safe_reraise():
                log.error("could not initialize", exc)
        else:
            self.registry.configure_socket(self.socket)
'''
    result=list(initialization_cleanup_candidates(source))
    assert len(result)==1
    assert 'self.registry.release_socket(self.socket)' in result[0].source
    assert result[0].source.index('self.registry.configure_socket') < result[0].source.index('except BaseException')


def test_resource_operator_rejects_unknown_cleanup_method():
    from axiomforge.issue_engine.lifecycle_repair import initialization_cleanup_candidates
    source = '''class Record:
    def initialize(self):
        try:
            self.resource = open("x")
        except BaseException as e:
            with util.safe_reraise():
                print(e)
        else:
            self.resource.read()
'''
    assert not list(initialization_cleanup_candidates(source))


def test_async_cleanup_operator_requires_specific_ownership_phases():
    from axiomforge.issue_engine.async_cleanup import two_phase_close_candidates
    assert not list(two_phase_close_candidates('''class Close:
    async def aclose(self):
        if not self._closed:
            self._closed = True
            await self._stream.aclose()
'''))


def test_three_search_operator_families_declared():
    from axiomforge.issue_engine.synthesis import synthesize
    assert callable(synthesize)
    from axiomforge.issue_engine.state_repair import borrowed_lookup_candidates
    from axiomforge.issue_engine.lifecycle_repair import initialization_cleanup_candidates
    from axiomforge.issue_engine.async_cleanup import two_phase_close_candidates
    for func in (borrowed_lookup_candidates,initialization_cleanup_candidates,two_phase_close_candidates):
        assert callable(func)


def test_isolated_executions_prevent_cross_command_state_contamination(tmp_path):
    repo, patch, contract_file = make_project(tmp_path)
    # Reproduction has an unrelated, legitimate side effect. A correct normal
    # control must not inherit it, otherwise the before/after experiment
    # rejects a valid repair for the wrong reason.
    (repo/'repro.py').write_text(
        'from pathlib import Path\nPath("side_effect.txt").write_text("modified")\n'
        + (repo/'repro.py').read_text())
    (repo/'control.py').write_text((repo/'control.py').read_text() +
        '\nfrom pathlib import Path\nassert not Path("side_effect.txt").exists()\n')
    cfg=json.loads(contract_file.read_text())
    cfg['repetitions']=1
    contract_file.write_text(json.dumps(cfg))
    with pytest.raises(IssueError, match='baseline control'):
        evaluate(load_contract(contract_file),patch)
    cfg['isolated_checks']=True
    contract_file.write_text(json.dumps(cfg))
    receipt=evaluate(load_contract(contract_file),patch)
    assert receipt['passed']
    assert receipt['isolated_checks']
    assert not (repo/'side_effect.txt').exists()


def test_isolated_check_requires_boolean(tmp_path):
    _,_,path=make_project(tmp_path)
    obj=json.loads(path.read_text())
    obj['isolated_checks']='yes'
    path.write_text(json.dumps(obj))
    with pytest.raises(IssueError,match='isolated_checks'):
        load_contract(path)


def test_portfolio_refuses_unreplayed_generator_authorship_claim(tmp_path):
    from axiomforge.issue_engine.portfolio import review_portfolio
    repo,patch,c=make_project(tmp_path)
    # The candidate is valid, but this variant (extra whitespace) is deliberately
    # not the exact bytes produced by the generic automatic generator.
    patch.write_text(patch.read_text().replace('+    return x + 1', '+    return (x + 1)'))
    manifest=tmp_path/'claims.json'
    manifest.write_text(json.dumps({'schema':'axiomforge.portfolio.v1','cases':[
        {'contract':'contract.json','patch':'fix.patch',
         'patch_authority':'axiomforge_synthesized',
         'generated_source_file':'module.py','max_candidates':10,'max_seconds':25}]}))
    result=review_portfolio(manifest)
    assert result['locally_verified_candidate_patches']==1
    assert result['axiomforge_synthesized_candidates']==0
    assert not result['all_authorship_claims_replayed']
    assert not result['evidence'][0]['generator_replayed']


def test_portfolio_replays_true_generator_patch(tmp_path):
    from axiomforge.issue_engine.portfolio import review_portfolio
    from axiomforge.issue_engine.synthesis import synthesize
    repo, patch, c=make_project(tmp_path)
    candidate=synthesize(load_contract(c),'module.py',max_candidates=10,max_seconds=25)
    assert candidate['status']=='VERIFIED_CANDIDATE'
    patch.write_text(candidate['patch'])
    manifest=tmp_path/'claims.json'
    manifest.write_text(json.dumps({'schema':'axiomforge.portfolio.v1','cases':[
        {'contract':'contract.json','patch':'fix.patch',
         'patch_authority':'axiomforge_synthesized',
         'generated_source_file':'module.py','max_candidates':10,'max_seconds':25}]}))
    result=review_portfolio(manifest)
    assert result['axiomforge_synthesized_candidates']==1
    assert result['all_authorship_claims_replayed']

@pytest.mark.parametrize('metadata', [
    'old mode 100644\nnew mode 100755\n',
    'rename from module.py\nrename to control.py\n',
    'new file mode 100644\n',
    'GIT binary patch\n',
    'copy from module.py\ncopy to control.py\n',
])
def test_patch_rejects_hidden_metadata_changes(tmp_path, metadata):
    from axiomforge.issue_engine.core import _inspect_patch
    _, patch, _ = make_project(tmp_path)
    original = patch.read_text()
    candidate = original.replace('--- a/module.py', metadata + '--- a/module.py')
    with pytest.raises(IssueError, match='cannot change'):
        _inspect_patch(candidate, frozenset({'module.py'}))


def test_patch_rejects_mismatched_git_header_before_git_apply(tmp_path):
    from axiomforge.issue_engine.core import _inspect_patch
    _, patch, _ = make_project(tmp_path)
    candidate = patch.read_text().replace('diff --git a/module.py b/module.py',
                                          'diff --git a/module.py b/control.py')
    with pytest.raises(IssueError, match='identical safe paths'):
        _inspect_patch(candidate, frozenset({'module.py'}))


def test_patch_rejects_extra_unapproved_diff_block(tmp_path):
    from axiomforge.issue_engine.core import _inspect_patch
    _, patch, _ = make_project(tmp_path)
    candidate = patch.read_text() + '\ndiff --git a/control.py b/control.py\n--- a/control.py\n+++ b/control.py\n@@ -1 +1 @@\n-foo\n+bar\n'
    with pytest.raises(IssueError, match='unapproved'):
        _inspect_patch(candidate, frozenset({'module.py'}))


def test_contract_refuses_authorized_edit_of_failure_oracle(tmp_path):
    repo,patch,c=make_project(tmp_path)
    obj=json.loads(c.read_text())
    obj['editable_files']=['module.py','repro.py']
    c.write_text(json.dumps(obj))
    with pytest.raises(IssueError, match='test/oracle script'):
        load_contract(c)
