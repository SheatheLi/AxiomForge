"""Bounded, semantics-tested Python repair search, NOT an LLM wrapper.

Generates local, source-preserving AST mutations, then executes the same
cross-project issue contract used for hand-authored patches. A candidate only
survives if the real baseline fails with its specific fingerprint, patched
behavior passes repeatedly and independent control tests remain green.

This is a bounded operator catalog, not a claim of arbitrary program repair.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
import difflib
import hashlib
from pathlib import Path
import tempfile
import time
from typing import Iterator

from .core import IssueContract, IssueError, _relative, CandidateEvaluator

@dataclass(frozen=True)
class Mutation:
    kind: str
    line: int
    original: str
    replacement: str
    source: str


def _position(source: str, row: int, byte_col: int) -> int:
    lines = source.splitlines(keepends=True)
    if not 1 <= row <= len(lines):
        raise IssueError('AST position out of range')
    prefix = lines[row-1].encode('utf8')[:byte_col]
    return sum(len(x) for x in lines[:row-1]) + len(prefix.decode('utf8'))


def _span(source: str, node: ast.AST) -> tuple[int, int]:
    return (_position(source, node.lineno, node.col_offset),
            _position(source, node.end_lineno, node.end_col_offset))


def _replace(source: str, begin: int, end: int, content: str) -> str:
    return source[:begin] + content + source[end:]


def candidates(source: str, *, line_start: int = 1, line_end: int = 100000,
               named_boolean_knobs: tuple[str, ...] = ('pop', 'strict', 'recursive',
                    'required', 'validate', 'allow_recurse', 'fixable')) -> Iterator[Mutation]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not getattr(node,'lineno',None) or not line_start <= node.lineno <= line_end:
            continue
        if isinstance(node, ast.Compare) and len(node.ops)==1:
            operations = {ast.Eq:['!='],ast.NotEq:['=='],ast.Lt:['<=','>'],
                ast.LtE:['<','>='],ast.Gt:['>=','<'],ast.GtE:['>','<='],
                ast.Is:['is not'],ast.IsNot:['is']}
            old = node.ops[0]
            options = next((v for k,v in operations.items() if type(old) is k), [])
            if options:
                left_end=_span(source,node.left)[1]
                right_start=_span(source,node.comparators[0])[0]
                middle=source[left_end:right_start]
                for text in options:
                    updated = _replace(source,left_end,right_start,' '+text+' ')
                    yield Mutation('comparison',node.lineno,middle,text,updated)
        elif isinstance(node, ast.BinOp):
            old=next((t for t in (ast.Add,ast.Sub,ast.Mult,ast.FloorDiv)
                      if type(node.op) is t),None)
            operators={ast.Add:['-'],ast.Sub:['+'],ast.Mult:['+'],ast.FloorDiv:['*']}
            if old:
                left_end=_span(source,node.left)[1]
                right_start=_span(source,node.right)[0]
                middle=source[left_end:right_start]
                for text in operators[old]:
                    new=_replace(source,left_end,right_start,' '+text+' ')
                    yield Mutation('arithmetic',node.lineno,middle,text,new)
        elif isinstance(node, ast.Constant) and type(node.value) is bool:
            start,end=_span(source,node)
            updated=_replace(source,start,end,'False' if node.value else 'True')
            yield Mutation('bool-toggle',node.lineno,source[start:end],
                           'False' if node.value else 'True',updated)
        elif isinstance(node, ast.Call):
            existing={key.arg for key in node.keywords if key.arg}
            for kw in node.keywords:
                if kw.arg in named_boolean_knobs and isinstance(kw.value,ast.Constant):
                    if type(kw.value.value) is bool:
                        start,end=_span(source,kw.value)
                        updated=_replace(source,start,end,'False' if kw.value.value else 'True')
                        yield Mutation('keyword-toggle',node.lineno,source[start:end],
                                       'False' if kw.value.value else 'True',updated)
            # The call already exists; synthesize a missing boolean switch.
            # Explicitly restrict this hypothesis to named, reviewable knobs.
            if not any(k.arg is None for k in node.keywords):
                end=_span(source,node)[1]
                args=(node.args or node.keywords)
                for knob in named_boolean_knobs:
                    if knob in existing:
                        continue
                    for val in ('False','True'):
                        insert=f'{", " if args else ""}{knob}={val}'
                        updated=_replace(source,end-1,end-1,insert)
                        yield Mutation('keyword-insertion',node.lineno,'',insert,updated)


def to_patch(path: str, original: str, updated: str) -> str:
    normalized=str(_relative(path))
    diff=''.join(difflib.unified_diff(original.splitlines(keepends=True),
        updated.splitlines(keepends=True),fromfile='a/'+normalized,tofile='b/'+normalized,n=3))
    if not diff:
        return ''
    return f'diff --git a/{normalized} b/{normalized}\n'+diff



def compose_nonoverlapping(original: str, left: str, right: str) -> str | None:
    """Compose independent line-preserving source edits from ONE original base.

    Never compose overlapping hunks, including simultaneous insertions. This
    is deliberately conservative: composite candidates must not silently
    overwrite a competing hypothesis, nor change unrelated source locations.
    """
    lines = original.splitlines(keepends=True)
    def differences(updated: str) -> list[tuple[int, int, list[str]]]:
        other = updated.splitlines(keepends=True)
        matcher = difflib.SequenceMatcher(None, lines, other)
        return [(i1, i2, other[j1:j2]) for op, i1, i2, j1, j2 in matcher.get_opcodes()
                if op != 'equal']
    changes = differences(left) + differences(right)
    if not changes:
        return None
    changes.sort(key=lambda op: (op[0], op[1]))
    for previous, following in zip(changes, changes[1:]):
        # A boundary insertion that touches another replacement is ambiguous.
        if previous[1] > following[0] or (
            previous[1] == following[0]
            and (previous[0] == previous[1] or following[0] == following[1])
        ):
            return None
    output = list(lines)
    for start, stop, content in reversed(changes):
        output[start:stop] = content
    combined = ''.join(output)
    if combined == original:
        return None
    try:
        ast.parse(combined)
    except SyntaxError:
        return None
    return combined


def partial_repair_credit(receipt: dict) -> int:
    """Use *execution results*, not a hand-assigned heuristic fitness value."""
    updated = receipt['patched']
    if not updated['controls_pass'] or not updated['independent_pass']:
        return 0
    return sum(int(r['returncode'] == 0 and not r['timeout']) for r in (
        updated['reproducer'] + updated['independent_reproductions']))

def synthesize(contract: IssueContract, path: str, *,
               max_candidates: int = 40, max_seconds: float = 180,
               line_start: int = 1, line_end: int = 100000,
               knobs: tuple[str, ...] = ('pop','strict','recursive','fixable'),
               focus_line: int | None = None, max_edits: int = 1,
               beam_width: int = 12) -> dict:
    """Return a verified bounded one- or two-site repair and Git patch.

    Guarantees neither global optimality nor successful upstream merge. Every
    accepted repair has an executable before/after and counterexample control.
    """
    rel=str(_relative(path))
    if rel not in contract.editable_files:
        raise IssueError('synthesis file must be explicitly editable in the contract')
    source_path=contract.checkout/rel
    if not source_path.is_file() or source_path.stat().st_size > 350000:
        raise IssueError('synthesis accepts only bounded Python source files')
    source=source_path.read_text('utf8')
    if source_path.suffix != '.py':
        raise IssueError('Python AST mutation currently supports .py only')
    if not 1 <= max_candidates <= 1000 or max_seconds <= 0:
        raise IssueError('invalid search budget')
    if max_edits not in (1, 2) or not 2 <= beam_width <= 32:
        raise IssueError('repair depth must be 1 or 2; beam width must be 2..32')
    started=time.monotonic()
    tested=0
    duplicate=set()
    rejected=[]
    partial=[]
    from .state_repair import borrowed_lookup_candidates
    from .context_repair import timeout_flow_candidates
    from .lifecycle_repair import initialization_cleanup_candidates
    from .async_cleanup import two_phase_close_candidates
    from .safety_repair import comment_preservation_candidates
    from .layout_repair import bounded_rejoin_candidates
    from .query_scope_repair import dml_scope_candidates
    generated_pool = candidates(source,line_start=line_start,line_end=line_end,
                              named_boolean_knobs=knobs)
    # The dynamic execution spectrum makes the first candidate genuinely
    # dependent on observed code paths, not source file ordering.
    if focus_line is None:
        generated=iter(generated_pool)
    else:
        generated=iter(sorted(generated_pool, key=lambda mutation:
                              (abs(mutation.line-focus_line), mutation.line, mutation.kind)))
    structural=iter(borrowed_lookup_candidates(contract,source,rel,
                              line_start=line_start,line_end=line_end))
    # Search high-information structural fixes first; local mutations are a
    # fallback, never counted if they fail the stronger control tests.
    from itertools import chain
    lifecycle=iter(initialization_cleanup_candidates(source,line_start=line_start,line_end=line_end))
    async_cleanup=iter(two_phase_close_candidates(source,line_start=line_start,line_end=line_end))
    comment_safety=iter(comment_preservation_candidates(source,line_start=line_start,line_end=line_end))
    layout_guard=iter(bounded_rejoin_candidates(source,line_start=line_start,line_end=line_end))
    query_scopes=iter(dml_scope_candidates(source,line_start=line_start,line_end=line_end))
    timeouts=iter(timeout_flow_candidates(source,line_start=line_start,line_end=line_end))
    with tempfile.TemporaryDirectory(prefix='axiomforge-synthesis-') as d, CandidateEvaluator(contract) as evaluator:
        patch=Path(d)/'candidate.patch'
        for mutation in chain(structural,lifecycle,async_cleanup,comment_safety,layout_guard,query_scopes,timeouts,generated):
            if time.monotonic()-started >= max_seconds or tested>=max_candidates:
                break
            if mutation.source in duplicate or mutation.source==source:
                continue
            duplicate.add(mutation.source)
            diff=to_patch(rel,source,mutation.source)
            if not diff:
                continue
            patch.write_text(diff,encoding='utf8')
            tested+=1
            try:
                receipt=evaluator.evaluate_patch(patch)
                if receipt['passed']:
                    return {'schema':'axiomforge.synthesized-patch.v1',
                            'status':'VERIFIED_CANDIDATE','issue_url':contract.issue_url,
                            'operator':mutation.kind,'line':mutation.line,
                            'attempts':tested,'seconds':round(time.monotonic()-started,3),
                            'edits_used':1,'patch':diff,'receipt':receipt,
                            'claim':'Patch passes declared tests, not an automatic upstream PR.'}
                if max_edits == 2 and contract.independent_reproductions:
                    credit=partial_repair_credit(receipt)
                    if credit:
                        partial.append((credit,mutation))
                rejected.append({'operator':mutation.kind,'line':mutation.line,
                                 'reason':'patch does not satisfy all declared failures and controls'})
            except IssueError as exc:
                rejected.append({'operator':mutation.kind,'line':mutation.line,
                                 'reason':str(exc)[:220]})
        # Counterexample-guided depth-2 stage. Prioritize candidates that
        # repaired at least one independent failing variant *without* breaking
        # any normal controls. Never concatenate arbitrary overlapping diffs.
        if max_edits == 2 and partial and contract.independent_reproductions:
            partial.sort(key=lambda x: (-x[0], x[1].line, x[1].kind))
            from itertools import combinations
            for (_,one),(_,two) in combinations(partial[:beam_width], 2):
                if tested >= max_candidates or time.monotonic()-started >= max_seconds:
                    break
                combined=compose_nonoverlapping(source,one.source,two.source)
                if combined is None or combined in duplicate:
                    continue
                duplicate.add(combined)
                diff=to_patch(rel,source,combined)
                if not diff:
                    continue
                patch.write_text(diff,encoding='utf8')
                tested+=1
                try:
                    receipt=evaluator.evaluate_patch(patch)
                    if receipt['passed']:
                        return {'schema':'axiomforge.synthesized-patch.v1',
                                'status':'VERIFIED_CANDIDATE','issue_url':contract.issue_url,
                                'operator':'composed-two-site','operators':[one.kind,two.kind],
                                'line':one.line,'other_line':two.line,'edits_used':2,
                                'attempts':tested,'seconds':round(time.monotonic()-started,3),
                                'patch':diff,'receipt':receipt,
                                'claim':'Two nonoverlapping changes pass declared and independent tests; not a proven upstream fix.'}
                    rejected.append({'operator':'composed-two-site', 'line':one.line,
                                     'reason':'composite candidate did not satisfy the complete contract'})
                except IssueError as exc:
                    rejected.append({'operator':'composed-two-site','line':one.line,
                                     'reason':str(exc)[:220]})
    return {'schema':'axiomforge.synthesized-patch.v1','status':'NOT_SOLVED',
            'issue_url':contract.issue_url,'attempts':tested,
            'seconds':round(time.monotonic()-started,3),
            'rejections':rejected[:15],
            'claim':'No passing patch found; never claim this Issue is resolved.'}
