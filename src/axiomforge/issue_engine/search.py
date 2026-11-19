"""Budgeted AST repair search with optional executable failure-spectrum guidance.

The user grants an *allowlist*, not a fault location. Traces rank all permitted
files based on failed vs passing executions and prioritize nearby AST edits;
a fair fallback searches remaining code if coverage is incomplete.
"""
from __future__ import annotations
import time
from .core import IssueContract, IssueError
from .synthesis import synthesize


def synthesize_repository(contract: IssueContract, *, max_candidates_per_file: int = 20,
                          max_seconds: float = 240, max_files: int = 100,
                          use_spectrum: bool = False, max_trace_events: int = 2000000,
                          max_edits: int = 1, use_dataflow: bool = False) -> dict:
    if not 1 <= max_candidates_per_file <= 1000 or not 1 <= max_files <= 1000 or max_seconds <= 0:
        raise IssueError('invalid repository search budget')
    if use_dataflow and not use_spectrum:
        raise IssueError('dataflow-guided repair requires dynamic spectrum; do not guess a fault seed')
    started = time.monotonic()
    files = sorted(f for f in contract.editable_files if f.endswith('.py'))
    if not files:
        raise IssueError('no authorized Python production files to search')
    if len(files) > max_files:
        raise IssueError('too many editable files; narrow the authorized boundary')
    spectrum = None
    leaders = {}
    if use_spectrum:
        from .localization import localize
        spectrum = localize(contract, max_events=max_trace_events,
                            repetitions=min(contract.repetitions, 2))
        # A completely shared spectrum supplies no location evidence.
        # Do not optimize on the misleading 'line 1' tie-breaker.
        leaders = ({x['file']: x for x in spectrum['file_ranking']}
                   if spectrum['contrast_available'] else {})
        files.sort(key=lambda f: (-leaders.get(f, {}).get('peak_score', -1), f))
    checked = []
    flow_evidence: dict[str, dict] = {}
    attempts = 0
    for index, name in enumerate(files):
        remaining = max_seconds - (time.monotonic() - started)
        if remaining <= 0:
            break
        allowance = max(1.0, remaining / (len(files) - index))
        launch = time.monotonic()
        hypotheses = []
        leader = leaders.get(name)
        # The fault may live at a definition line, not the executed statement:
        # consider a generous region around the strongest executable frame.
        if leader and use_dataflow:
            from .dataflow import interprocedural_slice
            try:
                src=contract.checkout / name
                if src.stat().st_size>350000:
                    raise IssueError('authorized source exceeds bounded dataflow budget')
                flow_evidence[name] = interprocedural_slice(
                    src.read_text('utf8'), leader['best_line'])
            except IssueError as exc:
                flow_evidence[name] = {'status': 'UNSUPPORTED', 'reason': str(exc)}
        if leader:
            line = leader['best_line']
            focus_budget = max(1, max_candidates_per_file // 2)
            hypotheses.append(dict(line_start=max(1, line - 12), line_end=line + 12,
                                   max_candidates=focus_budget, max_seconds=allowance * 0.75,
                                   focus_line=line,
                                   priority_lines=tuple(flow_evidence.get(name, {}).get('sliced_lines', []))))
        hypotheses.append(dict(max_candidates=max_candidates_per_file, max_seconds=allowance,
                               focus_line=leader['best_line'] if leader else None,
                               priority_lines=tuple(flow_evidence.get(name, {}).get('sliced_lines', []))))
        file_attempts = 0
        last = None
        for spec in hypotheses:
            elapsed = time.monotonic() - launch
            left = allowance - elapsed
            if left <= 0:
                break
            spec['max_seconds'] = min(spec['max_seconds'], left)
            outcome = synthesize(contract, name, max_edits=max_edits, **spec)
            last = outcome
            file_attempts += outcome.get('attempts', 0)
            if outcome['status'] == 'VERIFIED_CANDIDATE':
                attempts += file_attempts
                checked.append({'file': name, 'status': outcome['status'], 'attempts': file_attempts,
                                'seconds': round(time.monotonic() - launch, 3),
                                'spectrum_score': leader['peak_score'] if leader else None})
                outcome['repository_search'] = dict(checked=checked, files_authorized=len(files),
                    candidate_files_considered=len(checked), candidate_attempts=attempts,
                    searched_without_preselecting_filename=True,
                    spectrum_guided=use_spectrum, spectrum_contrast_used=bool(leaders),
                    dataflow_guided=use_dataflow, dataflow=flow_evidence, spectrum=spectrum)
                return outcome
        attempts += file_attempts
        checked.append({'file': name, 'status': last['status'] if last else 'BUDGET_EXHAUSTED',
                        'attempts': file_attempts, 'seconds': round(time.monotonic()-launch, 3),
                        'spectrum_score': leader['peak_score'] if leader else None})
    return {'schema': 'axiomforge.synthesized-patch.v1', 'status': 'NOT_SOLVED',
            'issue_url': contract.issue_url, 'attempts': attempts,
            'seconds': round(time.monotonic()-started, 3),
            'repository_search': dict(checked=checked, files_authorized=len(files),
                                      searched_without_preselecting_filename=True,
                                      spectrum_guided=use_spectrum, spectrum_contrast_used=bool(leaders),
                    dataflow_guided=use_dataflow, dataflow=flow_evidence, spectrum=spectrum),
            'claim': 'No verified patch in the authorized scope; do not claim this Issue is resolved.'}
