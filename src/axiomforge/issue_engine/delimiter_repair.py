"""Bounded synthesis for delimited records with bracketed separators.

This family recognizes a field-splitting expression with a separator that may
also occur inside a bracket-delimited token. It emits three auditable variants:
rightmost split and bracket-aware split. The repair
engine's executable contracts decide whether any candidate is admissible.

No repository, issue identifier, function name, or patch bytes are embedded.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator

from .synthesis import Mutation, _span, _replace


def delimiter_candidates(source: str, *, line_start: int = 1,
                         line_end: int = 100000) -> Iterator[Mutation]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not line_start <= getattr(node, 'lineno', 0) <= line_end:
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == 'split'
                and len(node.args) == 1 and not node.keywords):
            continue
        separator = node.args[0]
        if not (isinstance(separator, ast.Constant) and isinstance(separator.value, str)
                and len(separator.value) == 1 and separator.value in ':,;|'):
            continue
        receiver = ast.get_source_segment(source, node.func.value)
        if not receiver or not isinstance(node.func.value, (ast.Name, ast.Attribute, ast.Subscript)):
            continue
        begin, end = _span(source, node)
        fragment = source[begin:end]
        literal = repr(separator.value)
        # First-record partition when the value may contain more delimiters.
        yield Mutation('bounded-first-delimiter', node.lineno, fragment,
                       f'{receiver}.split({literal}, 1)',
                       _replace(source, begin, end, f'{receiver}.split({literal}, 1)'))
        # Move the separator choice from first occurrence to last occurrence.
        yield Mutation('rightmost-delimiter', node.lineno, fragment,
                       f'{receiver}.rsplit({literal}, 1)',
                       _replace(source, begin, end, f'{receiver}.rsplit({literal}, 1)'))
        # The bounded bracketed-field case is a reusable structure common to
        # URI authorities, structured log keys, and address:port fields.
        # Do not allow arbitrary code in the generated lambda: receiver is AST.
        name = '__axiom_field'
        condition = (f'{name}.startswith("[") and '+
                     f'({name}.endswith("]") or "]"+{literal} in {name})')
        bracket = (f'({name}[1:{name}.index("]")], '
                   f'{name}[{name}.index("]")+1:].removeprefix({literal}) or None)')
        # An admissible patch must preserve ordinary token semantics.
        plain = f'{name}.split({literal})'
        expression = f'(lambda {name}: {bracket} if {condition} else {plain})({receiver})'
        try:
            ast.parse(expression, mode='eval')
        except SyntaxError:
            continue
        yield Mutation('bracket-delimiter-aware', node.lineno, fragment,
                       expression, _replace(source, begin, end, expression))
