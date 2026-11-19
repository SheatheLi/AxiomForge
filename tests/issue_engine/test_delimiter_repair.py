"""Counterexample-driven boundary parsing without a repository-specific patch."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from axiomforge.issue_engine.core import IssueContract
from axiomforge.issue_engine.delimiter_repair import delimiter_candidates
from axiomforge.issue_engine.synthesis import synthesize


SOURCE = '''def endpoint(value):
    return value.split(":") if ":" in value else (value, None)
'''


def _contract(tmp_path: Path):
    p = tmp_path / 'fixture'
    p.mkdir()
    (p / 'endpoint.py').write_text(SOURCE)
    bad = '''from endpoint import endpoint
try:
    assert endpoint('[::1]:8080') == ('::1', '8080')
except (ValueError,AssertionError) as exc:
    raise AssertionError('BRACKETED-PORT-BOUNDARY') from exc
'''
    bad2 = '''from endpoint import endpoint
try:
    assert endpoint('[2001:db8::5]') == ('2001:db8::5', None)
except (ValueError,AssertionError) as exc:
    raise AssertionError('BRACKETED-PORT-BOUNDARY') from exc
'''
    good = '''from endpoint import endpoint
assert endpoint('example.org:4321') == ['example.org','4321']
assert endpoint('example.org') == ('example.org',None)
'''
    check = '''from endpoint import endpoint
assert endpoint('a:b:c') == ['a','b','c']
'''
    return IssueContract('https://github.com/example/project/issues/123', p,
        (sys.executable,'-c',bad), (sys.executable,'-c',good), frozenset(['endpoint.py']),
        'BRACKETED-PORT-BOUNDARY', repetitions=1, timeout_seconds=3,
        independent_reproductions=((sys.executable,'-c',bad2),),
        independent_checks=((sys.executable,'-c',check),), isolated_checks=True)


def test_bracket_aware_mutations_keep_plain_token_semantics():
    cands = list(delimiter_candidates(SOURCE))
    assert [x.kind for x in cands] == ['bounded-first-delimiter', 'rightmost-delimiter', 'bracket-delimiter-aware']
    namespace = {}
    exec(cands[2].source, namespace)
    assert namespace['endpoint']('[::1]:80') == ('::1','80')
    assert namespace['endpoint']('[2001:db8::1]') == ('2001:db8::1',None)
    assert namespace['endpoint']('foo:bar:baz') == ['foo','bar','baz']
    assert namespace['endpoint']('foo') == ('foo',None)


def test_new_family_solves_out_of_template_case_against_equal_search_budget(tmp_path):
    c=_contract(tmp_path)
    result=synthesize(c,'endpoint.py',max_candidates=7,max_seconds=15)
    assert result['status']=='VERIFIED_CANDIDATE',result
    assert result['operator']=='bracket-delimiter-aware'
    assert result['receipt']['passed']
    # The same bounded search with this family disabled must not claim credit.
    with patch('axiomforge.issue_engine.delimiter_repair.delimiter_candidates',return_value=iter(())):
        baseline=synthesize(c,'endpoint.py',max_candidates=7,max_seconds=15)
    assert baseline['status']=='NOT_SOLVED',baseline


def test_does_not_transform_arbitrary_methods_or_unbounded_separators():
    source='''def f(obj):
    return obj.split("::")
'''
    assert list(delimiter_candidates(source))==[]


def test_split_first_delimiter_does_not_destroy_colon_in_header_value():
    # This is a focused behavior surrogate for public decode-spam-headers
    # issue #31. It is not a reproduction of that entire upstream repository.
    original="def split_header(entry):\n    k, v = entry.split(':')\n    return k, v\n"
    mutations=list(delimiter_candidates(original))
    ns={}
    exec(mutations[0].source,ns)
    assert ns['split_header']('CIP:2001:db8::1') == ('CIP','2001:db8::1')
    assert ns['split_header']('CODE:42') == ('CODE','42')
    assert ns['split_header']('EMPTY:') == ('EMPTY','')
