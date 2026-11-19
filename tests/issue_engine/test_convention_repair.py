"""Cross-codebase idiom transfers must be inspectable, conservative hypotheses."""
import ast
from axiomforge.issue_engine.convention_repair import convention_candidates

SOURCE='''class Segment:
    def raw_normalized(self, casefold=False):
        return 'ORDER'
class Alias:
    def __init__(self):
        self.ref_str = 'order'
        self.segment = Segment()
def known(segment):
    return segment.raw_normalized(False)
def lookup(a):
    result = []
    result.append((a.ref_str,))
    return result
'''


def test_transfers_existing_normalized_segment_semantics():
    items=list(convention_candidates(SOURCE))
    targets=[x for x in items if x.kind == 'peer-segment-normalization']
    assert len(targets) == 1
    assert 'a.segment.raw_normalized(False)' in targets[0].source
    ast.parse(targets[0].source)
    original={}; exec(SOURCE,original)
    revised={};exec(targets[0].source,revised)
    assert original['lookup'](original['Alias']()) == [('order',)]
    assert revised['lookup'](revised['Alias']()) == [('ORDER',)]


def test_no_guessing_unsupported_normalizer_api():
    src='def lookup(a):\n    return (a.ref_str,)\n'
    assert list(convention_candidates(src)) == []


def test_honors_line_scope_and_no_candidates_for_non_primitive_code():
    assert list(convention_candidates(SOURCE,line_start=2,line_end=4)) == []
    assert all(x.source != SOURCE for x in convention_candidates(SOURCE))


def test_ast_rewrite_maintains_string_comments_and_imports():
    src='''# foo.ref_str in a comment must remain intact\nclass C:\n    def raw_normalized(self, casefold=False):return "A"\ndef known(v):return v.raw_normalized(False)\ndef f(foo):\n    return foo.ref_str\n'''
    items=list(convention_candidates(src))
    assert items
    assert all('# foo.ref_str in a comment' in m.source for m in items)


def test_non_destructive_canonical_alias_alternative():
    source=SOURCE.replace("return 'ORDER'", "return 'ORDER'")
    source=source.replace('def known(segment):', 'def extra(segment):return segment.normalize("order")\ndef known(segment):')
    source=source.replace('class Segment:', 'class Segment:\n    def is_type(self, kind):return kind == "naked_identifier"\n    def normalize(self, value):return value.upper()')
    items=list(convention_candidates(source))
    alternatives=[m for m in items if m.kind=='peer-canonical-alias-alternative']
    assert len(alternatives)==1
    state={};exec(alternatives[0].source,state)
    assert state['lookup'](state['Alias']())==[('order',),('ORDER',)]
    assert 'result.append((a.ref_str,))' in alternatives[0].source
