# Reliability reproducible experiments

- `run_live_comparison.py`: actual HTTPX/HTTPcore loopback; 5 in-memory strategies × single/double cancel, 4 repetitions each; **monkeypatch private runtime methods temporarily**. Must run alone in a trusted process.
- `verify_patch_subprocess.py`: `patch --dry-run`, actual apply to a temporary full HTTPcore source copy, independent Python process, actual TCP regression; leaves installed HTTPcore untouched.
- `live_results.json`: exact controlled comparison output.
- `patched_process_results.json`: receipt and source patch sha256.
- `httpcore_rearm_candidate.patch`: candidate diff against HTTPcore 1.0.9; not upstream accepted or production-ready.
- `sqlalchemy_crosslayer_results.json`: actual SQLAlchemy 2.0.50 QueuePool initialization-failure protocol history, SQLite DBAPI only.
- `fault_plan_demo.json`: **synthetic** alternative cleanup-route path specification for SciPy MILP; it is NOT automatically learned from live HTTPX. `fault_plan_demo_result.json` gives objective only for supplied data.

Do not infer a general repair success rate, fault-selection completeness, or upstream acceptance from these artifacts. Run records are platform-dependent. Always check dependency versions and source scope.
