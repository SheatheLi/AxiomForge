"""Effect-aware repair operator for consumptive lookups in parent-linked scopes.

Infers the state mapping, lookup key, and parent link from *callee source*.
Synthesizes borrow/restore around a caller's dependent region. This operator
is deliberately narrow and fails closed unless all structural preconditions
are proven from Python AST, then must pass the executable issue contract.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from .core import IssueContract, IssueError
from .synthesis import Mutation

@dataclass(frozen=True)
class Effect:
    method: str
    key_parameter: str
    dictionary: str
    parent: str
    deleted_key: ast.expr


def _effect(method: ast.FunctionDef) -> Effect | None:
    mappings=[];parents=[]
    for node in ast.walk(method):
        if isinstance(node,ast.Delete):
            for target in node.targets:
                if (isinstance(target,ast.Subscript)
                    and isinstance(target.value,ast.Attribute)
                    and isinstance(target.value.value,ast.Name)
                    and target.value.value.id=='self'):
                    mappings.append((target.value.attr,target.slice))
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            f=node.func
            if (f.attr==method.name and isinstance(f.value,ast.Attribute)
                and isinstance(f.value.value,ast.Name) and f.value.value.id=='self'):
                parents.append(f.value.attr)
    if len(mappings)!=1 or len(set(parents))!=1 or not parents:
        return None
    pos=method.args.posonlyargs+method.args.args
    if len(pos)<2 or pos[0].arg!='self':return None
    key_name=pos[1].arg
    if not any(isinstance(x,ast.Name) and x.id==key_name for x in ast.walk(mappings[0][1])):
        return None
    return Effect(method.name,key_name,mappings[0][0],parents[0],mappings[0][1])


def _find_effect(contract:IssueContract, methodname:str) -> Effect | None:
    # Analyze only existing production Python source, without importing or
    # executing any module in the checkout while discovering the effect.
    answers=[]
    for p in contract.checkout.rglob('*.py'):
        if any(x in {'.git','test','tests','venv','.venv','__pycache__'} for x in p.parts):continue
        try:
            if p.stat().st_size>300000:continue
            code=p.read_text(encoding='utf8')
            if f'def {methodname}(' not in code:continue
            tree=ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node,ast.FunctionDef) and node.name==methodname:
                    effect=_effect(node)
                    if effect:answers.append(effect)
        except (OSError,UnicodeError,SyntaxError):continue
    if len(answers)!=1:return None
    return answers[0]


class _ReplaceKey(ast.NodeTransformer):
    def __init__(self,param:str,value:ast.expr):self.param=param;self.value=value
    def visit_Name(self,node:ast.Name):
        if node.id==self.param:
            return self.value
        return node


def borrowed_lookup_candidates(contract:IssueContract,source:str,path:str,*,
                              line_start:int=1,line_end:int=100000) -> Iterator[Mutation]:
    tree=ast.parse(source)
    lines=source.splitlines(keepends=True)
    candidates=[]
    # Only a consumptive lookup whose result controls the *next* block is
    # eligible. This avoids rewriting unrelated calls or unknown scopes.
    for node in ast.walk(tree):
        body=getattr(node,'body',None)
        if not isinstance(body,list):continue
        for old,next_node in zip(body,body[1:]):
            if not(isinstance(old,ast.Assign) and len(old.targets)==1
                   and isinstance(old.targets[0],ast.Name)
                   and isinstance(old.value,ast.Call)
                   and isinstance(old.value.func,ast.Attribute)
                   and isinstance(next_node,ast.If)
                   and isinstance(next_node.test,ast.Name)
                   and next_node.test.id==old.targets[0].id):continue
            if not line_start<=old.lineno<=line_end:continue
            caller=old.value.func.value
            if not isinstance(caller,ast.Name) or len(old.value.args)!=1:continue
            effect=_find_effect(contract,old.value.func.attr)
            if not effect:continue
            key=ast.fix_missing_locations(_ReplaceKey(effect.key_parameter,old.value.args[0]).visit(
                ast.parse(ast.unparse(effect.deleted_key),mode='eval').body))
            key_expr=ast.unparse(key)
            indent=' '*old.col_offset
            inner=' '*(old.col_offset+4)
            detail=' '*(old.col_offset+8)
            owner_var='_cte_scope_owner'
            if owner_var in source:continue
            owner=ast.unparse(caller)
            prefix=''.join([
                f'{indent}{owner_var} = {owner}\n',
                f'{indent}while {owner_var} is not None and {key_expr} not in {owner_var}.{effect.dictionary}:\n',
                f'{inner}{owner_var} = {owner_var}.{effect.parent}\n',
            ])
            begin=next_node.body[0].lineno-1
            finish=next_node.body[-1].end_lineno
            if begin<=old.lineno-1 or finish<=begin:continue
            original=lines[begin:finish]
            if not all(not x.strip() or x.startswith(inner) for x in original):continue
            region=(f'{inner}try:\n'+''.join('    '+s if s.strip() else s for s in original)+
                f'{inner}finally:\n'
                f'{detail}if {owner_var} is not None:\n'
                f'{detail}    {owner_var}.{effect.dictionary}[{key_expr}] = {next_node.test.id}\n')
            updated=lines.copy()
            updated[begin:finish]=[region]
            updated[old.lineno-1:old.lineno-1]=[prefix]
            repaired=''.join(updated)
            try:ast.parse(repaired)
            except SyntaxError:continue
            yield Mutation('borrow-restore-consumptive-lookup',old.lineno,
                           f'{effect.method} mutates {effect.dictionary}',
                           f'scoped restoration through {effect.parent}',repaired)
