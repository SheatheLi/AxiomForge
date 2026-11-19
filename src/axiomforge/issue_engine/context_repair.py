"""Interprocedural timeout propagation for synchronous network handshakes.

This is a *structural* candidate generator, not a hard-coded upstream patch.
It recognizes a locally-declared helper that performs stream I/O without a
bounded timeout and a caller that already computed a timeout for connection
establishment but omitted it when calling the helper.

The candidate must still pass an executable issue contract. Conservative
inference refuses unknown callers, **kwargs without a local literal mapping,
or any call that already forwards a timeout.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from .synthesis import Mutation, _span


def _replacement(source: str, edits: list[tuple[int, int, str]]) -> str:
    for (_, prev_stop, _), (next_start, _, _) in zip(
        sorted(edits), sorted(edits)[1:]
    ):
        if prev_stop > next_start:
            raise ValueError('overlapping structural edits')
    out = source
    for start, stop, text in sorted(edits, reverse=True):
        out = out[:start] + text + out[stop:]
    return out


def _read_write_calls(method: ast.FunctionDef) -> list[ast.Call]:
    operations: list[ast.Call] = []
    for expr in ast.walk(method):
        if not isinstance(expr, ast.Call) or not isinstance(expr.func, ast.Attribute):
            continue
        if (not isinstance(expr.func.value, ast.Name)
                or expr.func.value.id != 'stream'
                or expr.func.attr not in ('read', 'write')):
            continue
        if any(k.arg == 'timeout' or k.arg is None for k in expr.keywords):
            return []
        operations.append(expr)
    return operations


def _caller_has_timeout(scope: ast.FunctionDef | ast.AsyncFunctionDef, call: ast.Call) -> bool:
    """Require an actual `timeout` binding and a known kwargs literal."""
    if not (len(call.keywords) == 1 and call.keywords[0].arg is None
            and isinstance(call.keywords[0].value, ast.Name)):
        return False
    kw_name = call.keywords[0].value.id
    statements = [n for n in ast.walk(scope)
                  if isinstance(n, (ast.Assign, ast.AnnAssign))
                  and getattr(n, 'lineno', 0) < call.lineno]
    timed = any(any(isinstance(t, ast.Name) and t.id == 'timeout'
                        for t in ast.walk(n.targets[0] if isinstance(n, ast.Assign) else n.target))
                for n in statements)
    if not timed:
        return False
    # Scope must contain a literal kwargs map that omits timeout; dynamic **kwargs
    # could already contain it, which would make our candidate unsafe.
    for stmt in sorted(statements, key=lambda s: s.lineno, reverse=True):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if any(isinstance(target, ast.Name) and target.id == kw_name for target in targets):
            value = stmt.value
            if not isinstance(value, ast.Dict):
                return False
            keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
            return ('timeout' not in keys and
                    len(keys) == len(value.keys) and
                    {'stream', 'host', 'port'}.issubset(keys))
    return False


def timeout_flow_candidates(source: str, *, line_start: int = 1,
                            line_end: int = 100000) -> Iterator[Mutation]:
    """Create one coherent multi-location patch for a missing timeout contract."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for helper in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        if not line_start <= helper.lineno <= line_end:
            continue
        args = helper.args
        if 'stream' not in [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]:
            continue
        if 'timeout' in [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]:
            continue
        if args.kwarg is not None or args.vararg is not None or not args.kwonlyargs:
            continue
        io_calls = _read_write_calls(helper)
        if not io_calls or not any(c.func.attr == 'read' for c in io_calls):
            continue
        caller_calls: list[ast.Call] = []
        for caller in (n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not helper):
            for call in ast.walk(caller):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id == helper.name and _caller_has_timeout(caller, call)):
                    caller_calls.append(call)
        if not caller_calls:
            continue
        # Refuse unrecognized callers, otherwise altering the helper signature
        # could silently change the semantics of another path.
        all_calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name) and c.func.id == helper.name]
        if len(all_calls) != len(caller_calls):
            continue
        header_end = sum(len(x) for x in lines[:helper.body[0].lineno - 1])
        header_start = sum(len(x) for x in lines[:helper.lineno - 1])
        header = source[header_start:header_end]
        pos = header.rfind(')')
        if pos < 0 or helper.body[0].lineno == helper.lineno:
            continue
        position = header_start + pos
        line_before = source[:position].splitlines()[-1] if source[:position].splitlines() else ''
        # For a multi-line signature insert another keyword-only argument on
        # its own line. A single-line signature instead receives an inline
        # argument. Preserve source formatting rather than unparsing the AST.
        indent = ' ' * (helper.args.kwonlyargs[-1].col_offset)
        if not line_before.strip() or line_before.rstrip().endswith(','):
            new_parameter = indent + 'timeout: float | None = None,\n'
        else:
            new_parameter = ', timeout: float | None = None'
        edits: list[tuple[int, int, str]] = [(position, position, new_parameter)]
        okay = True
        for c in io_calls:
            begin, end = _span(source, c)
            fragment = source[begin:end]
            if not fragment.endswith(')') or fragment[:-1].rstrip().endswith(','):
                okay = False
                break
            edits.append((end - 1, end - 1, ', timeout=timeout'))
        if not okay:
            continue
        for c in caller_calls:
            begin, end = _span(source, c)
            frag = source[begin:end]
            if frag.replace(' ', '') != f'{helper.name}(**{c.keywords[0].value.id})':
                okay = False
                break
            at = begin + frag.index('(') + 1
            edits.append((at, at, 'timeout=timeout, '))
        if not okay:
            continue
        try:
            updated = _replacement(source, edits)
            ast.parse(updated)
        except (ValueError, SyntaxError):
            continue
        yield Mutation('interprocedural-timeout-propagation', helper.lineno,
                       'timeout computed but missing across call boundary and stream I/O',
                       'forward timeout through helper signature, call, and stream I/O', updated)
