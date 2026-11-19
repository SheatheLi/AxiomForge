"""Dynamic spectrum-based fault localization on real executable Issue contracts.

Collects failure and passing-control execution spectra in immutable source
snapshots. The Ochiai score is a search priority, never a correctness claim.
"""
from __future__ import annotations
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

from .core import IssueContract, IssueError, _snapshot, _run, _broken, _passes


def _trace(contract: IssueContract, root: Path, command: tuple[str, ...],
           out: Path, *, max_events: int) -> tuple[dict[str, Any], dict[str, Any]]:
    args = list(command)
    if args[0] == '${PYTHON}':
        args[0] = sys.executable
    if Path(args[0]).resolve() != Path(sys.executable).resolve():
        raise IssueError('fault localization supports only explicitly invoked current Python interpreter')
    if len(args) < 2 or not (args[1].endswith('.py') or args[1] in ('-m', '-c')):
        raise IssueError('fault localization supports Python script, -c or -m commands only')
    os_env = {
        'AXIOMFORGE_TRACE_ROOT': str(root),
        'AXIOMFORGE_TRACE_OUT': str(out),
        'AXIOMFORGE_TRACE_ALLOW': json.dumps(sorted(contract.editable_files)),
        'AXIOMFORGE_TRACE_BUDGET': str(max_events),
    }
    # Use explicit child environment, not global os.environ mutation.
    from .trace_entry import __file__ as entry
    outcome = _run([sys.executable, entry, *args[1:]], root,
                   contract.timeout_seconds, env_update=os_env)
    if not out.is_file():
        raise IssueError('instrumented process produced no trace (crash, timeout or unsupported entry)')
    data = json.loads(out.read_text('utf8'))
    if data.get('truncated'):
        raise IssueError('execution trace budget exhausted; cannot rank incomplete spectra')
    if any(p not in contract.editable_files for p in data['files']):
        raise IssueError('trace includes a non-authorized production file')
    return outcome, data


def localize(contract: IssueContract, *, max_events: int = 2000000,
             max_sites: int = 150, repetitions: int | None = None) -> dict[str, Any]:
    """Rank observed source lines by Ochiai similarity to the specific failure.

    An uncovered fault, environment mismatch or unsupported command returns an
    explicit error, not a misleading suspiciousness measurement.
    """
    if not 1_000 <= max_events <= 20_000_000 or not 1 <= max_sites <= 2000:
        raise IssueError('invalid tracing budget')
    reps = contract.repetitions if repetitions is None else repetitions
    if not 1 <= reps <= 8:
        raise IssueError('invalid repetitions')
    started = time.monotonic()
    results: list[dict[str, Any]] = []
    hit_fail: dict[tuple[str, int], int] = defaultdict(int)
    hit_pass: dict[tuple[str, int], int] = defaultdict(int)
    events_fail: dict[tuple[str, int], int] = defaultdict(int)
    events_pass: dict[tuple[str, int], int] = defaultdict(int)
    with tempfile.TemporaryDirectory(prefix='axiomforge-spectrum-') as d:
        base = Path(d)
        repo = base / 'snapshot'
        _snapshot(contract.checkout, repo)
        for kind, command in [('fail', contract.reproduce), ('pass', contract.control)]:
            for index in range(reps):
                receipt, sample = _trace(contract, repo, command,
                                         base / f'{kind}_{index}.json', max_events=max_events)
                if kind == 'fail' and not _broken(receipt, contract.fingerprint):
                    raise IssueError('instrumented failure does not reproduce required fingerprint')
                if kind == 'pass' and not _passes(receipt):
                    raise IssueError('instrumented control fails; cannot compute comparable spectra')
                target = hit_fail if kind == 'fail' else hit_pass
                frequency_target = events_fail if kind == 'fail' else events_pass
                for name, lines in sample['files'].items():
                    for number in set(lines):
                        target[(name, number)] += 1
                for name, counts in sample.get('frequencies', {}).items():
                    for number, frequency in counts.items():
                        frequency_target[(name, int(number))] += frequency
                results.append({'kind': kind, 'run': index, 'seconds': receipt['seconds'],
                                'events': sample['events'], 'executed_lines': sum(map(len, sample['files'].values()))})
    rankings = []
    for name, line in sorted(set(hit_fail) | set(hit_pass)):
        fails, passes = hit_fail[(name, line)], hit_pass[(name, line)]
        if not fails:
            continue
        score = fails / math.sqrt(reps * (fails + passes))
        failed_events = events_fail[(name, line)]
        control_events = events_pass[(name, line)]
        lift = math.log2((failed_events + reps) / (control_events + reps))
        priority = score + 0.15 * min(2.0, max(0.0, lift))
        rankings.append({'file': name, 'line': line, 'score': round(score, 6),
                         'priority_score': round(priority, 6),
                         'failed_line_events': failed_events, 'control_line_events': control_events,
                         'failed_runs': fails, 'control_runs': passes})
    rankings.sort(key=lambda x: (-x['priority_score'], -x['failed_runs'], x['control_runs'], x['file'], x['line']))
    if not rankings:
        raise IssueError('no authorized source lines executed in failing test')
    file_rank = {}
    for item in rankings:
        f = item['file']
        if f not in file_rank:
            file_rank[f] = {'file': f, 'peak_score': item['priority_score'], 'best_line': item['line']}
    contrasting_sites = sum(1 for item in rankings if item['failed_runs'] > item['control_runs']
                            or item['failed_line_events'] > item['control_line_events'])
    return {'schema': 'axiomforge.fault-spectrum.v1', 'status': 'TRACED',
            'contrast_available': contrasting_sites > 0, 'contrasting_sites': contrasting_sites,
            'issue_url': contract.issue_url, 'strategy': 'Ochiai executed-line spectrum with bounded frequency-lift prioritization',
            'repetitions': reps, 'sites': rankings[:max_sites], 'file_ranking': list(file_rank.values()),
            'total_ranked_sites': len(rankings), 'measurements': results,
            'seconds': round(time.monotonic() - started, 3),
            'limitations': ('No discriminating execution difference; ranking is not evidence of a fault location.'
                            if not contrasting_sites else
                            'Passing-control spectra are only as representative as supplied controls; line priority is not a causal proof.')}
