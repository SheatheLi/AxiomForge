"""A conservative cross-statement query-scope repair hypothesis.

When a rule begins at a nested SELECT rather than its DML statement, AST
reconstruction may lose the ancestor's visible target-table bindings. This
operator proposes restoring the parent query and not filtering its target out
as a child subquery alias. Generated candidates remain subject to independent
reproducer and control tests; the tool cannot infer all SQL dialect scoping.
"""
from __future__ import annotations
import ast
from typing import Iterator
from .synthesis import Mutation

_DML = ('update_statement','delete_statement','merge_statement')


def dml_scope_candidates(source: str, *, line_start: int = 1,
                         line_end:int = 100000)->Iterator[Mutation]:
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    query_import=next((n for n in tree.body if isinstance(n,ast.ImportFrom)
                       and n.module and n.module.endswith('analysis.query')
                       and any(a.name=='Query' for a in n.names)),None)
    if query_import is None or query_import.end_lineno!=query_import.lineno: return
    if any(a.name in ('Selectable','QueryType') for a in query_import.names):return
    for cls in (n for n in tree.body if isinstance(n,ast.ClassDef)):
        evaluator=next((m for m in cls.body if isinstance(m,ast.FunctionDef) and m.name=='_eval'),None)
        available=next((m for m in cls.body if isinstance(m,ast.FunctionDef) and m.name=='_iter_available_targets'),None)
        if not evaluator or not available:continue
        if not(line_start<=evaluator.lineno<=line_end or line_start<=evaluator.end_lineno<=line_end):continue
        assignment=next((s for s in ast.walk(evaluator) if isinstance(s,ast.AnnAssign)
            and isinstance(s.target,ast.Name) and s.target.id=='query'
            and isinstance(s.value,ast.Call) and ast.unparse(s.value.func)=='Query.from_segment'),None)
        if assignment is None:continue
        probe=next((s for s in ast.walk(available) if isinstance(s,ast.If)
                    and 'subquery' in ast.unparse(s.test)
                    and '.path_to(' in ast.unparse(s.test)
                    and 'alias.from_expression_element' in ast.unparse(s.test)),None)
        if probe is None or not probe.body or not isinstance(probe.body[0],ast.Continue):continue
        # Do not emit candidates if the target query is already reconstructed.
        if any('query.parent = ' in ast.unparse(s) for s in evaluator.body):continue
        indent=' '*assignment.col_offset
        ancestor_types=', '.join(repr(t) for t in _DML)
        insert=(
            f'{indent}# Restore the visible DML target for correlated SELECTs.\n'
            f'{indent}for ancestor in reversed(context.parent_stack):\n'
            f'{indent}    if ancestor.is_type({ancestor_types}):\n'
            f'{indent}        query.parent = Query(\n'
            f'{indent}            query_type=QueryType.Simple, dialect=context.dialect,\n'
            f'{indent}            selectables=[Selectable(ancestor, dialect=context.dialect)],\n'
            f'{indent}        )\n'
            f'{indent}        query.is_subquery = True\n'
            f'{indent}        break\n'
        )
        # Do not count the DML ancestor itself as a descendant SELECT alias.
        guard_indent=' '*probe.col_offset
        guard=(
            f'{guard_indent}if (\n'
            f'{guard_indent}    subquery\n'
            f'{guard_indent}    and alias.from_expression_element\n'
            f'{guard_indent}    and not alias.from_expression_element.is_type({ancestor_types})\n'
            f'{guard_indent}    and alias.from_expression_element.path_to(\n'
            f'{guard_indent}        subquery.selectables[0].selectable\n'
            f'{guard_indent}    )\n'
            f'{guard_indent}):\n'
        )
        changes=[(query_import.lineno-1,query_import.end_lineno,
                   lines[query_import.lineno-1].rstrip('\n').replace('import Query','import Query, QueryType, Selectable')+'\n'),
                 (assignment.end_lineno,assignment.end_lineno,insert),
                 (probe.lineno-1,probe.body[0].lineno-1,guard)]
        output=lines.copy()
        for start,end,replacement in sorted(changes,reverse=True):output[start:end]=[replacement]
        candidate=''.join(output)
        try:ast.parse(candidate)
        except SyntaxError:continue
        yield Mutation('restore-outer-dml-scope',assignment.lineno,
                       'nested SELECT reconstructed without DML ancestor',
                       'scope visibility and ancestor-aware alias exclusion',candidate)
