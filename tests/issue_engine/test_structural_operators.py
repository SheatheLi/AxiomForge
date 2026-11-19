"""Repository-independent AST operator controls: no upstream code is bundled."""
from __future__ import annotations
import ast
import json
from pathlib import Path

from axiomforge.issue_engine.safety_repair import comment_preservation_candidates
from axiomforge.issue_engine.layout_repair import bounded_rejoin_candidates
from axiomforge.issue_engine.query_scope_repair import dml_scope_candidates
from axiomforge.issue_engine.async_cleanup import two_phase_close_candidates


def test_comment_guard_emitted_for_deleted_composite_syntax_node():
    src='''class Rule:
    def _eval(self, context):
        if else_clause and else_clause.children(check):
            before_else = children.reversed()
            return LintResult(anchor=context.segment, fixes=[LintFix.delete(else_clause[0])] + [LintFix.delete(s) for s in before_else])
'''
    items=list(comment_preservation_candidates(src))
    assert len(items)==1
    assert 'else_clause[0].recursive_crawl("comment")' in items[0].source
    assert ast.parse(items[0].source)


def test_comment_guard_not_emitted_for_non_deleting_lint_rule():
    src='''class Rule:
    def _eval(self, context):
        if flag:
            return LintResult(anchor=context.segment)
'''
    assert not list(comment_preservation_candidates(src))


def test_layout_oscillation_guard_not_linked_to_legacy_issue_number():
    src='''class Layout:
    def single(self, select_targets_info, context):
        select_clause = get_select_clause(context)
        target_seg = resolve_target(context)
        if select_targets_info.comment_after_select_idx != -1:
            return LintResult(anchor=select_clause.get())
        insert_buff = [WhitespaceSegment(), target_seg]
        return LintResult(fixes=insert_buff)
'''
    ops=list(bounded_rejoin_candidates(src))
    assert len(ops)==1
    assert 'proposed_line_length > max_line_length' in ops[0].source
    assert ast.parse(ops[0].source)


def test_dml_scope_operator_restores_ancestor_without_issue_specific_identifiers():
    src='''from sqlfluff.utils.analysis.query import Query
class Rule:
    def _eval(self, context):
        query: Query = Query.from_segment(context.segment, dialect=context.dialect)
        return list(self._visit_queries(query, set()))
    def _iter_available_targets(self, query, subquery=None):
        for selectable in query.selectables:
            for alias in selectable.select_info.table_aliases:
                if subquery and alias.from_expression_element.path_to(subquery.selectables[0].selectable):
                    continue
                yield alias
'''
    items=list(dml_scope_candidates(src))
    assert len(items)==1
    assert 'QueryType, Selectable' in items[0].source
    assert 'query.is_subquery = True' in items[0].source
    assert ast.parse(items[0].source)


def test_async_pool_close_two_phase_op_is_structural():
    src='''from .._synchronization import AsyncEvent, AsyncShieldCancellation, AsyncThreadLock
class PoolByteStream:
    def __init__(self):
        self._stream = None
        self._pool = None
        self._pool_request = None
        self._closed = False
    async def aclose(self):
        if not self._closed:
            self._closed = True
            with AsyncShieldCancellation():
                if hasattr(self._stream, "aclose"):
                    await self._stream.aclose()
            with self._pool._optional_thread_lock:
                self._pool._requests.remove(self._pool_request)
                closing = self._pool._assign_requests_to_connections()
            await self._pool._close_connections(closing)
'''
    results=list(two_phase_close_candidates(src))
    assert len(results)==1
    assert 'self._close_stage = 0' in results[0].source
    assert 'async with self._close_lock' in results[0].source
    assert ast.parse(results[0].source)


def test_external_examples_do_not_embed_canned_patches():
    src=Path(__file__).resolve().parents[2]/'examples'/'real_issues'
    contracts=sorted(src.glob('*.json'))
    assert len(contracts)==6
    for contract in contracts:
        data=json.loads(contract.read_text('utf8'))
        assert data['checkout']=='__PROVIDE_CHECKOUT__'
        assert 'patch' not in data
        assert data['reproduce'][0]=='${PYTHON}'


def test_repository_wide_search_finds_bad_module_without_filename_hint(tmp_path):
    import sys
    from axiomforge.issue_engine.core import load_contract
    from axiomforge.issue_engine.search import synthesize_repository
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'alpha.py').write_text('MESSAGE = "harmless"\n')
    (repo/'service.py').write_text('def score(x):\n    return x - 1\n')
    (repo/'reproduce.py').write_text('import service\nassert service.score(4)==5, "ISSUE-6207-WRONG-SCORE"\n')
    (repo/'control.py').write_text('import service\nassert service.score(6)>0\n')
    cfg={
        'schema':'axiomforge.issue-contract.v1',
        'issue_url':'https://github.com/example/service/issues/6207',
        'checkout':'repo','reproduce':['${PYTHON}','reproduce.py'],
        'control':['${PYTHON}','control.py'],
        'editable_files':['alpha.py','service.py'],
        'failure_fingerprint':'ISSUE-6207-WRONG-SCORE',
        'repetitions':1,'timeout_seconds':6,
    }
    p=tmp_path/'issue.json';p.write_text(json.dumps(cfg))
    result=synthesize_repository(load_contract(p),max_candidates_per_file=5,max_seconds=22)
    assert result['status']=='VERIFIED_CANDIDATE',result
    assert 'service.py' in result['patch']
    assert result['repository_search']['searched_without_preselecting_filename']
    assert (repo/'service.py').read_text().endswith('return x - 1\n')


def test_repository_search_respects_max_files_limit(tmp_path):
    import pytest
    from axiomforge.issue_engine.core import IssueError,load_contract
    from axiomforge.issue_engine.search import synthesize_repository
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'one.py').write_text('x=1\n')
    (repo/'two.py').write_text('y=2\n')
    p=tmp_path/'issue.json'
    p.write_text(json.dumps({'schema':'axiomforge.issue-contract.v1',
      'issue_url':'https://github.com/example/demo/issues/65', 'checkout':'repo',
      'reproduce':['${PYTHON}','-c','raise AssertionError("FAILURE")'],
      'control':['${PYTHON}','-c','pass'],
      'editable_files':['one.py','two.py'],
      'failure_fingerprint':'FAILURE'}))
    with pytest.raises(IssueError,match='too many editable files'):
        synthesize_repository(load_contract(p),max_files=1)
