"""Replay the PRE-REGISTERED negative holdout without changing repair operators.

WARNING: Executes source code of the supplied trusted upstream checkouts.
Exact source and operator hashes are enforced; newer versions cannot inherit
these historical holdout claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from axiomforge.issue_engine.core import IssueContract, IssueError, _check_baseline, _trial
from axiomforge.issue_engine.synthesis import synthesize


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-exec", action="store_true", help="Explicitly allow execution of external source/tests")
    parser.add_argument("--max-candidates", type=int, default=6)
    parser.add_argument("--max-seconds", type=float, default=22.0)
    args = parser.parse_args()
    if not args.allow_exec:
        parser.error("External code execution requires --allow-exec and trusted repositories")
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text(encoding="utf-8"))
    freeze = ROOT / "artifacts/v11_cross_repository/v11_operator_freeze.json"
    if sha256(freeze) != manifest["source_freeze_sha256"]:
        parser.error("Operator freeze manifest has changed; this is not the original holdout evaluation")
    for source_name, expected in json.loads(freeze.read_text(encoding="utf-8"))["files"].items():
        if sha256(ROOT / source_name) != expected:
            parser.error("Repair operator changed: " + source_name)
    outcomes: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="axiomforge-v11-unseen-") as tmp:
        for spec in manifest["cases"]:
            source = args.repos_root / spec["repo"]
            checkout = Path(tmp) / spec["id"]
            result: dict = {"id": spec["id"], "repository": spec["repo"], "issue": spec["url"]}
            if not source.is_dir():
                result.update(status="SOURCE_MISSING", reason=str(source))
                outcomes.append(result)
                continue
            target = source / spec["file"]
            if not target.is_file() or sha256(target) != spec["source_sha256"]:
                result.update(status="SOURCE_VERSION_MISMATCH", reason=spec["file"])
                outcomes.append(result)
                continue
            for label in ("repro", "control"):
                file = Path(__file__).parent / spec["id"] / f"{label}.py"
                if sha256(file) != spec[f"{label}_sha256"]:
                    parser.error("Holdout test modified: " + str(file))
            shutil.copytree(source, checkout, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            target_checks = checkout / "axiom_checks" / spec["id"]
            target_checks.mkdir(parents=True, exist_ok=True)
            for label in ("repro", "control"):
                shutil.copy2(Path(__file__).parent / spec["id"] / f"{label}.py", target_checks / f"{label}.py")
            rel = f"axiom_checks/{spec['id']}"
            contract = IssueContract.parse({
                "schema": "axiomforge.issue-contract.v1",
                "issue_url": spec["url"],
                "checkout": str(checkout),
                "reproduce": ["${PYTHON}", f"{rel}/repro.py"],
                "control": ["${PYTHON}", f"{rel}/control.py"],
                "editable_files": [spec["file"]],
                "failure_fingerprint": spec["fp"],
                "timeout_seconds": 8,
                "repetitions": 1,
                "isolated_checks": True,
                "independent_checks": [],
                "independent_reproductions": [],
            }, base=checkout)
            with tempfile.TemporaryDirectory(prefix="axiomforge-holdout-baseline-") as bd:
                baseline = _trial(contract, Path(bd) / "baseline", None)
            try:
                _check_baseline(baseline)
            except IssueError as e:
                result.update(status="NOT_REPRODUCIBLE", reason=str(e))
                outcomes.append(result)
                continue
            outcome = synthesize(contract, spec["file"],
                                 line_start=spec["lines"][0], line_end=spec["lines"][1],
                                 max_candidates=args.max_candidates, max_seconds=args.max_seconds)
            for key in ("status", "attempts", "seconds", "operator", "receipt", "patch"):
                if key in outcome:
                    result[key] = outcome[key]
            outcomes.append(result)
            print(spec["id"], result.get("status"), flush=True)
    summary = {x: sum(row.get("status") == x for row in outcomes)
               for x in sorted({row.get("status") for row in outcomes})}
    payload = {"schema": "axiomforge.v11.frozen-holdout-replay.v1",
               "assessment": "nonrandom, unseen by repair-operator development; limited sample",
               "summary": summary, "cases": outcomes}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Result:", str(args.out), summary)
    return 0 if all(row.get("status") not in ("ERROR", "SOURCE_MISSING", "SOURCE_VERSION_MISMATCH") for row in outcomes) else 2


if __name__ == "__main__":
    raise SystemExit(main())
