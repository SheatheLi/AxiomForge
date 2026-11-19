"""Semantics-aware guards for symbolic operator inversions.

An AST condition may inspect only the first lexer token of a comparison
operator before rewriting the operator while swapping operands. If the token
sequence represents a *self-dual composite* operator, replacing its first
character changes program meaning. This operator family recognizes an existing
reciprocal inversion map, never alters <= or >= inversion, and demands an
executable before/after contract before accepting a candidate.

No issue URL, project name, source-file name or predetermined patch is used.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from .synthesis import Mutation, _span, _replace


def _has_comparator_inversion(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        mapping = {}
        for k,v in zip(node.keys,node.values):
            if (isinstance(k,ast.Constant) and isinstance(v,ast.Constant)
                    and isinstance(k.value,str) and isinstance(v.value,str)):
                mapping[k.value]=v.value
        if mapping.get('<') == '>' and mapping.get('>') == '<':
            return True
    return False


def operator_shape_candidates(source: str, *, line_start: int = 1,
                              line_end: int = 100000) -> Iterator[Mutation]:
    tree=ast.parse(source)
    if not _has_comparator_inversion(tree):
        return
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If,ast.IfExp)) or not line_start <= node.lineno <= line_end:
            continue
        cond=node.test
        if not isinstance(cond,ast.BoolOp) or not isinstance(cond.op,ast.And):
            continue
        names={part.id for part in cond.values if isinstance(part,ast.Name)}
        names &= {part.value.id for part in ast.walk(cond)
                  if isinstance(part,ast.Subscript)
                  and isinstance(part.value,ast.Name)
                  and isinstance(part.slice,ast.Constant)
                  and part.slice.value == 0}
        begin,end=_span(source,cond)
        fragment=source[begin:end]
        for name in sorted(names):
            has_raw=any(isinstance(part,ast.Attribute) and part.attr=='raw'
                and isinstance(part.value,ast.Subscript)
                and isinstance(part.value.value,ast.Name)
                and part.value.value.id==name
                for part in ast.walk(cond))
            if not has_raw:
                continue
            # '<=>' and '<>' are self-dual; '<=', '>=' are not.
            # Guard the complete normalized token spelling, not token count.
            guard=f"''.join(__part.raw for __part in {name}) not in ('<=>', '<>')"
            updated=_replace(source,begin,end,f'{guard} and ({fragment})')
            try:ast.parse(updated)
            except SyntaxError:continue
            yield Mutation('composite-operator-inversion-guard',node.lineno,
                           fragment,guard,updated)


# Keep the public name used by synthesis; the old singleton heuristic was
# withdrawn after it broke <= and >= in the real regression matrix.
singleton_guard_candidates=operator_shape_candidates
