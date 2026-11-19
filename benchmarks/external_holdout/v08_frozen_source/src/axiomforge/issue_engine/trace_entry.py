"""Isolated subprocess entrypoint for fault-localization observations.

Not an execution sandbox: the user explicitly authorizes trusted checkout code.
Coverage is written outside the checkout and is never used as an oracle.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import runpy
import sys
import threading


def main() -> None:
    root = Path(os.environ['AXIOMFORGE_TRACE_ROOT']).resolve()
    target = Path(os.environ['AXIOMFORGE_TRACE_OUT']).resolve()
    allow = set(json.loads(os.environ['AXIOMFORGE_TRACE_ALLOW']))
    budget = int(os.environ.get('AXIOMFORGE_TRACE_BUDGET', '2000000'))
    lines: dict[str, set[int]] = {}
    frequencies: dict[str, dict[int, int]] = {}
    events = 0
    truncated = False

    def tracer(frame, event, arg):
        nonlocal events, truncated
        if event != 'call':
            if event == 'line' and getattr(frame, 'f_trace_lines', True):
                events += 1
                if events > budget:
                    truncated = True
                    return None
                rel = frame.f_code.co_filename
                try:
                    resolved = Path(rel).resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    return None
                if resolved in allow:
                    lines.setdefault(resolved, set()).add(frame.f_lineno)
                    hits = frequencies.setdefault(resolved, {})
                    hits[frame.f_lineno] = hits.get(frame.f_lineno, 0) + 1
            return tracer
        if events >= budget:
            truncated = True
            return None
        try:
            rel = Path(frame.f_code.co_filename).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return None
        return tracer if rel in allow else None

    args = sys.argv[1:]
    if not args:
        raise ValueError('missing traced Python entry command')
    # Resolve the exact caller command convention without using a shell.
    if args[0] == '-m':
        if len(args) < 2:
            raise ValueError('missing module name')
        module, remainder = args[1], args[2:]
        sys.argv = [module, *remainder]
        runner = lambda: runpy.run_module(module, run_name='__main__', alter_sys=True)
    elif args[0] == '-c':
        if len(args) < 2:
            raise ValueError('missing Python -c source')
        code, remainder = args[1], args[2:]
        sys.argv = ['-c', *remainder]
        runner = lambda: exec(compile(code, '<string>', 'exec'), {'__name__': '__main__', '__file__': '<string>'})
    elif args[0].endswith('.py'):
        filename, remainder = args[0], args[1:]
        sys.argv = [filename, *remainder]
        script = Path(filename).resolve()
        sys.path.insert(0, str(script.parent))
        runner = lambda: runpy.run_path(str(script), run_name='__main__')
    else:
        raise ValueError('tracing supports Python script or -m module commands only')
    sys.path.insert(0, str(root))
    sys.settrace(tracer)
    threading.settrace(tracer)
    try:
        runner()
    finally:
        sys.settrace(None)
        threading.settrace(None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'version': 1, 'files': {k: sorted(v) for k, v in sorted(lines.items())},
                                     'events': events, 'truncated': truncated,
                                     'frequencies': {name: {str(k): v for k, v in sorted(counts.items())}
                                                     for name, counts in sorted(frequencies.items())}}, sort_keys=True), encoding='utf8')


if __name__ == '__main__':
    main()
