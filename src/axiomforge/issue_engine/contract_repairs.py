"""Cross-repository, syntax-local repair hypotheses for three *classes* of bugs.

None-vs-empty: preserve explicit configuration for empty collections.
Numeric boundary: reject zero/negative chunks where a positive default implies a
positive-size I/O invariant (proposed hypothesis, never accepted without tests).
Exception map: derive missing class-name-consistent mappings from peers in an
adapter dictionary. No repository names, Issue identifiers or expected patches.
"""
from __future__ import annotations
import ast
from collections.abc import Iterator
from .synthesis import Mutation, _span, _replace


def contract_candidates(source: str, *, line_start: int = 1,
                        line_end: int = 100000) -> Iterator[Mutation]:
    tree=ast.parse(source)
    seen=set()
    def emit(kind: str, node: ast.AST, start: int, end: int, replacement: str):
        updated=_replace(source,start,end,replacement)
        if updated==source or updated in seen:return None
        try:ast.parse(updated)
        except SyntaxError:return None
        seen.add(updated)
        return Mutation(kind,node.lineno,source[start:end],replacement,updated)

    # Guard an optional container without silently treating [] / {} / set()
    # as the same meaning as the absence of a restriction.
    for node in ast.walk(tree):
        if not isinstance(node,ast.If) or not line_start<=node.lineno<=line_end:continue
        test=node.test
        primary=test.values[0] if isinstance(test,ast.BoolOp) and isinstance(test.op,ast.And) else test
        if not isinstance(primary,(ast.Name,ast.Attribute)):continue
        name=ast.get_source_segment(source,primary)
        if not name or name in {'self','cls'}:continue
        start,stop=_span(source,primary)
        m=emit('optional-container-nonempty-distinction',node,start,stop,f'{name} is not None')
        if m:yield m

    # Infer parameter validation from an integer, strictly positive default.
    # Rejecting an invalid zero-size I/O chunk is safer than silently sending
    # an empty request body; valid positive values stay unchanged.
    for fn in ast.walk(tree):
        if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)) or not line_start<=fn.lineno<=line_end:continue
        positional=list(zip(fn.args.args[-len(fn.args.defaults):],fn.args.defaults)) if fn.args.defaults else []
        keyword_only=[(p,d) for p,d in zip(fn.args.kwonlyargs,fn.args.kw_defaults) if d is not None]
        for param,default in positional+keyword_only:
            if not (isinstance(default,ast.Constant) and type(default.value) is int and default.value>0):continue
            if not any(token in param.arg.lower() for token in ('blocksize','chunk_size','buffer_size','batch_size')):continue
            # Insert before first executable statement, preserving decorator/docstring.
            statements=fn.body
            start_index=1 if statements and isinstance(statements[0],ast.Expr) and isinstance(statements[0].value,ast.Constant) and isinstance(statements[0].value.value,str) else 0
            if start_index>=len(statements):continue
            anchor=statements[start_index]
            at=_span(source,anchor)[0]
            indent=source[source.rfind('\n',0,at)+1:at]
            if indent.strip():continue
            text=(f'if {param.arg} <= 0:\n{indent}    raise ValueError("{param.arg} must be positive")\n{indent}')
            m=emit('positive-buffer-contract',fn,at,at,text)
            if m:yield m

    # Adapt a class-based foreign error hierarchy by borrowing dictionary
    # mapping idioms from existing entries. This is restricted to adapter maps
    # with matching .exceptions.XError: self.XError form; symbol definitions
    # must exist in the same source file. Generated accesses are tested by the
    # *real* vendor API, not statically assumed to exist.
    local_classes={n.name for n in ast.walk(tree) if isinstance(n,ast.ClassDef)}
    for node in ast.walk(tree):
        if not isinstance(node,ast.Dict) or not line_start<=node.lineno<=line_end:continue
        entries=[]
        for key,val in zip(node.keys,node.values):
            if (isinstance(key,ast.Attribute) and isinstance(val,ast.Attribute)
                and isinstance(val.value,ast.Name) and val.value.id=='self'
                and isinstance(key.value,ast.Attribute) and key.value.attr=='exceptions'):
                entries.append((key,val))
        if len(entries)<3:continue
        prefix=ast.get_source_segment(source,entries[0][0].value)
        if not prefix or not all(ast.get_source_segment(source,k.value)==prefix for k,_ in entries):continue
        known={v.attr for _,v in entries}
        last=_span(source,entries[-1][1])[1]
        closing=_span(source,node)[1]-1
        trailing=source[last:closing]
        if ',' not in trailing or '\n' not in trailing:continue
        at=source.rfind('\n',last,closing)+1
        entry_key_start=_span(source,entries[-1][0])[0]
        indentation=source[source.rfind('\n',0,entry_key_start)+1:entry_key_start]
        # Candidate must not assume every exception exists in the remote API.
        # Emit one candidate per missing locally declared class.
        names=sorted(c for c in local_classes if c.endswith('Error') and c not in known)
        for clsname in names:
            proposed=f'{indentation}{prefix}.{clsname}: self.{clsname},\n'
            m=emit('peer-exception-map-completion',node,at,at,proposed)
            if m:yield m
