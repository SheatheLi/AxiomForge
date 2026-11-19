#!/usr/bin/env python3
"""Offline fact-grain SQL demo entry point for the bundled example fixture.

The script intentionally reports REGRESSION as an observed unsafe baseline;
that outcome is a successful *detection*, not a successful SQL candidate.
It never writes to the fixture database and never fabricates review results.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from axiomforge.sql_review import ReviewError, inspect, make_html, save_receipt
from scripts.create_sql_review_demo import create_demo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the bundled read-only SQL fact-grain demo")
    parser.add_argument("--demo", action="store_true", help="run the documented bundled SQLite example")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "results", help="directory for report.html and JSON receipt")
    args = parser.parse_args(argv)
    if not args.demo:
        parser.error("Specify --demo for the bundled offline demonstration")
    fixture = ROOT / "examples" / "sql_review"
    db = create_demo()
    before = fixture / "before.sql"
    after = fixture / "after.sql"
    if not before.exists() or not after.exists():
        raise FileNotFoundError("Bundled SQL review example is incomplete")
    report = inspect(db, "orders", "id", "price", [before, after])
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    save_receipt(out / "sample.json", report)
    make_html(out / "sample.html", report)
    # Report the observed distinction between the original and corrected query.
    verdicts = [part['status'] for part in report['evaluations']]
    if report['status'] != 'REGRESSION' or verdicts != ['PASS', 'BLOCK']:
        print(f"Demo verification failed: {report['status']} {verdicts}", file=sys.stderr)
        return 2
    print(f"Demo: {report['status']} ({verdicts[0]} -> {verdicts[-1]})")
    print(f"Evidence: {out / 'sample.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
