"""Counterfactual patch-hunk necessity audit for an independently executable repair.

A passing repair can contain unrelated or harmful edits. Remove each hunk in
turn, replay the identical fault contract, and report whether the hunk is
actually required by the existing evidence. This is evidence-relative, not a
proof of global minimality or patch correctness.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile
import time

from .core import IssueContract, IssueError, CandidateEvaluator, _inspect_patch


def split_hunks(patch_text: str) -> tuple[list[str], list[tuple[str, list[str], list[str]]]]:
    """Separate a conventional unified Git diff into independently removable hunks."""
    preamble: list[str] = []
    hunks: list[tuple[str, list[str], list[str]]] = []
    current_header: list[str] = []
    current_hunk: list[str] = []
    file_name = ''
    for line in patch_text.splitlines(keepends=True):
        if line.startswith('diff --git '):
            if current_hunk:
                hunks.append((file_name, list(current_header), current_hunk))
                current_hunk = []
            current_header = [line]
            file_name = line.split(' b/', 1)[-1].strip()
        elif line.startswith('@@ '):
            if current_hunk:
                hunks.append((file_name, list(current_header), current_hunk))
            current_hunk = [line]
        elif current_hunk:
            current_hunk.append(line)
        elif current_header:
            current_header.append(line)
        else:
            preamble.append(line)
    if current_hunk:
        hunks.append((file_name, list(current_header), current_hunk))
    return preamble, hunks


def without_hunk(preamble: list[str], hunks: list[tuple[str, list[str], list[str]]], skip: int) -> str:
    pieces = list(preamble)
    last_header: list[str] | None = None
    for index, (_, header, body) in enumerate(hunks):
        if index == skip:
            continue
        if header != last_header:
            pieces.extend(header)
            last_header = header
        pieces.extend(body)
    return ''.join(pieces)


def audit_hunk_necessity(contract: IssueContract, patch: Path, *, max_hunks: int = 12,
                         max_seconds: float = 180) -> dict:
    """Require a real verified patch, then test deletion of each diff hunk.

    Some hunk deletions are not applicable without their neighbor. Those are
    explicitly UNDETERMINED rather than being counted as essential.
    """
    patch = patch.resolve()
    data = patch.read_text(encoding='utf8')
    _inspect_patch(data, contract.editable_files)
    preamble, hunks = split_hunks(data)
    if not hunks or len(hunks) > max_hunks:
        raise IssueError('ablation requires between 1 and max_hunks unified diff hunks')
    start = time.monotonic()
    rows = []
    with CandidateEvaluator(contract) as evaluator:
        original = evaluator.evaluate_patch(patch, deadline=start+max_seconds)
        if not original['passed']:
            return {'schema': 'axiomforge.ablation.v1', 'status': 'ORIGINAL_NOT_VERIFIED',
                    'original': original, 'hunks': [], 'evidence_relative_necessary': False}
        with tempfile.TemporaryDirectory(prefix='axiomforge-ablation-') as temp:
            for i, (name, _, _) in enumerate(hunks):
                if time.monotonic() - start >= max_seconds:
                    rows.append({'index': i, 'file': name, 'result': 'NOT_TESTED_BUDGET'})
                    continue
                if len(hunks) == 1:
                    rows.append({'index': i, 'file': name, 'result': 'SOLE_HUNK',
                                 'explanation': 'the original baseline fails; removal is equivalent to no patch'})
                    continue
                candidate = without_hunk(preamble, hunks, i)
                try:
                    _inspect_patch(candidate, contract.editable_files)
                    temp_patch = Path(temp) / f'without_{i}.patch'
                    temp_patch.write_text(candidate, encoding='utf8')
                    receipt = evaluator.evaluate_patch(temp_patch, deadline=start+max_seconds)
                    rows.append({'index': i, 'file': name,
                                 'result': 'REDUNDANT_UNDER_CONTRACT' if receipt['passed'] else 'REQUIRED_UNDER_CONTRACT',
                                 'remaining_patch_sha256': hashlib.sha256(candidate.encode()).hexdigest()})
                except (IssueError, OSError) as exc:
                    rows.append({'index': i, 'file': name, 'result': 'UNDETERMINED',
                                 'reason': str(exc)[:500]})
    return {'schema': 'axiomforge.ablation.v1',
            'status': 'AUDITED', 'issue_url': contract.issue_url,
            'original_patch_sha256': hashlib.sha256(data.encode()).hexdigest(),
            'hunk_count': len(hunks), 'hunks': rows,
            'evidence_relative_necessary': all(x['result'] in ('REQUIRED_UNDER_CONTRACT', 'SOLE_HUNK') for x in rows),
            'meaning': 'necessary only under the declared executable tests; not semantic or global minimality',
            'seconds': round(time.monotonic() - start, 3)}
