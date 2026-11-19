"""Differential property tests: static may-definition overapproximates concrete paths.

The independent oracle executes CPython code over 211 distinct inputs and
checks each result has a statically recorded possible defining statement.
This does not prove general soundness, alias correctness or cross-procedure use.
"""
from __future__ import annotations
import random
import pytest
from axiomforge.issue_engine.dataflow import backward_slice


@pytest.mark.parametrize('seed', list(range(36)))
def test_reaching_defs_cover_all_concrete_branch_outcomes(seed):
    randomizer=random.Random(seed)
    lines=['def f(x):','    value=100']
    primes=[2,3,5,7]
    randomizer.shuffle(primes)
    for i,p in enumerate(primes):
        target=randomizer.randrange(p)
        lines.extend([f'    if x%{p} == {target}:',f'        value={101+i}'])
    lines.append('    return value')
    source='\n'.join(lines)+'\n'
    report=backward_slice(source,len(lines),max_sites=30)
    values={int(lines[ln-1].split('=',1)[1]) for ln in report['sliced_lines']
            if lines[ln-1].strip().startswith('value=')}
    namespace={}
    exec(source,namespace)
    actual={namespace['f'](x) for x in range(211)}
    assert actual <= values
    assert len(actual)>=2
    assert report['cfg_nodes']==11
    assert report['worklist_iterations']>=10


def test_many_if_joins_remain_within_bounded_fixed_point():
    code=['def f(n):','    v=0']
    for n in range(65):
        code.extend([f'    if n == {n}:',f'        v={n+1}'])
    code.append('    return v')
    result=backward_slice('\n'.join(code)+'\n',len(code),max_sites=170,max_depth=12)
    assert result['cfg_nodes']==133
    assert 0 < result['worklist_iterations'] < 200000
    assert result['slice_nodes']>=66
