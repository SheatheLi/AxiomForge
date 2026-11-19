"""Conservative async resource-lifecycle repair operator.

Targets a narrow but important failure pattern: closing a pooled asynchronous
stream marks CLOSED before awaited cleanup and the pool detachment.  Uses a
backend-neutral lock already available in the project and a restartable
cleanup phase. This is a *proposal* and still requires executable controls.
"""
from __future__ import annotations
import ast
from typing import Iterator
from .synthesis import Mutation


def two_phase_close_candidates(source: str, *, line_start: int = 1,
                               line_end: int = 100000) -> Iterator[Mutation]:
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    for cls in (n for n in ast.walk(tree) if isinstance(n,ast.ClassDef)):
        init=next((m for m in cls.body if isinstance(m,ast.FunctionDef) and m.name=='__init__'),None)
        close=next((m for m in cls.body if isinstance(m,ast.AsyncFunctionDef) and m.name=='aclose'),None)
        if not init or not close or not line_start<=close.lineno<=line_end:continue
        if len(close.body)!=1 or not isinstance(close.body[0],ast.If):continue
        check=close.body[0]
        if (ast.unparse(check.test)!='not self._closed' or len(check.body)<4
            or ast.unparse(check.body[0])!='self._closed = True'):continue
        # Verify the actual program has all phases, rather than rewriting a
        # coincidentally similar method with different ownership semantics.
        shield,pool_lock,finish=check.body[1:4]
        if not (isinstance(shield,ast.With) and isinstance(pool_lock,ast.With)
            and isinstance(finish,ast.Expr) and isinstance(finish.value,ast.Await)):
            continue
        if not ('AsyncShieldCancellation' in ast.unparse(shield.items[0].context_expr)
            and '_optional_thread_lock' in ast.unparse(pool_lock.items[0].context_expr)):
            continue
        removal=next((s for s in pool_lock.body if isinstance(s,ast.Expr)
                      and isinstance(s.value,ast.Call)
                      and s.value.func.__class__ is ast.Attribute
                      and s.value.func.attr=='remove'),None)
        assignment=next((s for s in pool_lock.body if isinstance(s,ast.Assign)
                         and len(s.targets)==1 and isinstance(s.targets[0],ast.Name)),None)
        if not removal or not assignment:continue
        if not any(isinstance(s,ast.Expr) and isinstance(s.value,ast.Await)
                   for s in ast.walk(shield)):continue
        pending_name=assignment.targets[0].id
        close_call=finish.value.value
        if not isinstance(close_call,ast.Call) or len(close_call.args)!=1:continue
        if ast.unparse(close_call.args[0])!=pending_name:continue
        if 'close' not in ast.unparse(close_call.func):continue
        # Use the project's own backend-independent synchronization primitive.
        sync_import=next((s for s in tree.body if isinstance(s,ast.ImportFrom)
                          and s.module and s.module.endswith('_synchronization')
                          and any(a.name=='AsyncShieldCancellation' for a in s.names)),None)
        if not sync_import:continue
        if not any(isinstance(s,ast.Assign) and any(ast.unparse(t)=='self._closed' for t in s.targets)
                   and isinstance(s.value,ast.Constant) and s.value.value is False
                   for s in init.body):continue
        if '_close_lock' in source or '_close_stage' in source:continue
        indent=' '*(close.col_offset+4)
        inner=indent+'    '
        more=inner+'    '
        deeper=more+'    '
        # Generate from existing syntactic regions, preserving underlying
        # stream, pool lock and reassignment expressions rather than guessing.
        shield_block=''.join(lines[shield.lineno-1:shield.end_lineno])
        lock_block=''.join(lines[pool_lock.lineno-1:pool_lock.end_lineno])
        def shift(block:str,shift:int)->str:
            return ''.join((' '*shift+line if line.strip() else line)
                           for line in block.splitlines(keepends=True))
        # original two blocks start at If.body level. Reindent one extra level
        # because they are now nested in _close_stage==0.
        repaired_method=(
            f'{indent}with AsyncShieldCancellation():\n'
            f'{inner}async with self._close_lock:\n'
            f'{more}if self._closed:\n'
            f'{deeper}return\n'
            f'{more}if self._close_stage == 0:\n'
            +shift(shield_block,8)
            +shift(lock_block,8)
            +f'{deeper}self._pending_connections = {pending_name}\n'
            +f'{deeper}self._close_stage = 1\n'
            +f'{more}while self._pending_connections:\n'
            +f'{deeper}await {ast.unparse(close_call.func)}(self._pending_connections[:1])\n'
            +f'{deeper}del self._pending_connections[0]\n'
            +f'{more}self._closed = True\n'
        )
        # AST positions measured against original source; apply all edits from
        # bottom to top. Import and __init__ modifications are monotone.
        changes=[(check.lineno-1,check.end_lineno,repaired_method)]
        closed_init=next(s for s in init.body if isinstance(s,ast.Assign)
                         and any(ast.unparse(t)=='self._closed' for t in s.targets))
        changes.append((closed_init.end_lineno,closed_init.end_lineno,
            ' '* (closed_init.col_offset) + 'self._close_lock = AsyncLock()\n'
            +' '*closed_init.col_offset + 'self._close_stage = 0\n'
            +' '*closed_init.col_offset + 'self._pending_connections = []\n'))
        import_line=lines[sync_import.lineno-1]
        if 'AsyncLock' not in [a.name for a in sync_import.names]:
            changes.append((sync_import.lineno-1,sync_import.end_lineno,
                    import_line.rstrip('\n').replace('AsyncEvent,','AsyncEvent, AsyncLock,')+'\n'))
        output=lines.copy()
        for begin,end,value in sorted(changes,reverse=True):
            output[begin:end]=[value]
        result=''.join(output)
        try:ast.parse(result)
        except SyntaxError:continue
        yield Mutation('restartable-async-pool-close',close.lineno,
                       'CLOSED state committed before awaited cleanup',
                       'transactional close phases and backend-neutral locking',result)
