"""Cross-project patch evaluation with causal, executable before/after evidence.

This is deliberately *not* a generic automatic bug fixer. It checks whether
an independently authored candidate patch actually changes the specific broken
behavior without breaking the declared controls. Patch candidates never modify
the original checkout; checks are performed on isolated snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sys
import signal
import subprocess
import tempfile
import time
from typing import Any

class IssueError(ValueError):
    """Unsafe input, false claim, or failed precondition."""

_SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.pytest_cache', '.tox',
         '.mypy_cache', '.ruff_cache', '.idea', '.vscode', 'build', 'dist'}
_MAX_FILES = 15000
_MAX_BYTES = 150_000_000
_MAX_LOG_CHARS = 12000


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _relative(path: str) -> Path:
    if not path or '\\' in path or '\x00' in path:
        raise IssueError('paths must be portable, nonempty relative paths')
    p = Path(path)
    if p.is_absolute() or any(x in ('', '.', '..') for x in p.parts):
        raise IssueError(f'unsafe relative path: {path!r}')
    return p


def _command(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise IssueError('commands must be nonempty JSON argv arrays (shell is never used)')
    if len(value) > 60:
        raise IssueError('command is too long')
    return tuple(value)


@dataclass(frozen=True)
class IssueContract:
    issue_url: str
    checkout: Path
    reproduce: tuple[str, ...]
    control: tuple[str, ...]
    editable_files: frozenset[str]
    fingerprint: str
    timeout_seconds: float = 20
    repetitions: int = 2
    version: str = 'axiomforge.issue-contract.v1'
    independent_checks: tuple[tuple[str, ...], ...] = ()
    independent_reproductions: tuple[tuple[str, ...], ...] = ()
    isolated_checks: bool = False

    @staticmethod
    def parse(obj: dict[str, Any], *, base: Path) -> 'IssueContract':
        if obj.get('schema') != 'axiomforge.issue-contract.v1':
            raise IssueError('unsupported issue contract schema')
        url = obj.get('issue_url', '')
        if not isinstance(url, str) or not re.match(r'^https://github\.com/[^/]+/[^/]+/issues/\d+$', url):
            raise IssueError('issue_url must be a real GitHub issue-shaped URL; existence not asserted')
        if not isinstance(obj.get('checkout'), str):
            raise IssueError('checkout path is missing')
        repo = (base / obj['checkout']).resolve()
        if not repo.is_dir():
            raise IssueError(f'repository path does not exist: {repo}')
        fp = obj.get('failure_fingerprint')
        if not isinstance(fp, str) or len(fp) < 6 or len(fp) > 500:
            raise IssueError('failure_fingerprint must identify a specific failure, at least six characters')
        try:
            re.compile(fp)
        except re.error as exc:
            raise IssueError(f'invalid failure fingerprint regex: {exc}') from exc
        isolated = obj.get('isolated_checks', False)
        if type(isolated) is not bool:
            raise IssueError('isolated_checks must be a boolean')
        timeout = obj.get('timeout_seconds', 20)
        reps = obj.get('repetitions', 2)
        if not isinstance(timeout, (int, float)) or not 0.2 <= timeout <= 300:
            raise IssueError('timeout_seconds must be between 0.2 and 300')
        if type(reps) is not int or not 1 <= reps <= 8:
            raise IssueError('repetitions must be an integer between 1 and 8')
        edit = obj.get('editable_files')
        if not isinstance(edit, list) or not edit or any(not isinstance(x, str) for x in edit):
            raise IssueError('editable_files must explicitly identify production files')
        allowed = frozenset(str(_relative(x)) for x in edit)
        if any(not (repo / x).is_file() for x in allowed):
            raise IssueError('editable_files contain missing production file')
        # The repair engine must not be allowed to 'solve' an Issue by editing
        # the test oracle itself. This catches direct Python script entrypoints;
        # more complex pytest/import graphs require user review.
        invoked_scripts = set()
        for command in [obj.get('reproduce'), obj.get('control'),
                        *obj.get('independent_checks', []),
                        *obj.get('independent_reproductions', [])]:
            if not isinstance(command, list):
                continue
            for i, arg in enumerate(command):
                if (isinstance(arg, str) and arg.endswith('.py') and
                        arg not in ('-c', '-m') and not arg.startswith('--')):
                    try:
                        rel = str(_relative(arg))
                    except IssueError:
                        continue
                    if (repo / rel).is_file():
                        invoked_scripts.add(rel)
        if allowed & invoked_scripts:
            raise IssueError('editable_files cannot include a directly invoked test/oracle script')
        checks = obj.get('independent_checks', [])
        if not isinstance(checks, list) or len(checks) > 12:
            raise IssueError('independent_checks must be a list of at most 12 argv arrays')
        extra_failures = obj.get('independent_reproductions', [])
        if not isinstance(extra_failures, list) or len(extra_failures) > 12:
            raise IssueError('independent_reproductions must be a list of at most 12 argv arrays')
        return IssueContract(url, repo, _command(obj.get('reproduce')),
                             _command(obj.get('control')), allowed, fp, float(timeout), reps,
                             independent_checks=tuple(_command(c) for c in checks),
                             independent_reproductions=tuple(_command(c) for c in extra_failures),
                             isolated_checks=isolated)


def load_contract(path: Path) -> IssueContract:
    obj = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(obj, dict):
        raise IssueError('contract must be a JSON object')
    return IssueContract.parse(obj, base=path.resolve().parent)


def _snapshot(src: Path, target: Path) -> None:
    counter = [0, 0]
    src = src.resolve()
    if target.is_relative_to(src):
        raise IssueError('snapshot destination cannot be inside source repository')
    def collect(directory: Path) -> None:
        for child in directory.iterdir():
            if child.name in _SKIP:
                continue
            if child.is_symlink():
                raise IssueError(f'checkout contains a symlink, snapshot refuses: {child}')
            if child.is_dir():
                collect(child)
            elif child.is_file():
                counter[0] += 1
                counter[1] += child.stat().st_size
                if counter[0] > _MAX_FILES or counter[1] > _MAX_BYTES:
                    raise IssueError('checkout exceeds bounded snapshot budget')
    collect(src)
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return set(names) & _SKIP
    shutil.copytree(src, target, ignore=ignore, symlinks=False)


def _run(argv: tuple[str, ...] | list[str], cwd: Path, timeout: float, *,
         env_update: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    if argv and argv[0] == '${PYTHON}':
        argv = [sys.executable,*argv[1:]]
    options: dict[str, Any] = {'cwd': str(cwd), 'text': True, 'encoding': 'utf-8',
                               'errors': 'replace', 'stdout': subprocess.PIPE,
                               'stderr': subprocess.PIPE, 'shell': False}
    if env_update is not None:
        options['env'] = {**os.environ, **env_update}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    # Trusted repository code executes only through an explicitly selected command.
    try:
        p = subprocess.Popen(argv, **options)
        try:
            stdout, stderr = p.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                p.kill()
            else:
                os.killpg(p.pid, signal.SIGKILL)
            stdout, stderr = p.communicate()
            timed_out = True
    except OSError as exc:
        raise IssueError(f'failed to start subprocess {argv!r}: {exc}') from exc
    return {'argv': list(argv), 'returncode': p.returncode, 'timeout': timed_out,
            'seconds': round(time.monotonic() - started, 4),
            'stdout': stdout[-_MAX_LOG_CHARS:], 'stderr': stderr[-_MAX_LOG_CHARS:],
            'stdout_sha256': hashlib.sha256(stdout.encode('utf8')).hexdigest(),
            'stderr_sha256': hashlib.sha256(stderr.encode('utf8')).hexdigest()}


def _broken(result: dict[str, Any], regex: str) -> bool:
    return (not result['timeout'] and result['returncode'] != 0 and
            bool(re.search(regex, result['stdout'] + '\n' + result['stderr'], re.MULTILINE)))


def _passes(result: dict[str, Any]) -> bool:
    return not result['timeout'] and result['returncode'] == 0


def _bounded_logs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return results


def _inspect_patch(data: str, allowed: frozenset[str]) -> None:
    """Reject unapproved paths and non-content changes before invoking git apply.

    Trusting only ---/+++ headers is insufficient: a git diff can also carry
    metadata paths and mode transitions. Fail closed for patches without a
    single coherent pair of source and destination file headers.
    """
    if "\x00" in data or not data.startswith("diff --git a/"):
        raise IssueError("patch must begin with a textual git diff header")
    blocks = data.splitlines(keepends=False)
    seen: set[str] = set()
    current: str | None = None
    have_old = have_new = have_hunk = False

    def finish() -> None:
        if current is not None and not (have_old and have_new and have_hunk):
            raise IssueError(f"incomplete file patch: {current}")

    for line in blocks:
        if line.startswith("diff --git "):
            finish()
            # Quoted / spaced git paths are intentionally rejected rather than
            # parsed ambiguously. Repository authors may use a safer patch form.
            match = re.fullmatch(r"diff --git a/([^\s]+) b/([^\s]+)", line)
            if not match or match.group(1) != match.group(2):
                raise IssueError("patch must edit an existing file with identical safe paths")
            current = str(_relative(match.group(1)))
            if current not in allowed:
                raise IssueError(f"patch modifies an unapproved file: {current}")
            if current in seen:
                raise IssueError(f"patch repeats a file block: {current}")
            seen.add(current)
            have_old = have_new = have_hunk = False
        elif line.startswith(('rename from ', 'rename to ', 'copy from ', 'copy to ',
                              'new file mode ', 'deleted file mode ', 'old mode ',
                              'new mode ', 'similarity index ', 'dissimilarity index ',
                              'GIT binary patch', 'Binary files ', 'literal ', 'delta ')):
            raise IssueError("patch cannot change modes, copy, rename, add, delete or embed binary data")
        elif line.startswith('--- '):
            if current is None or line != f'--- a/{current}' or have_old:
                raise IssueError("patch has an invalid original file header")
            have_old = True
        elif line.startswith('+++ '):
            if current is None or line != f'+++ b/{current}' or not have_old or have_new:
                raise IssueError("patch has an invalid destination file header")
            have_new = True
        elif line.startswith('@@ '):
            if not (current and have_old and have_new):
                raise IssueError("patch hunk without approved file headers")
            have_hunk = True
        elif line.startswith('index '):
            if current is None or have_old or not re.fullmatch(r'index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?',line):
                raise IssueError("unexpected patch index metadata")
        elif line.startswith('\\ No newline at end of file'):
            if not have_hunk:
                raise IssueError("orphan end-of-file marker")
        elif line == '' and have_hunk:
            continue
        elif line.startswith((' ', '+', '-')):
            if not have_hunk:
                raise IssueError("patch content outside a hunk")
        else:
            raise IssueError("unexpected git patch metadata or malformed patch content")
    finish()
    if not seen:
        raise IssueError("patch contains no approved changes")


def _apply_patch(sandbox: Path, patch: Path, timeout: float, allowed: frozenset[str]) -> None:
    if not patch.is_file() or patch.stat().st_size > 1_000_000:
        raise IssueError('missing or oversized patch')
    data = patch.read_text(encoding='utf8')
    _inspect_patch(data, allowed)
    for args in (['git', 'apply', '--check', '--', str(patch.resolve())],
                 ['git', 'apply', '--', str(patch.resolve())]):
        r = _run(args, sandbox, timeout)
        if not _passes(r):
            raise IssueError(f'git patch rejected: {r["stderr"][-1800:]}')

def _trial(contract: IssueContract, root: Path, patch: Path | None,
           fixture: tuple[str, bytes] | None = None, *,
           deadline: float | None = None) -> dict[str, Any]:
    _snapshot(contract.checkout, root)
    if fixture is not None:
        rel, data = fixture
        p = root / _relative(rel)
        if not p.is_file() or p.is_symlink():
            raise IssueError('fixture is not an existing regular checkout file')
        p.write_bytes(data)
    if patch is not None:
        _apply_patch(root, patch, contract.timeout_seconds, contract.editable_files)
    # Optional clean-state contract: each declared command runs against the
    # same pre-test source snapshot. Otherwise a failing reproducer could
    # contaminate the passing control, or a prior test could accidentally
    # make a following test pass. Tests cannot share generated state here.
    check_index = 0
    def execute(argv: tuple[str, ...]) -> dict[str, Any]:
        nonlocal check_index
        def execute_in(snapshot: Path) -> dict[str, Any]:
            # Child checkouts must import the patched checkout, never an
            # upstream installation or a stale absolute PYTHONPATH. Without
            # this, a successful git apply can still execute the old code.
            path = os.pathsep.join((str(snapshot / 'src'), str(snapshot),
                                   os.environ.get('PYTHONPATH', '')))
            remaining = contract.timeout_seconds
            if deadline is not None:
                remaining = min(remaining, deadline - time.monotonic())
                if remaining <= 0:
                    raise IssueError('search deadline exhausted before running a contract command')
            return _run(argv, snapshot, max(0.02, remaining),
                        env_update={'PYTHONPATH': path,
                                    'AXIOMFORGE_CHECKOUT_ROOT': str(snapshot)})

        if not contract.isolated_checks:
            return execute_in(root)
        check_index += 1
        detached = root.parent / f'{root.name}_check_{check_index}'
        # Every check is a new checkout of the same already-patched template;
        # generated files and test-side mutations never flow to another check.
        shutil.copytree(root, detached, ignore=lambda d, names: set(names) & _SKIP)
        try:
            return execute_in(detached)
        finally:
            shutil.rmtree(detached, ignore_errors=True)

    reps = [execute(contract.reproduce) for _ in range(contract.repetitions)]
    ctrl = [execute(contract.control) for _ in range(contract.repetitions)]
    independent = [execute(command) for command in contract.independent_checks]
    extra_failures = [execute(command) for command in contract.independent_reproductions]
    return {'reproducer': reps, 'control': ctrl, 'independent_checks': independent,
            'independent_reproductions': extra_failures,
            'independent_pass': all(_passes(r) for r in independent),
            'independent_failure_confirmed': all(_broken(r, contract.fingerprint) for r in extra_failures),
            'independent_failure_repaired': all(_passes(r) for r in extra_failures),
            'consistent_fail': all(_broken(r, contract.fingerprint) for r in reps),
            'repaired': all(_passes(r) for r in reps),
            'controls_pass': all(_passes(r) for r in ctrl)}


def _check_baseline(base: dict[str, Any]) -> None:
    if not base['consistent_fail']:
        raise IssueError('baseline does not reproduce the SPECIFIC issue on every run')
    if not base['controls_pass']:
        raise IssueError('baseline control tests do not pass; patch cannot be credited')
    if not base['independent_pass']:
        raise IssueError('baseline independent checks do not pass; invalid additional contract')
    if not base['independent_failure_confirmed']:
        raise IssueError('baseline independent reproductions must trigger the specific failure')



def _source_fingerprint(contract: IssueContract) -> dict[str, str]:
    """Hash every authorized source file; evidence binds to the input snapshot."""
    return {name: _digest(contract.checkout / name) for name in sorted(contract.editable_files)}


class CandidateEvaluator:
    """Cache one validated baseline while keeping every candidate isolated.

    Candidate evaluations share immutable baseline *results*, not mutable
    working trees. Every patch gets a fresh checkout snapshot and subprocesses.
    """
    def __init__(self, contract: IssueContract):
        self.contract = contract
        self._temp: tempfile.TemporaryDirectory | None = None
        self._baseline: dict[str, Any] | None = None
        self._counter = 0
        self._sources = _source_fingerprint(contract)

    def __enter__(self) -> 'CandidateEvaluator':
        self._temp = tempfile.TemporaryDirectory(prefix='axiomforge-search-')
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._temp:
            self._temp.cleanup()
            self._temp = None

    def evaluate_patch(self, patch: Path, *, deadline: float | None = None) -> dict[str, Any]:
        if self._temp is None:
            raise IssueError('candidate evaluator must be used inside its context')
        if deadline is not None and time.monotonic() >= deadline:
            raise IssueError('search deadline exhausted before patch evaluation')
        if _source_fingerprint(self.contract) != self._sources:
            raise IssueError('source files changed during candidate search; cached baseline invalid')
        started = time.monotonic()
        directory = Path(self._temp.name)
        if self._baseline is None:
            candidate_baseline = _trial(self.contract, directory / 'baseline', None, deadline=deadline)
            try:
                _check_baseline(candidate_baseline)
            except IssueError:
                shutil.rmtree(directory / 'baseline', ignore_errors=True)
                raise
            # NEVER cache an unverified baseline. An exception on the first
            # candidate must not let the second bypass the hard precondition.
            self._baseline = candidate_baseline
        self._counter += 1
        candidate_snapshot = directory / f'patched_{self._counter}'
        try:
            updated = _trial(self.contract, candidate_snapshot, patch.resolve(), deadline=deadline)
        finally:
            if candidate_snapshot.exists():
                shutil.rmtree(candidate_snapshot)
        return _build_receipt(self.contract, patch.resolve(), self._baseline,
                              updated, time.monotonic() - started,
                              self._sources, baseline_reused=self._counter > 1)


def _build_receipt(contract: IssueContract, patch: Path, base: dict[str, Any],
                   updated: dict[str, Any], seconds: float, sources: dict[str, str],
                   *, baseline_reused: bool = False) -> dict[str, Any]:
    return {'schema': 'axiomforge.patch-evidence.v1', 'issue_url': contract.issue_url,
            'repo': str(contract.checkout), 'source_sha256': sources,
            'patch_sha256': _digest(patch), 'baseline': base, 'patched': updated,
            'passed': bool(updated['repaired'] and updated['controls_pass'] and
                           updated['independent_pass'] and updated['independent_failure_repaired']),
            'independent_check_count': len(contract.independent_checks),
            'independent_reproduction_count': len(contract.independent_reproductions),
            'isolated_checks': contract.isolated_checks,
            'evidence_tier': ('INDEPENDENT_FAILURES_AND_CONTROLS' if contract.independent_checks and contract.independent_reproductions else
                              'INDEPENDENT_FAILURES' if contract.independent_reproductions else
                              'INDEPENDENT_CONTROLS' if contract.independent_checks else 'DECLARED_TESTS_ONLY'),
            'baseline_reused_across_candidates': baseline_reused,
            'measurement_seconds': round(seconds, 4),
            'interpretation': 'local patch candidate only; no PR or upstream acceptance'}

def evaluate(contract: IssueContract, patch: Path, *, fixture: tuple[str, bytes] | None = None) -> dict[str, Any]:
    """Before/after experiment, with two fresh copies of the original repo.

    A result is *candidate patch evidence*, never an upstream PR or a proof of
    production correctness. No command may change the original checkout.
    """
    patch = patch.resolve()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='axiomforge-issue-') as temp:
        parent = Path(temp)
        base = _trial(contract, parent / 'base', None, fixture)
        _check_baseline(base)
        updated = _trial(contract, parent / 'patched', patch, fixture)
    return _build_receipt(contract, patch, base, updated, time.monotonic() - started,
                          _source_fingerprint(contract))


def _chunks(items: list[int], n: int) -> list[list[int]]:
    size, extra = divmod(len(items), n)
    out: list[list[int]] = []
    at = 0
    for i in range(n):
        length = size + (i < extra)
        if length:
            out.append(items[at:at + length]); at += length
    return out


def minimize(contract: IssueContract, patch: Path, file: str, *,
             max_evaluations: int = 60, max_seconds: float = 180) -> dict[str, Any]:
    """Hierarchical line DDMin under TWO predicates: old fails, fix works.

    Minimizes *reproduction input*, not production code. On budget exhaustion,
    no claim of single-line minimality may be made.
    """
    file = str(_relative(file))
    original = contract.checkout / file
    if not original.is_file() or original.is_symlink() or original.stat().st_size > 150_000:
        raise IssueError('minimization accepts only small regular input files')
    lines = original.read_bytes().splitlines(keepends=True)
    if len(lines) > 500 or len(lines) < 2:
        raise IssueError('minimization requires 2..500 input lines')
    if max_evaluations < 2 or max_seconds <= 0:
        raise IssueError('invalid evaluation budget')
    started = time.monotonic()
    active = list(range(len(lines)))
    evaluations = 0
    memo: dict[str, bool] = {}

    # Isolated baseline and repaired repositories are prepared only once.
    # All candidates mutate only the *input fixture*, not source code; this
    # avoids 2N complete repo copies and repeated git apply invocations.
    with tempfile.TemporaryDirectory(prefix='axiomforge-shrink-') as temp:
        root=Path(temp)
        base=root/'base'; fixed=root/'fixed'
        _snapshot(contract.checkout,base)
        _snapshot(contract.checkout,fixed)
        _apply_patch(fixed,patch.resolve(),contract.timeout_seconds,contract.editable_files)
        p_base=base/file; p_fixed=fixed/file
        def predicate(indices: list[int]) -> bool | None:
            nonlocal evaluations
            if evaluations >= max_evaluations or time.monotonic() - started >= max_seconds:
                return None
            data = b''.join(lines[i] for i in indices)
            h = hashlib.sha256(data).hexdigest()
            if h in memo:
                return memo[h]
            evaluations += 1
            p_base.write_bytes(data)
            p_fixed.write_bytes(data)
            left=_run(contract.reproduce,base,contract.timeout_seconds)
            # Short-circuit on unrelated failures. With an invalid baseline,
            # there is no causal claim about this particular issue.
            if not _broken(left,contract.fingerprint):
                memo[h]=False
                return False
            right=_run(contract.reproduce,fixed,contract.timeout_seconds)
            memo[h]=_passes(right)
            return memo[h]

        initial=predicate(active)
        if not initial:
            raise IssueError('initial fixture does not satisfy the before/after contract')
        granularity=2
        while len(active)>1:
            removed=False
            for chunk in _chunks(active,min(granularity,len(active))):
                gone=set(chunk)
                remaining=[i for i in active if i not in gone]
                res=predicate(remaining)
                if res is None:break
                if res:
                    active=remaining
                    granularity=max(2,granularity-1)
                    removed=True
                    break
            if evaluations>=max_evaluations or time.monotonic()-started>=max_seconds:
                break
            if not removed:
                if granularity>=len(active):break
                granularity=min(len(active),granularity*2)

        # Audit to a fixed point. Do not call the witness 1-minimal if we lack
        # a complete independent last pass of every remaining line.
        audit_complete=False
        while evaluations<max_evaluations and time.monotonic()-started<max_seconds:
            removed=False
            audit_complete=True
            for idx in list(active):
                trial=[i for i in active if i!=idx]
                res=predicate(trial)
                if res is None:
                    audit_complete=False
                    break
                if res:
                    active=trial
                    audit_complete=False
                    removed=True
                    break
            if audit_complete or not removed:
                break

    # Re-run the entire contract at final size, including both control tests
    # and requested repetitions: short-search probes are not final evidence.
    final=evaluate(contract,patch,fixture=(file,b''.join(lines[i] for i in active)))
    if not final['passed']:
        raise IssueError('final full issue contract rejected reduced witness')
    minimal=audit_complete and evaluations<max_evaluations and time.monotonic()-started<max_seconds
    return {'schema':'axiomforge.minimized-issue.v1','issue_url':contract.issue_url,
            'file':file,'original_sha256':_digest(original),
            'original_lines':len(lines), 'remaining_lines':len(active),
            'content':b''.join(lines[i] for i in active).decode('utf8',errors='replace'),
            'evaluations':evaluations, 'one_minimal':minimal,
            'budget_exhausted':not minimal,
            'patch_sha256':_digest(patch),
            'witness':final,
            'meaning':'Minimized executable reproduction, not a proof that the entire Issue is fixed.'}
