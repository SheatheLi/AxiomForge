"""Bounded explicit-state model checking of async cleanup commit ordering.

The model abstracts a *single* streamed connection and at most two close
attempts. Its counterexamples are mathematical properties of the abstraction,
not a correctness proof for every HTTPcore execution or async scheduler.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from typing import Any

@dataclass(frozen=True)
class State:
    phase: str = "READY"
    flag: bool = False
    connection: str = "ACTIVE"
    attempts: int = 0
    interruptions: int = 0


def transitions(s: State, *, strategy: str, attempts_limit: int, max_interruptions: int) -> list[tuple[str, State]]:
    if strategy not in ("early-commit", "defer-commit"):
        raise ValueError("Unsupported strategy")
    if s.phase == "READY":
        if s.flag or s.attempts >= attempts_limit or s.connection == "IDLE":
            return [("return", State("DONE", s.flag, s.connection, s.attempts, s.interruptions))]
        return [("enter_close", State("WAIT_LOCK", strategy == "early-commit", s.connection,
                                      s.attempts+1, s.interruptions))]
    if s.phase == "WAIT_LOCK":
        # The lock-await site is the observed interruptible boundary.
        options = [("lock_acquired_and_cleanup_completed", State("DONE", True, "IDLE",
                                                         s.attempts, s.interruptions))]
        if s.interruptions < max_interruptions:
            options.append(("cancel_at_lock_await", State("READY", s.flag, s.connection,
                                            s.attempts, s.interruptions+1)))
        return options
    return []


def verify_bounded(strategy: str, *, attempts_limit: int = 2, max_interruptions: int = 1) -> dict[str, Any]:
    if attempts_limit < 1 or attempts_limit > 10:
        raise ValueError("attempts_limit must be in 1..10")
    if max_interruptions < 0 or max_interruptions > attempts_limit:
        raise ValueError("max_interruptions must be in 0..attempts_limit")
    start = State()
    queue = deque([(start, [start], [])])
    visited = {start}
    terminals: list[State] = []
    counterexample = None
    while queue:
        state, route, actions = queue.popleft()
        if state.phase == "DONE":
            terminals.append(state)
            if state.connection == "ACTIVE" and state.interruptions > 0 and counterexample is None:
                counterexample = {"states": [asdict(v) for v in route], "actions": actions}
            continue
        for act, next_state in transitions(state, strategy=strategy, attempts_limit=attempts_limit,
                                           max_interruptions=max_interruptions):
            if next_state not in visited:
                visited.add(next_state)
                queue.append((next_state, route+[next_state], actions+[act]))
    return {"strategy": strategy, "attempts_limit": attempts_limit,
            "max_interruptions":max_interruptions,
            "state_count": len(visited), "terminal_count": len(terminals),
            "bad_terminal_count": sum(x.connection == "ACTIVE" and x.interruptions>0 for x in terminals),
            "shortest_counterexample": counterexample,
            "scope": ("Finite abstraction only. One interruption with two retries is repairable "
                      "with deferred commit; two exhausted attempts may still leak. "
                      "Not a production scheduler proof.")}
