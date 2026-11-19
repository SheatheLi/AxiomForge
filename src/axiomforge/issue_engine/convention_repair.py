"""Repository-local idiom transfer for normalization-sensitive references.

This repairs a *class* of inconsistent identifier-handling expressions by
borrowing normalization conventions already present in the production file.
It does not embed repository names, issue identifiers or expected patch bytes.
Every proposal is merely a candidate until an executable issue contract passes.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator

from .synthesis import Mutation, _span, _replace


def convention_candidates(source: str, *, line_start: int = 1,
                          line_end: int = 100000) -> Iterator[Mutation]:
    tree = ast.parse(source)
    # Never invent an API absent from the repository's own module.
    local_idioms = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {'raw_normalized', 'normalize', 'casefold', 'lower', 'upper'}
    }
    if not local_idioms:
        return
    yielded: set[str] = set()
    # Preserve existing raw aliases, adding the dialect canonical form only
    # for unquoted identifiers. Replacing the raw spelling outright is unsafe
    # because SQL dialects may use both spellings or preserve quoted casing.
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.Expr) or not line_start <= getattr(statement,'lineno',0) <= line_end:
            continue
        call=statement.value
        if not (isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute)
                and call.func.attr == 'append' and len(call.args)==1
                and isinstance(call.args[0],ast.Tuple) and len(call.args[0].elts)==1):
            continue
        reference=call.args[0].elts[0]
        if not (isinstance(reference,ast.Attribute) and reference.attr == 'ref_str'
                and 'normalize' in local_idioms):
            continue
        owner=ast.get_source_segment(source,reference.value)
        receiver=ast.get_source_segment(source,call.func.value)
        if not owner or not receiver:
            continue
        start,stop=_span(source,statement)
        indent=source[source.rfind('\n',0,start)+1:start]
        if indent.strip():
            continue
        extra=(f'\n{indent}if {owner}.segment and {owner}.segment.is_type("naked_identifier"):'
               f'\n{indent}    {receiver}.append(({owner}.ref_str.upper(),))')
        updated=_replace(source,stop,stop,extra)
        try:ast.parse(updated)
        except SyntaxError:continue
        yielded.add(updated)
        yield Mutation('peer-canonical-alias-alternative',statement.lineno,
                       source[start:stop], extra.strip(), updated)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not line_start <= getattr(node, 'lineno', 0) <= line_end:
            continue
        if node.attr not in {'ref_str', 'raw'}:
            continue
        start, stop = _span(source, node)
        fragment = source[start:stop]
        variants = []
        if node.attr == 'ref_str' and 'raw_normalized' in local_idioms:
            owner = ast.get_source_segment(source, node.value)
            # Transfer an existing element-normalization API as an alternative
            # to an unnormalized alias key. Candidate testing checks whether
            # the owner really has the proposed segment and expected semantics.
            if owner:
                variants.append((f'{owner}.segment.raw_normalized(False)', 'peer-segment-normalization'))
        if 'casefold' in local_idioms:
            variants.append((f'({fragment}).casefold()', 'peer-casefold'))
        if 'upper' in local_idioms:
            variants.append((f'({fragment}).upper()', 'peer-upper-normalization'))
        if 'lower' in local_idioms:
            variants.append((f'({fragment}).lower()', 'peer-lower-normalization'))
        if node.attr == 'raw' and 'raw_normalized' in local_idioms:
            variants.append((fragment[:-3] + '.raw_normalized(False)', 'peer-token-normalization'))
        for proposed, kind in variants:
            updated = _replace(source, start, stop, proposed)
            if updated in yielded or updated == source:
                continue
            try:
                ast.parse(updated)
            except SyntaxError:
                continue
            yielded.add(updated)
            yield Mutation(kind, node.lineno, fragment, proposed, updated)
