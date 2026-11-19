"""Semantics-aware, budget-checked formatting candidate generation.

When an auto-fixer joins a previously split single expression onto its first
line, independently bound the resulting line length. This avoids rules with
opposing constraints creating an oscillating rewrite. Matches the structural
rewrite action and derives expressions from the AST rather than issue numbers.
"""
from __future__ import annotations
import ast
from typing import Iterator
from .synthesis import Mutation


def bounded_rejoin_candidates(source: str, *, line_start:int=1,
                              line_end:int=100000)->Iterator[Mutation]:
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    for m in (node for node in ast.walk(tree) if isinstance(node,ast.FunctionDef)):
        if not(m.lineno<=line_end and m.end_lineno>=line_start):continue
        text=ast.unparse(m)
        if not ('WhitespaceSegment()' in text and 'insert_buff' in text
                and 'target_seg' in text and 'select_clause' in text):continue
        if 'max_line_length' in text:continue
        # At the last safe boundary before rewrite, a method already declines
        # fixes when comments make moving the expression unsafe.
        candidates=[node for node in ast.walk(m) if isinstance(node,ast.If)
                    and 'comment_after_select_idx' in ast.unparse(node.test)
                    and node.body and any(isinstance(t,ast.Return) for t in node.body)]
        if len(candidates)!=1:continue
        guard=candidates[0]
        if not line_start<=guard.lineno<=line_end:continue
        if not any(isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='target_seg' for t in node.targets)
                   for node in ast.walk(m)):continue
        # Respect existing indentation and preserve the rest of the file.
        space=' '*guard.col_offset
        proposal=(
            f'{space}# Do not recreate a line that the long-line rule must split.\n'
            f'{space}max_line_length = context.config.get("max_line_length")\n'
            f'{space}if max_line_length and max_line_length > 0:\n'
            f'{space}    first_line = select_clause.get().raw.split("\\n", 1)[0].rstrip()\n'
            f'{space}    proposed_line_length = (\n'
            f'{space}        context.segment.pos_marker.working_line_pos - 1\n'
            f'{space}        + len(first_line) + 1 + len(target_seg.raw)\n'
            f'{space}    )\n'
            f'{space}    if proposed_line_length > max_line_length:\n'
            f'{space}        return None\n\n'
        )
        updated=''.join(lines[:guard.lineno-1]+[proposal]+lines[guard.lineno-1:])
        try:ast.parse(updated)
        except SyntaxError:continue
        yield Mutation('bounded-layout-rejoin',guard.lineno,
                       'layout rewrite may violate configured line budget',
                       'guard rejoin before proposing autofix',updated)
