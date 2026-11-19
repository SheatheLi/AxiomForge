"""Bounded, replayable *native* HTTPX cancellation failure triage.

Never patches the installed dependency or imports a patched HTTPcore in the
parent process. Each observation is a fresh Python process talking to a real
loopback HTTP/1.1 server. Prefix shrinking is bounded, not an arbitrary trace
minimizer; all observations remain scheduler-dependent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from importlib.metadata import version
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ISSUE = "https://github.com/encode/httpx/issues/3782"
SOURCE = ROOT / "benchmarks/resource_protocol/httpx_double_cancel_local.py"
PATCHED = ROOT / "benchmarks/reliability/verify_patch_subprocess.py"
SYMPTOMS = ("pool_exhaustion", "stranded_active")


class NativeRunFailure(RuntimeError):
    pass


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _emit(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def target_identity() -> dict[str, str]:
    import httpcore
    import httpx
    from httpcore._async import http11
    if (httpx.__version__, httpcore.__version__) != ("0.28.1", "1.0.9"):
        raise NativeRunFailure("Requires inspected httpx==0.28.1 and httpcore==1.0.9")
    return {"python": sys.version.split()[0], "httpx": httpx.__version__,
            "httpcore": httpcore.__version__, "httpcore_http11_sha256": _digest(Path(http11.__file__)),
            "baseline_runner_sha256": _digest(SOURCE), "patched_runner_sha256": _digest(PATCHED)}


def _native(script: Path, repetitions: int, directory: Path, label: str, timeout: int) -> dict[str, Any]:
    if not 1 <= repetitions <= 12:
        raise ValueError("repetitions must be between 1 and 12")
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / f"{label}.json"
    stdout = directory / f"{label}.stdout.txt"
    stderr = directory / f"{label}.stderr.txt"
    command = [sys.executable, str(script), "--out", str(raw), "--repetitions", str(repetitions)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        stderr.write_text(f"PROCESS TIMEOUT after {timeout}s: {exc}\n", encoding="utf-8")
        raise NativeRunFailure(f"{label}: subprocess exceeded {timeout}s; inconclusive") from exc
    stdout.write_text(result.stdout, encoding="utf-8")
    stderr.write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0 or not raw.is_file():
        raise NativeRunFailure(f"{label}: native run failed (exit {result.returncode}), see {stderr}")
    data = json.loads(raw.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise NativeRunFailure(f"{label}: native result is not a JSON object")
    return data


def symptom_evidence(native: dict[str, Any], symptom: str) -> dict[str, Any]:
    """Verify observed symptom from actual source-run rows; never infer missing fields."""
    if symptom not in SYMPTOMS:
        raise ValueError("Unknown symptom")
    cases = native.get("double", {})
    rows = cases.get("rows", [])
    if not isinstance(rows, list):
        raise NativeRunFailure("Native double-cancellation rows missing")
    active = [(int(r.get("iteration", 0)), int(r["active_pool_connections"]))
              for r in rows if isinstance(r, dict) and isinstance(r.get("active_pool_connections"), int)
              and r.get("pending_requests") == 0]
    exhausted = [(int(r.get("iteration", 0)), str(r["inconclusive_or_blocked"]))
                 for r in rows if isinstance(r, dict) and r.get("inconclusive_or_blocked")
                 in ("TimeoutError", "PoolTimeout")]
    if symptom == "stranded_active":
        ok = any(n > 0 for _, n in active)
        position = next((i for i, n in active if n > 0), None)
    else:
        # A timeout alone is not proof of pool exhaustion: require the actual
        # pool limit of three observed active connections in the same run.
        ok = bool(exhausted and any(n >= 3 for _, n in active))
        position = exhausted[0][0] if ok else None
    cancel = [e for e in cases.get("cleanup_events", [])
              if e.get("event") == "CANCELLED_DURING_CLEANUP"]
    early_commit = [e for e in cancel if e.get("stage") == "HTTP11ConnectionByteStream.aclose"
                    and e.get("closed_flag") is True]
    return {"present": ok, "first_iteration": position, "quiescent_active": active,
            "blocked": exhausted, "cleanup_cancel_count": len(cancel),
            "early_committed_close_interrupted": len(early_commit),
            "protocol_findings": cases.get("protocol_summary", {}).get("finding_kinds", []),
            "notes": "Cancellation stage evidence is observed instrumentation, not a full causal proof."}


def analyze_native(native: dict[str, Any]) -> dict[str, Any]:
    control = native.get("single", {}).get("rows", [])
    return {"single_cancel_control_active": [r.get("active_pool_connections") for r in control
                                              if "active_pool_connections" in r],
            "single_cancel_blocked": [r.get("inconclusive_or_blocked") for r in control
                                      if r.get("inconclusive_or_blocked")],
            "double_cancel": {s: symptom_evidence(native, s) for s in SYMPTOMS},
            "mechanism": "Cancellation during HTTP11ConnectionByteStream.aclose after the close flag "
                         "was set can skip a later retry; inspect observed cleanup events."}


def triage(outdir: Path, *, symptom: str = "pool_exhaustion", repetitions: int = 5,
           confirmations: int = 2, timeout: int = 35, compare_repair: bool = True) -> dict[str, Any]:
    if symptom not in SYMPTOMS or not 1 <= repetitions <= 12 or not 1 <= confirmations <= 3:
        raise ValueError("Unsupported symptom, repetitions (1..12), or confirmations (1..3)")
    if not 10 <= timeout <= 120:
        raise ValueError("timeout must be in 10..120 seconds")
    outdir = outdir.resolve()
    identity = target_identity()
    runs: list[dict[str, Any]] = []
    full = _native(SOURCE, repetitions, outdir / "raw", "baseline_full", timeout)
    detection = symptom_evidence(full, symptom)
    for number in range(1, repetitions + 1):
        # A fresh *native subprocess*, never a synthetic transition graph.
        native = full if number == repetitions else _native(SOURCE, number, outdir / "raw", f"prefix_{number}", timeout)
        observed = symptom_evidence(native, symptom)
        runs.append({"repetitions": number, "present": observed["present"],
                     "evidence": f"raw/{'baseline_full' if number == repetitions else 'prefix_' + str(number)}.json",
                     "first_iteration": observed["first_iteration"]})
        if observed["present"]:
            minimum = number
            selected = native
            break
    else:
        minimum = None
        selected = None
    confirmations_result: list[dict[str, Any]] = []
    if minimum is not None:
        for index in range(confirmations):
            native = _native(SOURCE, minimum, outdir / "raw", f"confirm_{index + 1}", timeout)
            ev = symptom_evidence(native, symptom)
            confirmations_result.append({"present": ev["present"], "evidence": f"raw/confirm_{index + 1}.json"})
    stable = minimum is not None and all(x["present"] for x in confirmations_result)
    patched = None
    if stable and compare_repair:
        patched = _native(PATCHED, minimum, outdir / "raw", "patched_minimal", timeout)
        if patched.get("repetitions") != minimum or not patched.get("independent_python_process") or not patched.get("patch_dry_run"):
            raise NativeRunFailure("Patched copy has invalid process/patch provenance")
        if len(patched.get("double_active", [])) != minimum:
            raise NativeRunFailure("Patched run missing expected observations")
    repair_ok = None if patched is None else bool(patched["double_blocked"] == 0 and
                                                    all(n == 0 for n in patched["double_active"]))
    observed = None if selected is None else symptom_evidence(selected, symptom)
    status = "REPRODUCED" if stable else ("INTERMITTENT" if minimum is not None else "NOT_REPRODUCED")
    report_path = outdir / "triage.json"
    replay_output_path = outdir / "replayed.json"
    try:
        replay_record_arg = str(report_path.relative_to(ROOT))
        replay_output_arg = str(replay_output_path.relative_to(ROOT))
    except ValueError:
        replay_record_arg = str(report_path)
        replay_output_arg = str(replay_output_path)
    report = {
        "schema": "axiomforge.native-issue-triage.v1", "source_issue": ISSUE,
        "status": status, "symptom": symptom, "target": identity,
        "input": {"maximum_repetitions": repetitions, "confirmations": confirmations,
                  "per_process_timeout_s": timeout, "compare_repair": compare_repair},
        "full_observation": analyze_native(full),
        "prefix_reduction": {"unit": "HTTP stream cancel/retry iterations, not generic trace events",
                             "minimum_repetitions": minimum, "attempts": runs,
                             "minimal_observation": observed, "confirmation_runs": confirmations_result,
                             "stable_under_requested_checks": stable},
        "independent_patch_comparison": None if patched is None else {
            "scope": "Throwaway patched HTTPcore 1.0.9, independent Python process",
            "double_active": patched["double_active"], "double_blocked": patched["double_blocked"],
            "candidate_diff_sha256": patched["applied_diff_sha256"],
            "eliminated_observed_symptom": repair_ok,
            "warning": "Only this local TCP schedule was exercised; no Trio proof or upstream approval"},
        "replay": None if minimum is None else {
            "command": f"python -m axiomforge.issue_engine.reliability_cli replay-triage {replay_record_arg} --out {replay_output_arg}",
            "repetitions": minimum, "original_target_identity": identity,
            "expected_symptom": symptom},
        "limits": ["Real native HTTPX/HTTPcore loopback, no external network",
                   "Failure prefix minimized by actual execution, not byte-level trace shrinking",
                   "Timing-dependent; clean outcome on another host is not proof of no bug",
                   "No installed package is modified; candidate patch is applied in a disposable copy"]}
    _emit(outdir / "triage.json", report)
    return report


def replay(report_path: Path, out: Path, *, timeout: int = 35) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != "axiomforge.native-issue-triage.v1":
        raise ValueError("Not an AxiomForge native triage record")
    expected = report["target"]
    actual = target_identity()
    if actual != expected:
        result = {"status": "TARGET_MISMATCH", "expected": expected, "current": actual}
        _emit(out, result)
        return result
    minimum = report.get("prefix_reduction", {}).get("minimum_repetitions")
    if not isinstance(minimum, int) or not 1 <= minimum <= 12:
        raise ValueError("Triage record does not contain a valid minimized prefix")
    symptom = report["symptom"]
    native = _native(SOURCE, minimum, out.parent / "raw", "independent_replay", timeout)
    evidence = symptom_evidence(native, symptom)
    result = {"schema": "axiomforge.native-issue-replay.v1", "status": "REPRODUCED" if evidence["present"] else "NOT_REPRODUCED",
              "symptom": symptom, "minimum_repetitions": minimum,
              "evidence": evidence, "native_result": "raw/independent_replay.json",
              "source_triage_sha256": _digest(report_path)}
    _emit(out, result)
    return result
