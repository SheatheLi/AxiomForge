"""Context-propagation operator: structural positives and adversarial negatives."""
import ast
import pytest
from axiomforge.issue_engine.context_repair import timeout_flow_candidates

SOURCE = '''def negotiate(stream, *, host, port, auth=None):
    stream.write(b"req")
    result = stream.read(max_bytes=100)
    return result

class Worker:
    def work(self, request):
        timeouts = request.extensions.get("timeout", {})
        timeout = timeouts.get("connect", None)
        kwargs = {"stream": self.stream, "host": b"a", "port": 100, "auth": None}
        return negotiate(**kwargs)
'''


def test_unseen_helper_interprocedural_repair_inserts_consistent_contract():
    items=list(timeout_flow_candidates(SOURCE))
    assert len(items)==1
    repaired=items[0].source
    assert items[0].kind=='interprocedural-timeout-propagation'
    assert 'timeout: float | None = None' in repaired
    assert 'stream.read(max_bytes=100, timeout=timeout)' in repaired
    assert 'stream.write(b"req", timeout=timeout)' in repaired
    assert 'negotiate(timeout=timeout, **kwargs)' in repaired
    ast.parse(repaired)
    scope={}
    exec(repaired,scope)
    class Stream:
        def write(self,data,timeout=None):assert timeout==2
        def read(self,max_bytes,timeout=None):assert timeout==2;return b'ok'
    worker=scope['Worker']()
    worker.stream=Stream()
    class Request:extensions={'timeout':{'connect':2}}
    assert worker.work(Request())==b'ok'


def test_no_candidate_when_timeout_not_computed():
    assert list(timeout_flow_candidates(SOURCE.replace('timeout = timeouts.get("connect", None)',
                                                     'not_a_timeout = timeouts.get("connect", None)')))==[]


def test_no_candidate_when_kwargs_already_contains_timeout():
    data=SOURCE.replace('"auth": None}', '"auth": None, "timeout": timeout}')
    assert list(timeout_flow_candidates(data))==[]


def test_no_candidate_when_helper_has_ambiguous_caller():
    data=SOURCE+'\ndef other(stream):\n    return negotiate(stream,host=b"x",port=1)\n'
    assert list(timeout_flow_candidates(data))==[]


def test_no_candidate_when_io_already_has_explicit_deadline():
    data=SOURCE.replace('stream.read(max_bytes=100)', 'stream.read(max_bytes=100, timeout=1)')
    assert list(timeout_flow_candidates(data))==[]


def test_no_candidate_for_unrelated_compute_helper():
    data=SOURCE.replace('stream.read(max_bytes=100)', 'stream.compute(max_bytes=100)')
    assert list(timeout_flow_candidates(data))==[]
