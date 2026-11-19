"""Parse a SMALL, auditable Python expression subset into a typed relational IR.

The parser is pure AST inspection: it does not execute arbitrary expressions.
If a query is not in the proven subset, it fails closed. No general claims
about pandas Python semantics follow from a successful parse.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass, asdict
import hashlib, json

class CompileError(ValueError):
    pass

@dataclass(frozen=True)
class Plan:
    data_frame: str
    operation: str  # sum | count
    value_column: str
    group_columns: tuple[str, ...]
    dropna: bool

    def serial(self):
        result=asdict(self)
        result["group_columns"] = list(self.group_columns)
        return result

@dataclass(frozen=True)
class ProgramPair:
    left: Plan
    right: Plan
    left_source: str
    right_source: str

    def serial(self):
        return {"left":self.left.serial(),"right":self.right.serial(),
                "left_source":self.left_source,"right_source":self.right_source}

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.serial(),sort_keys=True).encode()).hexdigest()


def _member(node, name):
    return isinstance(node,ast.Attribute) and node.attr==name

def _call(node, name):
    return isinstance(node,ast.Call) and _member(node.func,name) and not node.args and not node.keywords

def _column(node):
    if not isinstance(node,ast.Subscript) or not isinstance(node.slice,ast.Constant) or type(node.slice.value)!=str:
        raise CompileError("Only dataframe['constant_column'] is supported")
    return node.value,node.slice.value

def _source(text:str, side:str)->Plan:
    try:
        tree=ast.parse(text,mode='eval').body
    except SyntaxError as exc:
        raise CompileError("expression parse failure") from exc
    if side=='left':
        if not (_call(tree,'sum') or _call(tree,'count')):
            raise CompileError('left side must be df["value"].sum() or .count()')
        op=tree.func.attr
        base,col=_column(tree.func.value)
        if not isinstance(base,ast.Name):
            raise CompileError('left side must use a named frame')
        return Plan(base.id,op,col,(),False)
    if not _call(tree,'sum'):
        raise CompileError('right side must sum per-group aggregate results')
    aggregate=tree.func.value
    if not (_call(aggregate,'sum') or _call(aggregate,'count')):
        raise CompileError('only additive grouped SUM or COUNT can be certified')
    op=aggregate.func.attr
    grouped,col=_column(aggregate.func.value)
    if not isinstance(grouped,ast.Call) or not _member(grouped.func,'groupby'):
        raise CompileError('must apply groupby to a known frame')
    if not isinstance(grouped.func.value,ast.Name):
        raise CompileError('groupby source is not a named frame')
    if len(grouped.args)!=1:
        raise CompileError('groupby requires exactly one positional key argument')
    arg=grouped.args[0]
    if isinstance(arg,ast.Constant) and type(arg.value)==str:
        keys=(arg.value,)
    elif isinstance(arg,ast.List) and 1<=len(arg.elts)<=4 and all(isinstance(x,ast.Constant) and type(x.value)==str for x in arg.elts):
        keys=tuple(x.value for x in arg.elts)
    else:
        raise CompileError('grouping keys must be a string or literal list of strings')
    if len(set(keys))!=len(keys):
        raise CompileError('group keys must be distinct')
    if len(grouped.keywords)!=1 or grouped.keywords[0].arg!='dropna':
        raise CompileError('explicit dropna boolean required; all other groupby options disallowed')
    setting=grouped.keywords[0].value
    if not isinstance(setting,ast.Constant) or type(setting.value)!=bool:
        raise CompileError('dropna must be a static boolean')
    return Plan(grouped.func.value.id,op,col,keys,setting.value)

def compile_pandas_pair(left:str,right:str)->ProgramPair:
    a=_source(left,'left');b=_source(right,'right')
    if a.data_frame!=b.data_frame or a.operation!=b.operation or a.value_column!=b.value_column:
        raise CompileError('same frame, aggregate type and measure column required')
    if not b.group_columns:
        raise CompileError('grouping keys required')
    if a.value_column in b.group_columns:
        raise CompileError('measure/group aliasing unsupported')
    return ProgramPair(a,b,left,right)
