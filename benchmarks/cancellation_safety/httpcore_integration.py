"""Real httpcore implementation differential using targeted cancellation mutation.

Executes installed httpcore's actual AsyncConnectionPool request path, once as
shipped and once with ONLY its cancellation handler replaced in memory by the
historical failure pattern `except Exception`. All other program code is real
third-party code. This is NOT a claim to execute HTTPcore version 0.12.3.
"""
from __future__ import annotations
import ast
import asyncio
import importlib
import inspect
import json
from pathlib import Path
import sys
import textwrap

try:
    import httpcore
except ImportError:
    raise SystemExit('httpcore>=1.0 required for optional real-library integration')


class BlockedConnection:
    def __init__(self, origin, signal, stop):
        self.origin=origin; self.signal=signal; self.stop=stop; self.status='NEW'
    def can_handle_request(self, origin):return origin==self.origin
    def is_available(self): return True
    def is_idle(self):return False
    def is_closed(self):return self.status=='CLOSED'
    def has_expired(self):return False
    async def handle_async_request(self, request):
        self.status='ACTIVE'
        self.signal.set()
        await self.stop.wait()
        raise RuntimeError('Unexpected fake transport continuation')
    async def aclose(self): self.status='CLOSED'
    def info(self):return self.status


def mutated_handle_method():
    poolmod=importlib.import_module('httpcore._async.connection_pool')
    src=textwrap.dedent(inspect.getsource(httpcore.AsyncConnectionPool.handle_async_request))
    tree=ast.parse(src)
    handlers=[node for node in ast.walk(tree) if isinstance(node,ast.ExceptHandler)
              and isinstance(node.type,ast.Name) and node.type.id=='BaseException']
    if len(handlers)!=1:
        raise RuntimeError('Upstream version changed: expected exactly one cancellation catch')
    handlers[0].type.id='Exception'
    env=dict(vars(poolmod))
    exec(compile(ast.fix_missing_locations(tree),'<httpcore_cancel_mutation>','exec'),env)
    return env['handle_async_request']


async def inspect_pool(*, mutate:bool):
    signal, stop=asyncio.Event(), asyncio.Event()
    override=mutated_handle_method() if mutate else httpcore.AsyncConnectionPool.handle_async_request
    class LocalPool(httpcore.AsyncConnectionPool):
        handle_async_request=override
        def __init__(self):
            super().__init__(max_connections=1)
        def create_connection(self,origin):return BlockedConnection(origin,signal,stop)
    pool=LocalPool()
    req=httpcore.Request('GET','http://example.invalid/')
    task=asyncio.create_task(pool.handle_async_request(req))
    await asyncio.wait_for(signal.wait(),timeout=1)
    before={'pending_requests':len(pool._requests),'connections':len(pool._connections)}
    task.cancel()
    status='UNKNOWN'
    try:await asyncio.wait_for(task,timeout=1)
    except asyncio.CancelledError:status='CANCELLED'
    await asyncio.sleep(0)
    after={'pending_requests':len(pool._requests),'connections':len(pool._connections)}
    # Clean up test resources; no external network sockets are created.
    try: await asyncio.wait_for(pool.aclose(), timeout=0.5)
    except Exception: pass
    return {'before':before,'after':after,'cancel_status':status,
            'queue_invariant':after['pending_requests']==0}


async def main():
    normal=await inspect_pool(mutate=False)
    mutated=await inspect_pool(mutate=True)
    return {'library':'httpcore','version':httpcore.__version__,
            'comparison':'installed library code versus in-memory mutation BaseException -> Exception',
            'external_io':'fake blocking network connection, actual pool queue/cleanup implementation',
            'shipped':normal, 'cancellation_handler_mutant':mutated,
            'differential_detects_mutant':normal['queue_invariant'] and not mutated['queue_invariant'],
            'scope':'Controlled mutant on real installed code; NOT historical version 0.12.3 nor upstream new defect'}

if __name__=='__main__':
    result=asyncio.run(main())
    out=Path(sys.argv[1]) if len(sys.argv)>1 else None
    if out:out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
