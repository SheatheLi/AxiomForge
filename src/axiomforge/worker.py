"""Allowlisted subprocess boundary for fault observations; no arbitrary code imports."""
from __future__ import annotations
import json,sys
from axiomforge.targets.registry import execute,CONTRACTS

def main():
    req=json.loads(sys.stdin.read(2_000_000))
    name=req["target"]
    if name not in CONTRACTS:raise ValueError("target not allowlisted")
    print(json.dumps(execute(name,req["payload"],req.get("control")),sort_keys=True))
if __name__=="__main__":main()
