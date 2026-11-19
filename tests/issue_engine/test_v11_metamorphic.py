"""Frozen-family, generated-variation checks; not a public-Issue blind benchmark.

The randomized vocabulary and combinations are never added to the synthesis
catalog. Reproducible fixed seed, no Hypothesis/LLM/network dependencies.
"""
from __future__ import annotations
import random
import types
import ast
from axiomforge.issue_engine.contract_repairs import contract_candidates
from axiomforge.issue_engine.ownership_repair import ownership_candidates

RNG=random.Random(20261013)


def test_140_optional_collection_contract_variations():
    for i in range(140):
        field=f'policy_{i}'
        operand=f'action_{i}'
        code=(f'def predicate(self, method):\n'
              f'    if self.{field} and method not in self.{field}:\n'
              f'        return False\n    return True\n')
        proposals=list(contract_candidates(code))
        ours=[v for v in proposals if v.kind=='optional-container-nonempty-distinction']
        assert ours
        ns={};exec(ours[0].source,ns)
        owner=types.SimpleNamespace(**{field:None})
        assert ns['predicate'](owner,operand)
        setattr(owner,field,set())
        assert not ns['predicate'](owner,operand)
        setattr(owner,field,{operand})
        assert ns['predicate'](owner,operand)


def test_140_positive_buffer_contract_variations():
    names=('blocksize','chunk_size','buffer_size','batch_size')
    for i in range(140):
        name=names[i%len(names)]
        default=RNG.randint(1,65536)
        if i%2:
            code=f'def read(*, {name}={default}):\n    return {name}\n'
        else:
            code=f'def read({name}={default}):\n    return {name}\n'
        proposals=list(contract_candidates(code))
        repairs=[x for x in proposals if x.kind=='positive-buffer-contract']
        assert repairs
        ns={};exec(repairs[0].source,ns)
        assert ns['read'](**{name:default})==default
        for invalid in (0,-1,-99):
            try:ns['read'](**{name:invalid})
            except ValueError:pass
            else:raise AssertionError('nonpositive buffer size silently accepted')


def test_40_peer_mapping_completion_variations():
    for i in range(40):
        imported=f'vendor_{i}'
        source=('class Adapter:\n'
            '    class DataError(Exception): pass\n'
            '    class IntegrityError(Exception): pass\n'
            '    class CustomError(Exception): pass\n'
            '    class OtherError(Exception): pass\n'
            '    def mapping(self):\n'
            f'        return {{\n            {imported}.exceptions.IntegrityError: self.IntegrityError,\n'
            f'            {imported}.exceptions.CustomError: self.CustomError,\n'
            f'            {imported}.exceptions.OtherError: self.OtherError,\n        }}\n')
        candidates=list(contract_candidates(source))
        chosen=next(c for c in candidates if c.kind=='peer-exception-map-completion' and '.DataError:' in c.source)
        errors={key:type(f'{key}{i}',(Exception,),{}) for key in ['DataError','IntegrityError','CustomError','OtherError']}
        ns={imported:types.SimpleNamespace(exceptions=types.SimpleNamespace(**errors))}
        exec(chosen.source,ns)
        obj=ns['Adapter']();mapped=obj.mapping()
        assert mapped[errors['DataError']] is obj.DataError
        assert mapped[errors['IntegrityError']] is obj.IntegrityError


def test_40_reserved_resource_pool_variations():
    for i in range(40):
        name=f'Pool{i}'
        code=(f'class {name}:\n'
              '    def _assign_requests_to_connections(self):\n'
              '        removed=[]\n'
              '        for connection in self._connections:\n'
              '            if connection.is_idle():\n'
              '                removed.append(connection)\n'
              '        connections=[connection for connection in self._connections if connection.is_idle()]\n'
              '        for pool_request in self._requests:\n'
              '            pool_request.assign_to_connection(connections[0] if connections else None)\n'
              '        return removed\n')
        proposals=list(ownership_candidates(code))
        assert proposals
        ast.parse(proposals[0].source)
        # Resource can be idle in transport but already reserved in queue.
        ns={};exec(proposals[0].source,ns)
        connection=types.SimpleNamespace(is_idle=lambda:True)
        class R:
            def __init__(self,c):self.connection=c
            def assign_to_connection(self,c):self.connection=c
        pool=ns[name]();pool._connections=[connection];pool._requests=[R(connection)]
        assert pool._assign_requests_to_connections()==[]


def test_generalization_scope_is_explicit():
    # This is 360 randomly varied programs within the structural operator
    # domains, not 360 independent production bugs or a blind Issue success rate.
    assert 140+140+40+40==360
