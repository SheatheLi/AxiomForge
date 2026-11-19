"""Independent HTTPX #3782 local-loopback reproduction attempt.

Live HTTP/1.1 stream, actual HTTPX/HTTPcore pool, no public network and no
mutation. Outcome may depend on versions and scheduler; never force success.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import httpx
import httpcore
import importlib
from importlib.metadata import version
from axiomforge.issue_engine.resource_protocol import ProtocolChecker


async def handler(reader: asyncio.StreamReader,writer: asyncio.StreamWriter):
    try:
        await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
        await writer.drain()
        for i in range(100):
            message=f'line {i}\n'.encode('ascii')
            writer.write(f'{len(message):x}\r\n'.encode()+message+b'\r\n')
            await writer.drain()
            await asyncio.sleep(.002)
        writer.write(b'0\r\n\r\n')
        await writer.drain()
    except (ConnectionError,asyncio.TimeoutError,asyncio.IncompleteReadError, BrokenPipeError):
        pass
    finally:
        writer.close()
        try:await writer.wait_closed()
        except ConnectionError:pass


async def run_variant(cancel_times:int, *, repetitions:int=5):
    server=await asyncio.start_server(handler,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    # This controlled target is loopback-only. Ambient proxies must not alter
    # the transport under test or introduce optional SOCKS dependencies.
    client=httpx.AsyncClient(trust_env=False, limits=httpx.Limits(max_connections=3,max_keepalive_connections=1),
                             timeout=httpx.Timeout(1.0))
    rows=[]
    protocol=ProtocolChecker()
    cleanup_events=[]
    replacements=[]
    for module_name, cls_name, method in (
        ('httpcore._async.http11', 'HTTP11ConnectionByteStream', 'aclose'),
        ('httpcore._async.http11', 'AsyncHTTP11Connection', '_response_closed'),
        ('httpcore._async.connection_pool','PoolByteStream','aclose')):
        cls=getattr(importlib.import_module(module_name),cls_name)
        original=getattr(cls,method)
        async def instrumented(self,*args,_orig=original,_cls=cls_name,_method=method,**kwargs):
            try:
                result=await _orig(self,*args,**kwargs)
            except asyncio.CancelledError:
                if len(cleanup_events)<200:
                    cleanup_events.append({'stage':f'{_cls}.{_method}',
                        'event':'CANCELLED_DURING_CLEANUP',
                        'closed_flag':getattr(self,'_closed',None),
                        'connection_state':str(getattr(self,'_state',None))})
                raise
            else:
                if len(cleanup_events)<200:
                    cleanup_events.append({'stage':f'{_cls}.{_method}',
                        'event':'CLEANUP_RETURNED',
                        'closed_flag':getattr(self,'_closed',None),
                        'connection_state':str(getattr(self,'_state',None))})
                return result
        setattr(cls,method,instrumented)
        replacements.append((cls,method,original))
    next_token=0
    tracked={}
    async def worker(stop):
        async def gen():
            async with client.stream('GET',f'http://127.0.0.1:{port}/stream') as response:
                try:
                    async for chunk in response.aiter_lines():
                        yield chunk
                finally:
                    await asyncio.shield(response.aclose())
        iterator=gen()
        try:
            counter=0
            async for _ in iterator:
                counter+=1
                if counter >= 5:
                    stop.set()
        finally:
            await asyncio.shield(iterator.aclose())

    try:
        for i in range(repetitions):
            stop=asyncio.Event()
            task=asyncio.create_task(worker(stop))
            try:
                await asyncio.wait_for(stop.wait(),timeout=3)
                task.cancel()
                if cancel_times==2:
                    await asyncio.sleep(0)
                    task.cancel()
                try:
                    await asyncio.wait_for(task,timeout=3)
                except asyncio.CancelledError:
                    pass
                await asyncio.sleep(0)
                pool=client._transport._pool
                active_connections=[c for c in pool.connections if not c.is_idle() and not c.is_closed()]
                for conn in pool.connections:
                    key=id(conn)
                    if key not in tracked:
                        next_token+=1
                        tracked[key]=f'httpcore-connection:{next_token}'
                pending=len(pool._requests)
                for conn in active_connections:
                    # Pool APIs report ACTIVE/IDLE and outstanding requests;
                    # this is a quiescent-pool protocol observation, not a
                    # physical socket lifetime assertion.
                    protocol.emit('pool_quiescence',resource=tracked[id(conn)],
                                  pool='httpx:local',active=True,pending_requests=pending,
                                  note=f'Post-cancel quiescent snapshot {i+1}')
                rows.append({'iteration':i+1,'active_pool_connections':len(active_connections),
                             'pending_requests':pending,
                             'pool_repr':repr(pool)})
            except (asyncio.TimeoutError,httpx.PoolTimeout) as exc:
                rows.append({'iteration':i+1,'inconclusive_or_blocked':type(exc).__name__,
                             'pool_repr':repr(client._transport._pool)})
                task.cancel()
                try:await asyncio.wait_for(task,.2)
                except (asyncio.TimeoutError,asyncio.CancelledError,Exception):pass
                break
    finally:
        try:await asyncio.wait_for(client.aclose(),timeout=2)
        except (asyncio.TimeoutError,Exception):pass
        for cls,method,original in replacements:
            setattr(cls,method,original)
        server.close()
        await server.wait_closed()
    return {'cancel_times':cancel_times,'rows':rows,
            'cleanup_events':cleanup_events,
            'protocol_summary':protocol.finalize(), 'protocol_events':protocol.events}


async def main(repetitions):
    single=await run_variant(1,repetitions=repetitions)
    double=await run_variant(2,repetitions=repetitions)
    return {'source_issue':'https://github.com/encode/httpx/issues/3782',
            'httpx_version':httpx.__version__,'httpcore_version':httpcore.__version__,
            'anyio_version':version('anyio'),'h11_version':version('h11'),
            'runtime':sys.version.split()[0],
            'server':'in-process localhost HTTP/1.1 streaming over real TCP',
            'single':single,'double':double,
            'interpretation':'Exploratory live-library reproduction attempt, not an upstream defect claim; compare active/blocked outcomes and platform-dependent nonreproduction.'}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--repetitions',type=int,default=5);args=ap.parse_args()
    report=asyncio.run(main(args.repetitions))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'single':report['single']['rows'],'double':report['double']['rows']},indent=2))
