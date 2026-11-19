"""Mutation-pressure testing of independent normal-behaviour contracts.

We inject real AST counterfactual changes into *production* code and execute
normal controls in isolated copies. A surviving mutant is a review warning,
not a claim that the original code is wrong or the mutant is semantically unique.
The audit never changes the original checkout or the test oracle files.
"""
from __future__ import annotations
from collections import Counter
import hashlib
import random
import tempfile
import time
from pathlib import Path
from typing import Any

from .core import IssueContract, IssueError, CandidateEvaluator
from .synthesis import candidates, to_patch

_VALID_KINDS = frozenset({'comparison', 'arithmetic', 'bool-toggle', 'keyword-toggle'})


def audit_oracle(contract: IssueContract, *, file: str, max_mutants: int = 12,
                 seed: int = 2026, max_seconds: float = 150,
                 line_start: int = 1, line_end: int = 100000,
                 include_kinds: frozenset[str] = _VALID_KINDS) -> dict[str, Any]:
    """Bounded deterministic mutation sampling with independent controls.

    A mutation is killed when *any* passing-control contract fails. Reproducer
    success alone does not kill: the evaluator cannot treat desired bug repair
    as a normal regression. 0/0 is INSUFFICIENT_DATA, never 100% quality.
    """
    if not 1 <= max_mutants <= 100 or not 1 <= max_seconds <= 3600:
        raise IssueError('oracle audit budget invalid')
    if not 1 <= line_start <= line_end:
        raise IssueError('invalid mutation source bounds')
    if not include_kinds or not include_kinds <= _VALID_KINDS:
        raise IssueError('unknown mutation operator for oracle audit')
    if file not in contract.editable_files or not file.endswith('.py'):
        raise IssueError('mutation audit requires an allowlisted Python production file')
    path = contract.checkout / file
    if not path.is_file() or path.stat().st_size > 350000:
        raise IssueError('audit source missing or too large')
    text = path.read_text('utf8')
    import ast
    ast.parse(text)
    all_candidates = []
    unique = set()
    for mutation in candidates(text, line_start=line_start, line_end=line_end):
        if mutation.kind not in include_kinds or mutation.source in unique or mutation.source == text:
            continue
        unique.add(mutation.source)
        all_candidates.append(mutation)
        if len(all_candidates) > 3000:
            raise IssueError('too many AST mutations; restrict production scope')
    rng = random.Random(seed)
    rng.shuffle(all_candidates)
    selected = all_candidates[:max_mutants]
    started = time.monotonic()
    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix='axiomforge-oracle-audit-') as root, CandidateEvaluator(contract) as evaluator:
        patch_path = Path(root) / 'counterfactual.patch'
        for mutation in selected:
            if time.monotonic() - started >= max_seconds:
                break
            patch = to_patch(file, text, mutation.source)
            if not patch:
                continue
            patch_path.write_text(patch, encoding='utf8')
            try:
                receipt = evaluator.evaluate_patch(patch_path, deadline=started+max_seconds)
            except IssueError as exc:
                evidence.append({'line': mutation.line, 'kind': mutation.kind,
                                 'status': 'UNASSESSED', 'reason': str(exc)[:180]})
                continue
            # Independent checks and repeated controls are both mandatory to count.
            killed = not receipt['patched']['controls_pass'] or not receipt['patched']['independent_pass']
            evidence.append({'line': mutation.line, 'kind': mutation.kind,
                             'status': 'KILLED_BY_CONTROLS' if killed else 'SURVIVED_CONTROLS',
                             'patch_sha256': hashlib.sha256(patch.encode('utf8')).hexdigest(),
                             'candidate_passed_full_contract': bool(receipt['passed']),
                             'baseline_reused': receipt['baseline_reused_across_candidates'],
                             'evidence_tier': receipt['evidence_tier']})
    counts = Counter(x['status'] for x in evidence)
    killed = counts['KILLED_BY_CONTROLS']
    survived = counts['SURVIVED_CONTROLS']
    assessed = killed + survived
    complete = len(evidence) == len(selected) and not counts['UNASSESSED']
    return {'schema': 'axiomforge.oracle-mutation-audit.v1',
            'status': 'AUDITED' if assessed and complete else 'PARTIAL' if assessed else 'INSUFFICIENT_DATA',
            'issue_url': contract.issue_url, 'source_file': file,
            'source_sha256': hashlib.sha256(text.encode('utf8')).hexdigest(),
            'seed': seed, 'line_range': [line_start,line_end], 'mutation_kinds': sorted(include_kinds),
            'total_eligible_mutations': len(all_candidates),
            'selected_mutations': len(selected), 'assessed_mutations': assessed,
            'killed_by_normal_controls': killed, 'survived_normal_controls': survived,
            'unassessed': counts['UNASSESSED'],
            'control_kill_fraction': round(killed / assessed, 6) if assessed else None,
            'seconds': round(time.monotonic()-started, 3), 'mutations': evidence,
            'limitations': ('Mutation kill fraction measures sensitivity of explicitly supplied normal controls to this bounded mutation catalog, '
                            'not universal test adequacy. Equivalent and beneficial changes can survive; no claim of APR generalization.')}
