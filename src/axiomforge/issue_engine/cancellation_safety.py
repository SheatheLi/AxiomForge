"""Bounded cancellation safety exploration for trusted Python async code.

A deterministic *intervention* tester, not a proof of all interleavings:
  - AST compiler-level injection before explicit `await` expressions
  - native Task.cancel() requested at each reachable explicit await; actual
    delivery occurs at a real suspension point, not an inserted Future
  - fresh scenario factory on each run (no state reuse)
  - post-cancellation resource protocol oracle with strict ownership accounting
  - proof of *one* real violating schedule when an invariant fails

No claim is made about thread scheduling, native code, async-for/with implicit awaits,
or exhaustively proving the absence of leaks.
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

_active: contextvars.ContextVar["Gate | None"] = contextvars.ContextVar("axiom_cancel_gate", default=None)


@dataclass(frozen=True)
class Site:
    id: str
    function: str
    line: int
    column: int


class _AwaitTransformer(ast.NodeTransformer):
    def __init__(self, filename: str):
        self.filename = filename
        self.scope: list[str] = []
        self.sites: list[Site] = []
        self._counter = 0

    def visit_ClassDef(self, node: ast.ClassDef):
        self.scope.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        # Do not introduce await into synchronous functions. Nested async
        # functions are handled when visited as independent nodes.
        self.scope.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.scope.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.scope.pop()

    def visit_Await(self, node: ast.Await):
        # Keep the original await expression as the second tuple element;
        # the first is an awaitable controller checkpoint. In Python, tuple
        # expressions evaluate left-to-right, so the actual awaitable is not
        # created until after the injected checkpoint has completed.
        # This avoids dropping a pending coroutine when the gate cancels.
        original = self.generic_visit(node)
        idx = self._counter
        self._counter += 1
        scope = ".".join(self.scope) or "module"
        site_id = f"{self.filename}:{scope}:{node.lineno}:{node.col_offset}:{idx}"
        self.sites.append(Site(site_id, scope, node.lineno, node.col_offset))
        injected = ast.Await(value=ast.Call(func=ast.Name(id="_axiom_checkpoint",ctx=ast.Load()),
                                             args=[ast.Constant(site_id)],keywords=[]))
        pair = ast.Tuple(elts=[injected, original], ctx=ast.Load())
        selector = ast.Subscript(value=pair, slice=ast.Constant(1), ctx=ast.Load())
        return ast.copy_location(selector, node)


async def _axiom_checkpoint(site_id: str) -> None:
    gate = _active.get()
    if gate is not None:
        await gate.hit(site_id)


@dataclass(frozen=True)
class Instrumented:
    namespace: dict[str, Any]
    sites: tuple[Site, ...]
    source: str


def instrument_source(source: str, *, filename: str = "<trusted-scenario>",
                      namespace: Mapping[str, Any] | None = None) -> Instrumented:
    """Compile trusted Python source with checkpoint at every explicit await.

    Never use this with untrusted input. The compiler executes source through
    exec(), and injected async checkpoints intentionally change scheduling.
    """
    tree = ast.parse(source, filename=filename)
    transformer = _AwaitTransformer(filename)
    transformed = transformer.visit(tree)
    ast.fix_missing_locations(transformed)
    env = dict(namespace or {})
    env["_axiom_checkpoint"] = _axiom_checkpoint
    exec(compile(transformed, filename, "exec"), env)
    return Instrumented(env, tuple(transformer.sites), source)


@dataclass
class Ledger:
    """A strict allocation/release ledger; tracks concrete ownership IDs."""
    held: set[str] = field(default_factory=set)
    history: list[tuple[str, str]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def acquire(self, resource_id: str) -> None:
        if resource_id in self.held:
            self.violations.append(f"duplicate-acquire:{resource_id}")
        self.held.add(resource_id)
        self.history.append(("acquire", resource_id))

    def release(self, resource_id: str) -> None:
        if resource_id not in self.held:
            self.violations.append(f"invalid-release:{resource_id}")
        else:
            self.held.remove(resource_id)
        self.history.append(("release", resource_id))

    def snapshot(self) -> dict[str, Any]:
        return {"held": sorted(self.held), "violations": list(self.violations),
                "history": [list(item) for item in self.history]}


@dataclass
class Scenario:
    operation: Callable[[], Awaitable[Any]]
    observe: Callable[[], Mapping[str, Any]]
    # User can add protocol-specific invariants; defaults to empty ledger.
    invariant: Callable[[Mapping[str, Any]], bool] | None = None
    recovery: Callable[[], Awaitable[Any]] | None = None
    risk_probe: Callable[[], bool] | None = None  # optional observed in-flight resource obligation


@dataclass
class Gate:
    site: str | None
    seen: list[str] = field(default_factory=list)
    reached: asyncio.Event = field(default_factory=asyncio.Event)
    occurrence: int = 1
    _hits: int = 0
    fired: bool = False
    risk_probe: Callable[[], bool] | None = None
    risky_sites: list[str] = field(default_factory=list)
    risk_errors: list[str] = field(default_factory=list)

    async def hit(self, site_id: str) -> None:
        self.seen.append(site_id)
        if self.risk_probe is not None:
            try:
                if self.risk_probe():
                    self.risky_sites.append(site_id)
            except Exception as exc:
                self.risk_errors.append(f"{type(exc).__name__}: {exc}")
        if site_id == self.site:
            self._hits += 1
            if self._hits == self.occurrence:
                self.fired = True
                self.reached.set()
                # Request native asyncio cancellation: delivery occurs at the
                # awaited operation's actual suspension, not at an invented
                # Future() suspension. Synchronous awaitables may complete.
                task=asyncio.current_task()
                if task is not None:
                    task.cancel()


async def _run(factory: Callable[[], Scenario], *, site: str | None,
               timeout: float, settle_ticks: int = 2, occurrence: int = 1) -> dict[str, Any]:
    scenario = factory()
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario factory must return Scenario")
    gate = Gate(site, occurrence=occurrence, risk_probe=scenario.risk_probe)
    async def execute():
        token = _active.set(gate)
        try:
            return await scenario.operation()
        finally:
            _active.reset(token)
    worker = asyncio.create_task(execute())
    status = "OK"
    error = None
    try:
        if site is not None:
            waiter = asyncio.create_task(gate.reached.wait())
            done, pending = await asyncio.wait({worker, waiter}, timeout=timeout,
                                               return_when=asyncio.FIRST_COMPLETED)
            if not done:
                status = "TIMEOUT"
            elif waiter in done and gate.fired:
                pass  # checkpoint has already called Task.cancel()
            elif worker.done():
                status = "UNREACHED_SITE"
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
        try:
            await asyncio.wait_for(worker, timeout=timeout)
        except asyncio.CancelledError:
            status = "CANCELLED" if site is not None and gate.fired else "UNEXPECTED_CANCEL"
        except asyncio.TimeoutError:
            status = "TIMEOUT"
            worker.cancel()
        except Exception as exc:
            status = "ERROR"
            error = f"{type(exc).__name__}: {exc}"
    finally:
        if not worker.done():
            worker.cancel()
        if not worker.done():
            try:
                await asyncio.wait_for(worker, timeout=min(timeout, 0.25))
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        # Allow scheduled finalizers to run. This is not a quiescence proof.
        for _ in range(settle_ticks):
            await asyncio.sleep(0)
    recovery_status = "NOT_CONFIGURED"
    if site is not None and gate.fired and scenario.recovery is not None:
        try:
            await asyncio.wait_for(scenario.recovery(), timeout=min(timeout, 0.25))
            recovery_status = "OK"
        except asyncio.TimeoutError:
            recovery_status = "BLOCKED"
        except Exception as exc:
            recovery_status = f"ERROR:{type(exc).__name__}"
    observed = dict(scenario.observe())
    if scenario.invariant is None:
        ok = not observed.get("held") and not observed.get("violations")
    else:
        ok = bool(scenario.invariant(observed))
    return {"site": site, "status": status, "gate_reached": gate.fired,
            "trace": list(gate.seen), "risk_sites": list(gate.risky_sites),
            "risk_errors": list(gate.risk_errors), "risk_probe_available": bool(scenario.risk_probe),
            "observed": observed, "invariant_pass": ok,
            "error": error, "recovery_status": recovery_status}


async def explore(factory: Callable[[], Scenario], instrumented: Instrumented,
                  *, max_sites: int = 100, timeout: float = 1.0,
                  max_occurrences: int = 1, risk_guided: bool = False) -> dict[str, Any]:
    if max_sites < 1 or timeout <= 0 or max_occurrences < 1:
        raise ValueError("Invalid exploration bounds")
    baseline = await _run(factory, site=None, timeout=timeout)
    # Only sites reached during baseline qualify for this bounded exploration.
    trace = baseline["trace"]
    unique = list(dict.fromkeys(trace))
    if risk_guided:
        if baseline["risk_errors"]:
            raise ValueError(f"Risk observation failed: {baseline['risk_errors']}")
        if not baseline["risk_probe_available"]:
            raise ValueError("risk_guided needs Scenario.risk_probe")
        selected = [site for site in unique if site in set(baseline["risk_sites"])]
    else:
        selected = unique
    sites = selected[:max_sites]
    experiments: list[dict[str, Any]] = []
    for s in sites:
        count = min(trace.count(s), max_occurrences)
        for occ in range(1, count + 1):
            item = await _run(factory, site=s, timeout=timeout, occurrence=occ)
            item["occurrence"] = occ
            experiments.append(item)
    violations = [i for i in experiments if i["gate_reached"] and not i["invariant_pass"]]
    unknowns = [i for i in experiments if i["status"] not in {"CANCELLED", "OK"}]
    return {"schema": "axiomforge.cancel-exploration.v1", "method": "bounded-await-site-cancellation",
            "baseline": baseline, "eligible_sites": len(unique),
            "tested_sites": len(sites), "risk_guided": risk_guided,
            "risk_excluded_sites": len(unique)-len(selected), "experiments": experiments,
            "violations": len(violations), "unknown_results": len(unknowns),
            "first_counterexample": violations[0] if violations else None,
            "sites": [vars(x) for x in instrumented.sites],
            "scope": "explicit await only; reachable baseline paths; cancellation does not prove all interleavings"}


@dataclass
class ScheduleGate:
    """Two-or-more cancellation interventions, injected in a fixed order."""
    targets: tuple[str, ...]
    seen: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    signal: asyncio.Queue[str] = field(default_factory=asyncio.Queue)

    async def hit(self, site_id: str) -> None:
        self.seen.append(site_id)
        if len(self.fired) < len(self.targets) and site_id == self.targets[len(self.fired)]:
            self.fired.append(site_id)
            self.signal.put_nowait(site_id)
            task=asyncio.current_task()
            if task is not None:
                task.cancel()


async def _run_schedule(factory: Callable[[], Scenario], schedule: tuple[str, ...],
                        *, timeout: float) -> dict[str, Any]:
    """Reproduce an ordered cancellation trace, including cancellation in cleanup."""
    gate = ScheduleGate(schedule)
    case = factory()
    if not isinstance(case,Scenario):raise TypeError('factory must produce fresh Scenario')
    async def execute():
        token=_active.set(gate)
        try:return await case.operation()
        finally:_active.reset(token)
    task=asyncio.create_task(execute())
    status='OK'
    error=None
    try:
        for target in schedule:
            take=asyncio.create_task(gate.signal.get())
            done,_=await asyncio.wait({task,take},timeout=timeout,return_when=asyncio.FIRST_COMPLETED)
            if take in done:
                actual=take.result()
                if actual!=target:raise AssertionError('checkpoint ordering changed')
                pass  # self-cancellation issued by the instrumented task
            elif not done:
                status='TIMEOUT'
            else:
                status='UNREACHED_SITE'
            if not take.done():
                take.cancel()
                try:await take
                except asyncio.CancelledError:pass
            if status!='OK':break
        try:
            await asyncio.wait_for(task,timeout=timeout)
        except asyncio.CancelledError:
            if status=='OK':status='CANCELLED'
        except asyncio.TimeoutError:
            status='TIMEOUT'
        except Exception as exc:
            status='ERROR';error=f'{type(exc).__name__}: {exc}'
    finally:
        if not task.done():
            task.cancel()
            try:await asyncio.wait_for(task,timeout=0.25)
            except (asyncio.TimeoutError,asyncio.CancelledError,Exception):pass
        for _ in range(2):await asyncio.sleep(0)
    observed=dict(case.observe())
    passed=(bool(case.invariant(observed)) if case.invariant else
            not observed.get('held') and not observed.get('violations'))
    return {'schedule':list(schedule),'fired':list(gate.fired),'status':status,
            'trace':list(gate.seen),'observed':observed,
            'invariant_pass':passed,'error':error}


async def explore_nested_cancellation(factory:Callable[[],Scenario], unit:Instrumented,
                                      *, max_depth:int=2,max_runs:int=50,
                                      timeout:float=0.5)->dict[str,Any]:
    """Explore cancellation-induced paths via bounded breadth-first schedules.

    A cancellation can enter an otherwise unreached cleanup path. One-site
    testing may miss a second cancellation *inside* that cleanup. Start with
    normal-path awaits, then mine newly observed checkpoint sites from each
    cancellation trace. No completeness claim is made for other schedules.
    """
    if not 1<=max_depth<=3 or max_runs<1 or timeout<=0:
        raise ValueError('Invalid nested exploration bounds')
    baseline=await _run_schedule(factory,(),timeout=timeout)
    queue=[(site,) for site in dict.fromkeys(baseline['trace'])]
    seen=set(queue)
    results=[]
    while queue and len(results)<max_runs:
        schedule=queue.pop(0)
        outcome=await _run_schedule(factory,schedule,timeout=timeout)
        results.append(outcome)
        if len(schedule)>=max_depth or len(outcome['fired'])!=len(schedule):continue
        # Sites that only arise after injecting cancellation include
        # exceptional cleanup and cancellation-handler awaits. Limit expansion
        # to the not-yet-faulted tail of this exact trace.
        trace=outcome['trace']
        start=trace.index(schedule[-1])+1 if schedule[-1] in trace else len(trace)
        for target in dict.fromkeys(trace[start:]):
            nxt=schedule+(target,)
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    bugs=[item for item in results if len(item['fired'])==len(item['schedule'])
          and not item['invariant_pass']]
    return {'schema':'axiomforge.nested-cancellation.v1','method':'failure-induced-site-BFS',
            'baseline':baseline,'max_depth':max_depth,'max_runs':max_runs,
            'executed':len(results),'violations':len(bugs),
            'first_counterexample':bugs[0] if bugs else None,
            'runs':results, 'exhausted_frontier':not queue,
            'limitations':'bounded schedules; only explicit awaits; injected scheduling and finite settling; no all-schedule safety proof'}
