"""Causality-preserving ddmin of resource event histories.

Only event-type dependencies in the provided graph are honored. This is not
stateless DPOR, symbolic execution or a proof of entire runtime correctness.
"""
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Event:
    key:str
    resource:str
    kind:str
    parents:tuple[str,...]=()

def closed(events):
    present={e.key for e in events}
    return len(present)==len(events) and all(set(e.parents)<=present for e in events)

def minimize(events:list[Event],fails:Callable[[list[Event]],bool]):
    """Delta debug with dependency closure, preserving original happens-before order."""
    if not closed(events):raise ValueError('initial event graph missing prerequisites')
    if not fails(events):raise ValueError('full trace does not fail')
    current=list(events);chunk_size=max(1,len(current)//2);queries=0
    while chunk_size>=1:
        changed=False
        for start in range(0,len(current),chunk_size):
            drop={e.key for e in current[start:start+chunk_size]}
            candidate=[e for e in current if e.key not in drop]
            if candidate and closed(candidate):
                queries+=1
                if fails(candidate):
                    current=candidate;changed=True;break
        if changed:continue
        chunk_size//=2
    return current,queries

def leak(events:list[Event]):
    owned={};terminal=set()
    for e in events:
        if e.kind=='ALLOC':owned[e.resource]=True
        if e.kind=='RELEASE':owned[e.resource]=False
        if e.kind=='SCOPE_EXIT':terminal.add(e.resource)
    return any(owned.get(x,False) for x in terminal)
