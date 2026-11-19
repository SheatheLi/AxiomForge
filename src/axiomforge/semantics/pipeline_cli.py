"""Useful operator-facing CSV audit, evidence replay, and CI quality gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from .pipeline_audit import (AuditConfig, AuditError, audit_files, read_datasets,
                             minimize_witness, verify_receipt, write_receipt)
from .pipeline_report import write_html_report


def _config(args):
    return AuditConfig(join_key=args.key, measure=args.measure, segment=args.segment,
                       dropna=args.dropna, operation=args.operation,
                       max_rows=args.max_rows, max_join_rows=args.max_join_rows)


def _common(parser):
    parser.add_argument('--facts', required=True, help='Fact CSV, exact integer amounts')
    parser.add_argument('--dimensions', required=True, help='LEFT JOIN dimension CSV')
    parser.add_argument('--key', default='sku')
    parser.add_argument('--measure', default='cost')
    parser.add_argument('--segment', default='type')
    parser.add_argument('--dropna', choices=('true', 'false'), default='true')
    parser.add_argument('--operation', choices=('sum', 'count'), default='sum')
    parser.add_argument('--max-rows', type=int, default=5000)
    parser.add_argument('--max-join-rows', type=int, default=30000)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='axiomforge-audit', description='CSV pipeline semantic accounting + evidence replay')
    sub = parser.add_subparsers(dest='command', required=True)
    a = sub.add_parser('audit', help='run real pandas, SQLite and SQLAlchemy and preserve evidence')
    _common(a)
    a.add_argument('--out', required=True, help='write JSON receipt')
    a.add_argument('--html', help='write standalone HTML report')
    a.add_argument('--actual', help='materialized GROUP BY output CSV with segment and aggregate columns')
    a.add_argument('--gate', action='store_true', help='nonzero exit if record-level contract is violated')
    v = sub.add_parser('verify', help='rerun original inputs and recompute receipt')
    v.add_argument('--receipt', required=True)
    v.add_argument('--facts', required=True)
    v.add_argument('--dimensions', required=True)
    v.add_argument('--actual', help='materialized CSV, required if the original receipt used one')
    m = sub.add_parser('minimize', help='shrink fact-row witness with real backend final check')
    _common(m)
    m.add_argument('--max-replays', type=int, default=500)
    m.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            output = verify_receipt(args.receipt, args.facts, args.dimensions, actual_path=args.actual)
        else:
            args.dropna = args.dropna == 'true'
            config = _config(args)
            if args.command == 'audit':
                output = audit_files(args.facts, args.dimensions, config, actual_path=args.actual)
                write_receipt(args.out, output)
                if args.html:
                    write_html_report(output, args.html)
            else:
                facts, dims = read_datasets(args.facts, args.dimensions, config)
                output = minimize_witness(facts, dims, config, max_replays=args.max_replays)
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        if args.command == 'audit' and args.gate and output['release_gate'] != 'PASS':
            return 3
        return 0
    except (AuditError, ValueError, OSError) as exc:
        print(json.dumps({'status': 'REJECTED', 'reason': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
