"""Bounded, fail-closed intraprocedural CFG/reaching-definitions analysis.

Unlike a line-ranking-only fault spectrum, the result contains the actual
may-reaching definitions and control predicates for a statement. This is a
conservative *may* analysis, not SSA, interprocedural alias/heap analysis,
or a causal proof. Constructs not modelled are rejected rather than silently
creating a fictitious complete control-flow graph.
"""
from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import IssueError


@dataclass
class FlowNode:
    index: int
    line: int
    kind: str
    defines: set[str] = field(default_factory=set)
    reads: set[str] = field(default_factory=set)
    successors: set[int] = field(default_factory=set)
    guards: tuple[int, ...] = ()
    weak_writes: set[str] = field(default_factory=set)


def _names(node: ast.AST | None, *, context: type[ast.expr_context] = ast.Load) -> set[str]:
    """Read names, respecting comprehension-local bindings and lexical scopes."""
    if node is None:
        return set()
    if context is not ast.Load:
        return {x.id for x in ast.walk(node)
                if isinstance(x, ast.Name) and isinstance(x.ctx, context)}
    names: set[str] = set()

    def visit(part: ast.AST, bound: frozenset[str]) -> None:
        if isinstance(part, ast.Name):
            if isinstance(part.ctx, ast.Load) and part.id not in bound:
                names.add(part.id)
            return
        if isinstance(part, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            local = bound
            for generator in part.generators:
                visit(generator.iter, local)  # binding is not yet active in iter
                local = local | _targets(generator.target)
                for condition in generator.ifs:
                    visit(condition, local)
            if isinstance(part, ast.DictComp):
                visit(part.key, local); visit(part.value, local)
            else:
                visit(part.elt, local)
            return
        if isinstance(part, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            # Opaque nested lexical scope, not an inline function call.
            return
        for sub in ast.iter_child_nodes(part):
            visit(sub, bound)

    visit(node, frozenset())
    return names


def _targets(node: ast.AST) -> set[str]:
    return _names(node, context=ast.Store)


class _Graph:
    def __init__(self, func: ast.FunctionDef | ast.AsyncFunctionDef):
        self.func = func
        self.nodes: dict[int, FlowNode] = {}
        self.counter = 0
        params = {x.arg for x in (*func.args.posonlyargs, *func.args.args,
                                 *func.args.kwonlyargs)}
        if func.args.vararg:
            params.add(func.args.vararg.arg)
        if func.args.kwarg:
            params.add(func.args.kwarg.arg)
        self.entry = self.node(func.lineno, 'entry-parameters', params, set(), ())
        first = self.block(func.body, None, ())
        if first is not None:
            self.nodes[self.entry].successors.add(first)

    def node(self, line: int, kind: str, defines: set[str], reads: set[str],
             guards: tuple[int, ...]) -> int:
        if len(self.nodes) >= 3000:
            raise IssueError('CFG exceeds 3000 statements; narrow analysis to a function')
        self.counter += 1
        self.nodes[self.counter] = FlowNode(self.counter, line, kind, defines, reads,
                                            guards=guards)
        return self.counter

    def block(self, statements: list[ast.stmt], next_id: int | None,
              guards: tuple[int, ...]) -> int | None:
        current = next_id
        for statement in reversed(statements):
            if isinstance(statement, ast.If):
                k = self.node(statement.lineno, 'if', set(), _names(statement.test), guards)
                yes = self.block(statement.body, current, guards + (k,))
                no = self.block(statement.orelse, current, guards + (k,))
                self.nodes[k].successors.update(i for i in (yes, no) if i is not None)
                current = k
            elif isinstance(statement, (ast.While, ast.For, ast.AsyncFor)):
                if isinstance(statement, ast.While):
                    defined, read = set(), _names(statement.test)
                else:
                    defined, read = _targets(statement.target), _names(statement.iter)
                if statement.orelse:
                    raise IssueError('loop else not modelled; refuse unsafe dependency claim')
                k = self.node(statement.lineno, type(statement).__name__.lower(), defined, read, guards)
                body = self.block(statement.body, k, guards + (k,))
                self.nodes[k].successors.update(i for i in (body, current) if i is not None)
                current = k
            else:
                if isinstance(statement, (ast.Try, getattr(ast, 'TryStar', ast.Try), ast.With, ast.AsyncWith,
                                          ast.Match, ast.Break, ast.Continue,
                                          ast.Global, ast.Nonlocal, ast.FunctionDef,
                                          ast.AsyncFunctionDef, ast.ClassDef)):
                    raise IssueError(f'CFG does not model {type(statement).__name__}; narrow function or extend analysis')
                defines: set[str] = set()
                reads: set[str] = set()
                if isinstance(statement, ast.Assign):
                    defines = set().union(*(_targets(x) for x in statement.targets))
                    reads = _names(statement.value)
                elif isinstance(statement, ast.AnnAssign):
                    defines = _targets(statement.target)
                    reads = _names(statement.value)
                elif isinstance(statement, ast.AugAssign):
                    defines = _targets(statement.target)
                    reads = _names(statement.target) | defines | _names(statement.value)
                elif isinstance(statement, (ast.Return, ast.Raise, ast.Expr, ast.Assert)):
                    reads = _names(statement)
                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                        call = statement.value
                        if (isinstance(call.func, ast.Attribute)
                                and isinstance(call.func.value, ast.Name)
                                and call.func.attr in {'append', 'extend', 'clear', 'pop', 'remove',
                                                      'update', 'add', 'discard', 'sort', 'reverse', 'setdefault'}):
                            # Bounded effect summary. A method invocation is an additive
                            # possible definition of local object state, NOT a rebinding.
                            defines.add(call.func.value.id)
                elif isinstance(statement, ast.Pass):
                    pass
                else:
                    raise IssueError(f'CFG does not model {type(statement).__name__}; refuse misleading dataflow')
                # Walrus expression and closure/heap side effects require stronger modelling.
                if any(isinstance(x, (ast.NamedExpr, ast.Yield, ast.YieldFrom))
                       for x in ast.walk(statement)):
                    raise IssueError('CFG does not model walrus or generator suspension')
                k = self.node(statement.lineno, type(statement).__name__.lower(), defines, reads, guards)
                if isinstance(statement, ast.Expr):
                    self.nodes[k].weak_writes = set(defines)
                if not isinstance(statement, (ast.Return, ast.Raise)) and current is not None:
                    self.nodes[k].successors.add(current)
                current = k
        return current

    def solve(self) -> tuple[dict[int, dict[str, set[int]]], int]:
        """Worklist fixed point of forward may-reaching-definition sets."""
        predecessors: dict[int, set[int]] = {i: set() for i in self.nodes}
        for node in self.nodes.values():
            for s in node.successors:
                predecessors[s].add(node.index)
        incoming: dict[int, dict[str, set[int]]] = {i: {} for i in self.nodes}
        outgoing: dict[int, dict[str, set[int]]] = {i: {} for i in self.nodes}
        work = deque(sorted(self.nodes))
        queued = set(work)
        passes = 0
        while work:
            k = work.popleft(); queued.remove(k)
            passes += 1
            if passes > 200000:
                raise IssueError('dataflow worklist did not converge within bound')
            merged: dict[str, set[int]] = {}
            for p in predecessors[k]:
                for name, definitions in outgoing[p].items():
                    merged.setdefault(name, set()).update(definitions)
            incoming[k] = merged
            current = {name: set(definitions) for name, definitions in merged.items()}
            for name in self.nodes[k].defines:
                current[name] = (current.get(name, set()) | {k}
                                 if name in self.nodes[k].weak_writes else {k})
            if current != outgoing[k]:
                outgoing[k] = current
                for next_id in sorted(self.nodes[k].successors):
                    if next_id not in queued:
                        work.append(next_id); queued.add(next_id)
        return incoming, passes


def _find_function(source: str, line: int) -> ast.FunctionDef | ast.AsyncFunctionDef:
    try:
        root = ast.parse(source)
    except SyntaxError as exc:
        raise IssueError(f'cannot parse source for static dataflow: {exc.msg}') from exc
    matches = [f for f in ast.walk(root)
               if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
               and f.lineno <= line <= f.end_lineno]
    if not matches:
        raise IssueError(f'no function contains source line {line}; function-level analysis only')
    matches.sort(key=lambda f: (f.end_lineno - f.lineno, f.lineno))
    return matches[0]


def backward_slice(source: str, line: int, *, max_depth: int = 12,
                   max_sites: int = 120) -> dict[str, Any]:
    """Explain which reaching definitions and control predicates can influence line."""
    if not 1 <= line <= len(source.splitlines()) or not 1 <= max_depth <= 100:
        raise IssueError('invalid dataflow slice bounds')
    func = _find_function(source, line)
    try:
        graph = _Graph(func)
    except RecursionError as exc:
        raise IssueError('CFG construction exceeds supported nesting/statement depth') from exc
    incoming, iterations = graph.solve()
    seeds = [k for k, n in graph.nodes.items() if n.line == line and n.kind != 'entry-parameters']
    if not seeds:
        raise IssueError(f'line {line} has no modelled executable statement')
    reached: set[int] = set()
    queue = deque((k, 0, 'seed') for k in seeds)
    notes: dict[int, set[str]] = {}
    edges: set[tuple[int, int, str]] = set()
    while queue:
        k, depth, why = queue.popleft()
        notes.setdefault(k, set()).add(why)
        if k in reached:
            continue
        if len(reached) >= max_sites or depth > max_depth:
            raise IssueError('slice budget exceeded; do not report truncated dependency result')
        reached.add(k)
        node = graph.nodes[k]
        for name in sorted(node.reads):
            for provider in sorted(incoming[k].get(name, set())):
                edges.add((provider, k, f'def-use:{name}'))
                queue.append((provider, depth + 1, f'def-use:{name}'))
        for guard in node.guards:
            edges.add((guard, k, 'control-dependency'))
            queue.append((guard, depth + 1, 'control-dependency'))
    related = sorted(reached, key=lambda k: (graph.nodes[k].line, k))
    return {
        'schema': 'axiomforge.intraprocedural-dataflow.v1', 'status': 'ANALYZED',
        'scope': func.name, 'scope_lines': [func.lineno, func.end_lineno],
        'seed_line': line, 'cfg_nodes': len(graph.nodes), 'cfg_edges': sum(len(n.successors) for n in graph.nodes.values()),
        'worklist_iterations': iterations, 'slice_nodes': len(reached),
        'sliced_lines': sorted({graph.nodes[k].line for k in reached}),
        'sites': [{'line': graph.nodes[k].line, 'kind': graph.nodes[k].kind,
                   'defines': sorted(graph.nodes[k].defines),
                   'weak_heap_writes': sorted(graph.nodes[k].weak_writes),
                   'reads': sorted(graph.nodes[k].reads),
                   'evidence': sorted(notes.get(k, set()))} for k in related],
        'dependencies': [{'source_line': graph.nodes[a].line, 'target_line': graph.nodes[b].line,
                          'type': kind} for a, b, kind in sorted(edges)],
        'limitations': 'Local may-reaching definitions, selected built-in in-place mutation effects, and modelled if/for/while; no SSA, cross-function calls, arbitrary heap aliasing, exceptions, concurrency, or causal proof.'
    }


def slice_file(path: Path, line: int, **kwargs: Any) -> dict[str, Any]:
    if path.suffix != '.py' or not path.is_file() or path.stat().st_size > 350000:
        raise IssueError('dataflow analysis requires a bounded existing Python file')
    result = backward_slice(path.read_text('utf8'), line, **kwargs)
    result['source_file'] = str(path)
    return result


def interprocedural_slice(source: str, line: int, *, max_call_depth: int = 2,
                          max_sites: int = 120) -> dict[str, Any]:
    """Lexical same-class ``self.method()`` expansion of local may-dependence.

    Resolves direct calls only when the callee has a unique definition in the
    same class. This is a conservative search *hypothesis*, not a closed-world
    dispatch guarantee: subclass overrides and monkeypatching may change the
    runtime callee. Each referenced method is analyzed independently, and any
    unsupported method is marked PARTIAL rather than silently skipped.
    """
    if not 0 <= max_call_depth <= 3:
        raise IssueError('interprocedural call depth must be 0..3')
    initial = backward_slice(source, line, max_sites=max_sites)
    if not max_call_depth:
        return {**initial, 'analysis_mode': 'intraprocedural-only',
                'call_graph_edges': [], 'call_graph_status': 'NOT_REQUESTED'}
    root = ast.parse(source)
    caller = _find_function(source, line)
    owner = next((klass for klass in ast.walk(root)
                  if isinstance(klass, ast.ClassDef) and any(
                      isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and m.name == caller.name and m.lineno == caller.lineno
                      for m in klass.body)), None)
    if owner is None:
        return {**initial, 'analysis_mode': 'intraprocedural-only',
                'call_graph_edges': [], 'call_graph_status': 'NOT_APPLICABLE'}
    methods = {m.name: m for m in owner.body
               if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    caller = methods[caller.name]
    aggregated = set(initial['sliced_lines'])
    expansions = [initial]
    edges: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    # Each callee is expanded once; recursive cycles cannot spin forever.
    visited = {caller.name}
    pending = deque([(caller, initial, 0, (caller.name,))])
    while pending:
        function, receipt, depth, active_chain = pending.popleft()
        if depth >= max_call_depth:
            continue
        influencing_lines = set(receipt['sliced_lines'])
        for node in ast.walk(function):
            if not (isinstance(node, ast.Call) and node.lineno in influencing_lines
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'self'):
                continue
            target = methods.get(node.func.attr)
            if target is None:
                continue
            if target.name in active_chain:
                errors.append({'caller':function.name,'callee':target.name,
                               'reason':'recursive or cyclic direct call skipped; partial provenance only'})
                continue
            returns = [r for r in ast.walk(target) if isinstance(r, ast.Return)]
            edges.append({'caller': function.name, 'callee': target.name,
                          'call_line': node.lineno,
                          'return_lines':sorted({r.lineno for r in returns}),
                          'resolution': 'lexical-same-class-self-call'})
            if target.name in visited:
                # Repeated call sites share one bounded callee analysis; do not
                # falsely label a second call as recursion or lose its edge.
                continue
            visited.add(target.name)
            if not returns:
                errors.append({'caller': function.name, 'callee': target.name,
                               'reason': 'callee has no explicit return for dependency seed'})
                continue
            for ret in returns:
                try:
                    result = backward_slice(source, ret.lineno, max_sites=max_sites)
                except IssueError as exc:
                    errors.append({'caller':function.name,'callee':target.name,
                                   'reason': str(exc)})
                    continue
                aggregated.update(result['sliced_lines'])
                expansions.append(result)
                pending.append((target, result, depth+1, active_chain+(target.name,)))
    if len(aggregated) > max_sites * (max_call_depth + 1):
        raise IssueError('interprocedural slice exceeds bounded total sites')
    return {**initial, 'schema':'axiomforge.lexical-interprocedural-dataflow.v1',
            'status':'PARTIAL' if errors else 'ANALYZED',
            'analysis_mode':'intra-procedural CFG + direct same-class self calls',
            'sliced_lines':sorted(aggregated),
            'call_graph_edges':edges,
            'call_graph_status':'PARTIAL' if errors else 'COMPLETE_WITHIN_LEXICAL_SCOPE',
            'method_count':len({r['scope'] for r in expansions}),
            'method_evidence':[{'scope':r['scope'], 'scope_lines':r['scope_lines'],
                                'sliced_lines':r['sliced_lines'], 'cfg_nodes':r['cfg_nodes'],
                                'worklist_iterations':r['worklist_iterations']} for r in expansions],
            'unsupported_callees':errors,
            'limitations':('Same-class lexical direct calls only, not dynamic dispatch, virtual override resolution, '
                           'alias/heap object identity, arbitrary cross-module import, concurrency, exception flow or causal proof.')}
