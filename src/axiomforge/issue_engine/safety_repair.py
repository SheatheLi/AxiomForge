"""Source-preserving, test-gated guard against destructive segment deletion.

Searches AST for an auto-fix branch deleting a composite syntax segment. If
that segment can contain comments, propose refusing the destructive fix while
continuing to report the violation. The rule-specific failure and no-comment
control must independently pass or the proposal is rejected.
"""
from __future__ import annotations
import ast
from typing import Iterator
from .synthesis import Mutation


def comment_preservation_candidates(source: str, *, line_start: int = 1,
                                    line_end: int = 100000) -> Iterator[Mutation]:
    lines=source.splitlines(keepends=True)
    tree=ast.parse(source)
    for rule in (n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)):
        if rule.end_lineno < line_start or rule.lineno > line_end:
            continue
        if 'context' not in [a.arg for a in rule.args.args]:continue
        for node in ast.walk(rule):
            if not isinstance(node,ast.If) or not node.body or node.orelse:continue
            if not line_start <= node.lineno <= line_end:continue
            # Only generate when the branch unconditionally returns a LintResult
            # with a delete edit. Dynamic fixes and unrelated returns abstain.
            returns=[r for s in node.body for r in ast.walk(s) if isinstance(r,ast.Return)]
            if len(returns)!=1 or not isinstance(returns[0].value,ast.Call):continue
            if ast.unparse(returns[0].value.func)!='LintResult':continue
            delete_calls=[]
            for n in ast.walk(returns[0].value):
                if isinstance(n,ast.Call) and ast.unparse(n.func)=='LintFix.delete' and len(n.args)==1 and isinstance(n.args[0],ast.Subscript):
                    delete_calls.append(n)
            if len(delete_calls)!=1:continue
            target=delete_calls[0].args[0]
            # A concrete syntax subtree (indexed collection) is eligible;
            # individual whitespace tokens are handled separately.
            if not isinstance(target,ast.Subscript):continue
            target_expr=ast.unparse(target)
            if any('recursive_crawl("comment")' in ast.unparse(s) for s in node.body):continue
            anchor=next((k.value for k in returns[0].value.keywords if k.arg=='anchor'),None)
            if anchor is None:continue
            anchor_expr=ast.unparse(anchor)
            first=node.body[0]
            indentation=' '*first.col_offset
            guard=(f'{indentation}if any({target_expr}.recursive_crawl("comment")):\n'
                   f'{indentation}    return LintResult(anchor={anchor_expr})\n\n')
            patched=lines[:first.lineno-1]+[guard]+lines[first.lineno-1:]
            candidate=''.join(patched)
            try:ast.parse(candidate)
            except SyntaxError:continue
            yield Mutation('preserve-comments-in-composite-delete',node.lineno,
                           f'unconditional deletion of {target_expr}',
                           'reject destructive autofix when comments are nested',candidate)
