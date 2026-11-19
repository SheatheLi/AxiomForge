import ast
from axiomforge.issue_engine.ownership_repair import ownership_candidates


def test_rewrites_all_idle_checks_in_pool_assignment_not_one():
    source=('class Pool:\n    def assign(self):\n        for connection in self._connections:\n'
            '            if connection.is_idle() and len(self._requests):\n                self.cleanup(connection)\n'
            '        available=[connection for connection in self._connections if connection.is_idle()]\n'
            '        request=self._requests[0]\n        request.assign_to_connection(available[0])\n')
    items=list(ownership_candidates(source));assert len(items)==1
    candidate=items[0];ast.parse(candidate.source)
    assert candidate.source.count('self._unreserved_idle(connection)')==2
    assert 'def _is_reserved' in candidate.source


def test_no_false_trigger_outside_reservation_pool():
    source='def f(connection):\n    return connection.is_idle()\n'
    assert not list(ownership_candidates(source))
