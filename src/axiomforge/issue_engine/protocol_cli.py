"""Read-only JSON protocol checker; no execution of supplied source files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from .resource_protocol import ProtocolChecker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-layer resource lifecycle event verification")
    parser.add_argument("history", type=Path, help="JSON file with list of lifecycle events or {events:[...]}")
    parser.add_argument("--out", type=Path, help="Write structured findings JSON")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.history.read_text(encoding="utf-8"))
        events = data["events"] if isinstance(data, dict) else data
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise ValueError("history must contain a list of event dictionaries")
        _, result = ProtocolChecker.replay(events)
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output, encoding="utf-8")
        print(output, end="")
        return 2 if result["findings"] else 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Invalid protocol trace: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
