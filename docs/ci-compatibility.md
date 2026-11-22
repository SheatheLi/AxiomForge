# CI: live compatibility versus historical issue replay

**These are two different verification claims.**

## Historical witnesses — pinned pre-fix profile

The `historical-replay` job runs all contract smoke tests with:

- Python 3.11
- NumPy **2.3.5**
- pandas **2.2.3**
- SQLAlchemy **2.0.50**

Three tests marked `historical_replay` require historical SQLAlchemy failure witnesses. They verify witness provenance, the historical failure count, and rejection of a forged status; these claims make sense only in a frozen environment which actually exhibits the upstream defect. The full historical test set runs: the three assertions have **not** been removed or relaxed.

## Current dependencies — compatibility profile

The `current-compatibility` job installs current versions permitted by `pyproject.toml`. It executes all version-independent smoke tests, plus an explicit live SQLAlchemy connection/cleanup consistency test. It excludes **only** the following version-dependent historical assertions via the declared `historical_replay` marker:

- `test_three_independent_contracts_exposed`
- `test_all_attempts_provenance_saved_and_replayed`
- `test_forged_consistent_observation_rejected_even_if_rehashed`

A fixed SQLAlchemy version may correctly yield **zero** failure witnesses. It would be unsound to treat the absence of a known historical defect as a failure of present-day compatibility, or to claim a historical bug was discovered in the current library.

## Claims and limits

A green current-compatibility job does **not** mean historical failures still reproduce. A green historical-replay job does **not** prove the latest libraries are defective. Neither smoke job is a full proof of the toolchain, and the historical 437-pass/10-skip receipt is a separate recorded run, not a fresh CI test count.

To reproduce locally with existing installed dependencies, use the relevant commands in `.github/workflows/smoke.yml`. Do not upgrade the historical profile dependencies without revalidating its witness contracts.
