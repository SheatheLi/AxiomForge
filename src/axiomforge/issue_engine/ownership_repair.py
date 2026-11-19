"""Generate resource ownership guards for pool idle/available predicates.

Proposes guarding an apparently idle resource if a queued/in-flight request
has reserved it. Multiple uses within the same assignment loop are protected
atomically in one proposal to avoid a half-fixed lifecycle state. Works on
AST signatures of a pool, not package names, issue IDs or exact source text.
"""
from __future__ import annotations
import ast
from collections.abc import Iterator
from .synthesis import Mutation, _span, _replace


def ownership_candidates(source: str, *, line_start: int=1, line_end: int=100000)->Iterator[Mutation]:
    tree=ast.parse(source)
    for method in ast.walk(tree):
        if not isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)):
            continue
        if not method.lineno<=line_end or method.end_lineno<line_start:
            continue
        snippet=ast.get_source_segment(source,method) or ''
        if not ('self._requests' in snippet and 'assign_to_connection' in snippet):
            continue
        sites=[]
        for node in ast.walk(method):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                and node.func.attr=='is_idle' and not node.args and not node.keywords
                and isinstance(node.func.value,ast.Name) and node.func.value.id=='connection'):
                if line_start<=node.lineno<=line_end:
                    sites.append(node)
        if not sites:continue
        # A small helper makes the ownership invariant reviewable, and keeps
        # existing comprehensions readable. Reserve checks must also apply to
        # the availability list: an assigned connection must not be lent twice.
        candidates=[(node,'idle') for node in sites]
        for node in ast.walk(method):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                and node.func.attr=='is_available' and not node.args and not node.keywords
                and isinstance(node.func.value,ast.Name) and node.func.value.id=='connection'):
                if line_start<=node.lineno<=line_end:candidates.append((node,'available'))
        if '_is_reserved' in snippet:continue
        updated=source
        for node,kind in sorted(candidates,key=lambda n:_span(source,n[0])[0],reverse=True):
            begin,end=_span(source,node)
            new=f'self._unreserved_{kind}(connection)'
            updated=_replace(updated,begin,end,new)
        method_start=_span(source,method)[0]
        method_indent=source[source.rfind('\n',0,method_start)+1:method_start]
        if method_indent.strip():continue
        addition=(f'def _is_reserved(self, connection):\n'
                  f'{method_indent}    return any(\n'
                  f'{method_indent}        getattr(request, "connection", None) is connection\n'
                  f'{method_indent}        for request in self._requests\n'
                  f'{method_indent}    )\n\n{method_indent}'
                  f'def _unreserved_idle(self, connection):\n'
                  f'{method_indent}    return connection.is_idle() and not self._is_reserved(connection)\n\n{method_indent}'
                  f'def _unreserved_available(self, connection):\n'
                  f'{method_indent}    return connection.is_available() and not self._is_reserved(connection)\n\n{method_indent}')
        updated=_replace(updated,method_start,method_start,addition)
        try:ast.parse(updated)
        except SyntaxError:continue
        yield Mutation('ownership-aware-idle-reclamation',method.lineno,
                       f'{len(sites)} observed idle checks',
                       'exclude resources already assigned to active requests',updated)
