$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
python -m pip install -e ".[test,research]"
python -m pytest -q
python -m axiomforge doctor
python -m axiomforge run --budget 32 --out artifacts/local.json
python -m axiomforge verify artifacts/local.json --rerun
python -m axiomforge report artifacts/local.json --out artifacts/local.html
python -m axiomforge prove-model
