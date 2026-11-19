"""Conservative resource ownership trace evaluator for asynchronous APIs.

Consumes externally recorded resource events; makes no inference that a logged
CLOSE_BEGIN caused actual release. 'OPEN_AT_END' is an unclosed observation,
not a certified leak unless the observing trace includes a terminal lifecycle
boundary. The tool detects conflicting release/borrow state transitions and
returns minimal evidence references for investigation.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass,asdict
import argparse,json,math,pathlib

class ResourceTraceError(ValueError):pass

ACTIONS=frozenset(('BORROW','CLOSE_BEGIN','CLOSE_CANCEL','RELEASE','CLOSE_END','POOL_TERMINATED'))

@dataclass(frozen=True)
class ResourceFinding:
    resource_id:str
    status:str
    first_event:int
    last_event:int
    reasons:tuple[str,...]

@dataclass(frozen=True)
class TraceReport:
    status:str
    events:int
    resources:int
    terminal_observed:bool
    findings:tuple[ResourceFinding,...]
    interpretation:str
    def serial(self):return asdict(self)

def analyze(payload:dict)->TraceReport:
    if not isinstance(payload,dict) or not isinstance(payload.get('events'),list):
        raise ResourceTraceError('events array required')
    events=payload['events']
    if not events:raise ResourceTraceError('empty trace')
    clock=payload.get('clock_id')
    if not isinstance(clock,str) or not clock.strip():raise ResourceTraceError('clock identifier required')
    active={};tracked={};findings=defaultdict(list);first={};last={}
    previous=-math.inf;terminal=False
    for pos,row in enumerate(events):
        if not isinstance(row,dict) or row.get('action') not in ACTIONS:
            raise ResourceTraceError(f'unknown event at index {pos}')
        timestamp=row.get('timestamp')
        if type(timestamp) not in (float,int) or not math.isfinite(timestamp) or timestamp<previous:
            raise ResourceTraceError('timestamps must be finite and nondecreasing')
        previous=timestamp
        rid=row.get('resource_id')
        action=row['action']
        if action=='POOL_TERMINATED':
            if pos!=len(events)-1 or terminal:raise ResourceTraceError('terminal event must be last and unique')
            if rid not in (None,''):raise ResourceTraceError('pool terminal cannot target one resource')
            terminal=True;continue
        if terminal:raise ResourceTraceError('events after terminal state')
        if not isinstance(rid,str) or not rid or len(rid)>200:
            raise ResourceTraceError('resource_id required')
        first.setdefault(rid,pos);last[rid]=pos
        state=active.get(rid,'UNSEEN');tracked.setdefault(rid,[]).append(action)
        if action=='BORROW':
            if state=='OPEN':findings[rid].append('double borrow before release')
            elif state=='CLOSING':findings[rid].append('reborrow during close')
            elif state=='RELEASED':findings[rid].append('resource ID reused after release')
            else:active[rid]='OPEN'
        elif action=='CLOSE_BEGIN':
            if state!='OPEN':findings[rid].append(f'close begins in state {state}')
            else:active[rid]='CLOSING'
        elif action=='CLOSE_CANCEL':
            if state!='CLOSING':findings[rid].append(f'cancel outside close in {state}')
            # Cancellation of close does not restore ownership or imply release.
        elif action=='RELEASE':
            if state=='UNSEEN':findings[rid].append('release without borrow')
            elif state=='RELEASED':findings[rid].append('double release')
            else:active[rid]='RELEASED'
        elif action=='CLOSE_END':
            if state!='RELEASED':findings[rid].append('close completion without verified release')
    results=[]
    for rid in sorted(tracked):
        state=active.get(rid,'UNSEEN');errors=findings[rid]
        if errors: status='PROTOCOL_VIOLATION'
        elif state=='RELEASED':status='RELEASED'
        elif terminal:status='OPEN_AT_TERMINAL'
        else:status='INDETERMINATE'
        results.append(ResourceFinding(rid,status,first[rid],last[rid],tuple(errors)))
    total='PROTOCOL_VIOLATION' if any(x.status=='PROTOCOL_VIOLATION' for x in results) else (
        'OPEN_AT_TERMINAL' if any(x.status=='OPEN_AT_TERMINAL' for x in results) else (
        'INDETERMINATE' if any(x.status=='INDETERMINATE' for x in results) else 'RELEASED'))
    return TraceReport(total,len(events),len(results),terminal,tuple(results),
        'Event consistency only; recorded release is not independent proof of physical cleanup')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=pathlib.Path)
    args=parser.parse_args()
    result=analyze(json.loads(args.trace.read_text(encoding='utf8')))
    print(json.dumps(result.serial(),ensure_ascii=False,indent=2,allow_nan=False))
if __name__=='__main__':main()
