"""Graph fixed-point, ambiguous branches, actual product execution and invalid-case guards."""
import json
from pathlib import Path
import pytest

from axiomforge.issue_engine.dataflow import backward_slice, slice_file
from axiomforge.issue_engine.core import IssueError, load_contract
from axiomforge.issue_engine.search import synthesize_repository


def test_branch_join_tracks_both_reaching_defs_and_control_dependencies():
    source='''def output(x):
    x = x + 1
    if x > 2:
        z = x * 2
    else:
        z = x - 1
    return z
'''
    receipt = backward_slice(source, 7)
    assert receipt['cfg_nodes'] == 6
    assert receipt['sliced_lines'] == [1, 2, 3, 4, 6, 7]
    assert {'source_line': 4, 'target_line': 7, 'type':'def-use:z'} in receipt['dependencies']
    assert {'source_line': 6, 'target_line': 7, 'type':'def-use:z'} in receipt['dependencies']
    assert receipt['worklist_iterations'] >= 6


def test_redefinition_kills_superseded_defs():
    source='''def f(x):
    z=x
    z=4
    return z
'''
    r=backward_slice(source,4)
    assert r['sliced_lines'] == [3,4]


def test_loop_reaching_definitions_fixed_point_with_zero_or_many_iterations():
    source='''def f(n):
    total=0
    while n>0:
        total+=n
        n-=1
    return total
'''
    r=backward_slice(source,6)
    assert {2,3,4,5,6} <= set(r['sliced_lines'])
    assert r['cfg_edges']>=6
    assert {'source_line':4,'target_line':4,'type':'def-use:total'} in r['dependencies']
    assert {'source_line':2,'target_line':4,'type':'def-use:total'} in r['dependencies']


def test_function_arguments_are_reachable_definitions():
    r=backward_slice('def f(a):\n    return a\n',2)
    assert r['sliced_lines']==[1,2]


@pytest.mark.parametrize('source',[
    'def f():\n    try:\n        x=1\n    except ValueError:\n        x=2\n    return x\n',
    'def f():\n    with open("a") as f:\n        x=f.read()\n    return x\n',
    'def f():\n    while True:\n        break\n    return 1\n',
    'def f():\n    x = (y := 2)\n    return x\n',
])
def test_unsupported_control_flow_fails_closed(source):
    with pytest.raises(IssueError, match='does not model|does not model walrus'):
        backward_slice(source, len(source.splitlines()))


def test_bounded_slice_rejects_truncation_not_claimed_complete():
    with pytest.raises(IssueError,match='slice budget'):
        backward_slice('def f(a):\n    b=a+1\n    return b\n',3,max_depth=1)


def test_repo_search_rejects_dataflow_without_real_trace(tmp_path):
    from axiomforge.issue_engine.core import IssueContract
    folder=tmp_path/'repo';folder.mkdir()
    (folder/'prod.py').write_text('def go():\n return True\n')
    (folder/'bad.py').write_text('assert False, "BUG-CONTRACT"\n')
    (folder/'good.py').write_text('assert True\n')
    spec={'schema':'axiomforge.issue-contract.v1', 'issue_url':'https://github.com/unit/demo/issues/3',
          'checkout':'repo','reproduce':['${PYTHON}','bad.py'],'control':['${PYTHON}','good.py'],
          'editable_files':['prod.py'],'failure_fingerprint':'BUG-CONTRACT',
          'repetitions':1,'timeout_seconds':5}
    p=tmp_path/'contract.json';p.write_text(json.dumps(spec))
    with pytest.raises(IssueError,match='requires dynamic spectrum'):
        synthesize_repository(load_contract(p),use_dataflow=True)


def test_dataflow_file_rejects_large_unknown_files(tmp_path):
    p=tmp_path/'bad.txt';p.write_text('def f():\n return 4\n')
    with pytest.raises(IssueError,match='bounded existing Python'):
        slice_file(p,2)


def test_spectrum_plus_dataflow_drives_actual_repository_search(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    (repo/'service.py').write_text('''def transform(n):
    c=n+1
    if c > 0:
        return c-2
    return c
''')
    (repo/'failure.py').write_text('''from service import transform
assert transform(5)==6, "BROKEN-FLAG-5339"
''')
    (repo/'normal.py').write_text('''from service import transform
assert transform(-5)==-4
''')
    obj={'schema':'axiomforge.issue-contract.v1', 'issue_url':'https://github.com/example/project/issues/5339',
        'checkout':'repo','reproduce':['${PYTHON}','failure.py'],
        'control':['${PYTHON}','normal.py'],'editable_files':['service.py'],
        'failure_fingerprint':'BROKEN-FLAG-5339','repetitions':1,'timeout_seconds':5}
    p=tmp_path/'contract.json';p.write_text(json.dumps(obj))
    result=synthesize_repository(load_contract(p),max_candidates_per_file=10,max_seconds=32,
                                 use_spectrum=True,use_dataflow=True)
    assert result['repository_search']['dataflow_guided'] is True
    assert result['repository_search']['dataflow']['service.py']['status']=='ANALYZED'
    assert result['repository_search']['dataflow']['service.py']['worklist_iterations']>=1
    assert result['status'] in ('VERIFIED_CANDIDATE','NOT_SOLVED')


def test_heap_in_place_mutation_is_a_weak_reaching_definition():
    source='''def collect(n):
    seen=[]
    for i in range(n):
        if i>0:
            seen.append(i)
    return seen
'''
    r=backward_slice(source,6)
    assert 5 in r['sliced_lines']
    assert 3 in r['sliced_lines']
    assert {'source_line':5,'target_line':6,'type':'def-use:seen'} in r['dependencies']
    assert next(s for s in r['sites'] if s['line']==5)['weak_heap_writes']==['seen']


def test_comprehension_binder_does_not_pollute_function_scope():
    source='''def f(items):
    others=[x for x in items if x>0]
    return others
'''
    r=backward_slice(source,3)
    assert r['sliced_lines']==[1,2,3]
    assert not any(x['type']=='def-use:x' for x in r['dependencies'])
