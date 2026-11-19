"""Only lexical same-class self calls are expanded; explicit dispatch caveats."""
from axiomforge.issue_engine.dataflow import interprocedural_slice, backward_slice


def test_same_class_method_call_expands_into_original_definition_and_condition():
    source='''class Adapter:
    def validate(self, item):
        if not self.allowed(item):
            return False
        return True
    def allowed(self, item):
        if item == "bad":
            return False
        return True
'''
    intra=backward_slice(source,3)
    assert 7 not in intra['sliced_lines']
    inter=interprocedural_slice(source,3)
    assert inter['method_count']==2
    assert inter['call_graph_edges'][0]['caller']=='validate'
    assert inter['call_graph_edges'][0]['callee']=='allowed'
    assert {6,7,8,9} <= set(inter['sliced_lines'])
    assert inter['call_graph_status']=='COMPLETE_WITHIN_LEXICAL_SCOPE'


def test_two_hops_are_bounded_by_requested_maximum():
    source='''class S:
    def a(self,x):
        return self.b(x)
    def b(self,x):
        return self.c(x)
    def c(self,x):
        if x>0:
            return 1
        return 2
'''
    one=interprocedural_slice(source,3,max_call_depth=1)
    assert one['method_count']==2
    assert 7 not in one['sliced_lines']
    two=interprocedural_slice(source,3,max_call_depth=2)
    assert two['method_count']==3
    assert 7 in two['sliced_lines']


def test_nonself_call_is_not_claimed_as_resolved():
    source='''class S:
    def a(self,x):
        return x.b(x)
    def b(self,x):
        return x+1
'''
    output=interprocedural_slice(source,3)
    assert output['method_count']==1
    assert not output['call_graph_edges']


def test_recursive_self_call_is_not_infinitely_expanded():
    source='''class S:
    def a(self,n):
        if n>0:
            return self.a(n-1)
        return 0
'''
    r=interprocedural_slice(source,4)
    assert not r['call_graph_edges']
    assert r['method_count']==1
    assert r['status']=='PARTIAL'


def test_no_return_callee_reports_partial_not_validated():
    source='''class S:
    def a(self):
        return self.b()
    def b(self):
        print("side effect")
'''
    r=interprocedural_slice(source,3)
    assert r['status']=='PARTIAL'
    assert len(r['unsupported_callees'])==1


def test_same_helper_called_twice_is_not_mistaken_for_recursion():
    source='''class S:
    def apply(self,n):
        if n>0:
            a=self.helper(n)
        else:
            a=self.helper(-n)
        return a
    def helper(self,n):
        return n+1
'''
    r=interprocedural_slice(source,7)
    assert r['status']=='ANALYZED'
    assert len([x for x in r['call_graph_edges'] if x['callee']=='helper'])==2
    assert r['method_count']==2


def test_syntax_error_fails_closed_with_issue_error():
    from axiomforge.issue_engine.core import IssueError
    import pytest
    with pytest.raises(IssueError,match='cannot parse source'):
        backward_slice('def bad(:\n  pass\n',1)
