"""Borrow a repository-local stream-position contract for recursive retries.

For a recursive request method forwarding **kwargs, search the same package
for an established position-recording/rewind helper. If found, propose reusing
that helper on entry before any I/O. No issue ID or expected output embedded.
A candidate must pass original/redirect/control tests to survive.
"""
from __future__ import annotations
import ast
from collections.abc import Iterator
from pathlib import Path
from .synthesis import Mutation, _span, _replace


def replay_candidates(source: str, relative_file: str, checkout: Path, *,
                      line_start:int=1,line_end:int=100000) -> Iterator[Mutation]:
    # Search for the conventional helper in an actual peer module, not in a
    # private injected solution. This donor code must exist in the checkout.
    base=checkout/relative_file
    module_dir=base.parent
    peer=module_dir/'util'/'request.py'
    if not peer.is_file() or not peer.resolve().is_relative_to(checkout.resolve()):return
    peer_source=peer.read_text('utf8')
    if not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='set_file_position'
               for n in ast.walk(ast.parse(peer_source))):return
    tree=ast.parse(source)
    for fn in ast.walk(tree):
        if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)) or not line_start<=fn.lineno<=line_end:continue
        keyword=fn.args.kwarg.arg if fn.args.kwarg else None
        if not keyword:continue
        # A recursive request must pass the same **kwargs to itself and a
        # subordinate handler. If it doesn't, this operator is inapplicable.
        matching_calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
            and n.func.attr==fn.name and isinstance(n.func.value,ast.Name) and n.func.value.id=='self'
            and any(k.arg is None and isinstance(k.value,ast.Name) and k.value.id==keyword for k in n.keywords)]
        if not matching_calls:continue
        if not any(isinstance(n,ast.Subscript) and isinstance(n.value,ast.Name) and n.value.id==keyword
                   and isinstance(n.slice,ast.Constant) and n.slice.value=='body' for n in ast.walk(fn)):
            continue
        # Entering the method on the first hop records stream position;
        # entering it again after a redirect rewinds before forwarding.
        statements=fn.body
        index=1 if statements and isinstance(statements[0],ast.Expr) and isinstance(statements[0].value,ast.Constant) and isinstance(statements[0].value.value,str) else 0
        if index==len(statements):continue
        start=_span(source,statements[index])[0]
        indent=source[source.rfind('\n',0,start)+1:start]
        if indent.strip():continue
        assignment=(f'{keyword}["body_pos"] = set_file_position('
                    f'{keyword}.get("body"), {keyword}.get("body_pos"))\n{indent}')
        amended=_replace(source,start,start,assignment)
        import_line='from .util.request import set_file_position\n'
        if import_line not in amended:
            # Insert immediately before another relative import, after module
            # header/docstring/future imports, preserving PEP 8 import order.
            anchor=next((n for n in tree.body if isinstance(n,ast.ImportFrom) and n.level==1
                         and n.module and n.module.startswith('util.')
                         and n.module > 'util.request'),None)
            if anchor is None:continue
            offset=_span(source,anchor)[0]
            amended=_replace(amended,offset,offset,import_line)
        try:ast.parse(amended)
        except SyntaxError:continue
        yield Mutation('repository-peer-replay-position',fn.lineno,
                       'recursive kwargs body stream', 'borrow peer set_file_position',amended)
