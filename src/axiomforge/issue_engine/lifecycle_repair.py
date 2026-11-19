"""Effect-guided, source-preserving resource-initialization repair candidates.

Recognizes resource acquisition inside a try with post-acquisition initialization
in an else clause.  If the class already has an explicit close operation for the
same resource, move initialization into the try and propose cleanup on failure.

The operator is conservative: it refuses ambiguous handlers, nested resources,
absent cleanup functions, and cross-function inferences.  A candidate must then
pass executable before/after and control tests; AST similarity is never proof.
"""
from __future__ import annotations
import ast
from typing import Iterator
from .synthesis import Mutation


def _self_field(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'self':
        return node.attr
    return None


def _line_indent(line: str) -> str:
    return line[:len(line)-len(line.lstrip())]


def initialization_cleanup_candidates(source: str, *, line_start: int = 1,
                                      line_end: int = 100000) -> Iterator[Mutation]:
    """Yield edits based on actual ownership and cleanup calls in the class."""
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        # Only infer close method if it directly calls a resource closer.
        cleaners: dict[str,set[str]]={}
        for method in cls.body:
            if not isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            if 'close' not in method.name.lower():continue
            for c in ast.walk(method):
                if not isinstance(c,ast.Call) or not isinstance(c.func,ast.Attribute):continue
                if not any(x in c.func.attr.lower() for x in ('close','release','dispose')):continue
                if 'dispatch' in ast.unparse(c.func) or c.func.attr in ('close', 'dispatch_close'):continue
                for arg in c.args:
                    field=_self_field(arg)
                    if field:
                        cleaners.setdefault(field,set()).add(ast.unparse(c.func))
        for method in cls.body:
            if not isinstance(method,ast.FunctionDef):continue
            for t in ast.walk(method):
                if not isinstance(t,ast.Try) or not t.orelse or len(t.handlers)!=1 or t.finalbody:continue
                if not line_start<=t.lineno<=line_end:continue
                handler=t.handlers[0]
                if (not isinstance(handler.type,ast.Name) or handler.type.id!='BaseException'
                    or len(handler.body)!=1 or not isinstance(handler.body[0],ast.With)):
                    continue
                protected=handler.body[0]
                if len(protected.items)!=1 or not protected.body:continue
                # Match an explicit exception-preserving context manager; do not silently
                # swallow cleanup errors or obscure the original exception.
                if 'safe_reraise' not in ast.unparse(protected.items[0].context_expr):continue
                acquired=set()
                for stmt in t.body:
                    for sub in ast.walk(stmt):
                        if not isinstance(sub,ast.Assign) or not isinstance(sub.value,ast.Call):continue
                        acquired.update(filter(None,(_self_field(target) for target in sub.targets)))
                owns=[name for name in acquired if len(cleaners.get(name,()))==1]
                if len(owns)!=1:continue
                field=owns[0]
                close=next(iter(cleaners[field]))
                # Exclude if the except branch already contains cleanup.
                if field in ast.unparse(handler):continue
                # Only accept the straightforward syntax used by safe-reraise handlers.
                try_header=t.lineno-1
                except_header=handler.lineno-1
                last_else=t.orelse[-1].end_lineno
                first_else=t.orelse[0].lineno-1
                else_header=first_else-1
                while else_header>except_header and not lines[else_header].lstrip().startswith('else:'):
                    else_header-=1
                if else_header<=except_header or lines[else_header].strip()!='else:':continue
                if len(_line_indent(lines[try_header]))!=len(_line_indent(lines[else_header])):continue
                if last_else != t.end_lineno:continue
                insert_at=protected.body[0].lineno-1
                if not except_header < insert_at < else_header:continue
                indentation=_line_indent(lines[insert_at])
                extra=''.join((
                    f'{indentation}if self.{field} is not None:\n',
                    f'{indentation}    try:\n',
                    f'{indentation}        {close}(self.{field})\n',
                    f'{indentation}    finally:\n',
                    f'{indentation}        self.{field} = None\n',
                ))
                # Post-acquisition initialization belongs inside the try, so that
                # errors raised by event handlers are handled and cleaned up.
                updated=''.join(
                    lines[:except_header] + lines[first_else:last_else]
                    + lines[except_header:insert_at] + [extra]
                    + lines[insert_at:else_header] + lines[last_else:]
                )
                try:
                    ast.parse(updated)
                except SyntaxError:
                    continue
                yield Mutation('acquire-initialize-cleanup',t.lineno,
                               f'post-acquire event outside try: {field}',
                               'guarded close on failed initialization',updated)
