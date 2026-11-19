"""Monotone identity-accounting oracle for resource construction/retirement.

The oracle tracks explicitly observed close operations, NOT eventual garbage
collection, OS handles or server sessions. Uses a monotonic generation token
rather than relying on id(resource), which Python may reuse after collection.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OwnershipOracle:
    created: int = 0
    retired: int = 0
    _generations: dict[int, list[int]] = field(default_factory=lambda:defaultdict(list))
    _outstanding: set[int] = field(default_factory=set)
    violations: list[str] = field(default_factory=list)

    def created_resource(self, resource: Any) -> int:
        self.created += 1
        generation=self.created
        self._generations[id(resource)].append(generation)
        self._outstanding.add(generation)
        return generation

    def retired_resource(self, resource: Any) -> int | None:
        key=id(resource)
        pending=self._generations.get(key)
        if not pending:
            self.violations.append(f'unknown-or-duplicate-retire:{key}')
            return None
        generation=pending.pop()
        self._outstanding.remove(generation)
        self.retired += 1
        if not pending:self._generations.pop(key,None)
        return generation

    def snapshot(self) -> dict[str, Any]:
        return {'created':self.created,'retired':self.retired,
                'not_retired':self.created-self.retired,
                'unclosed_generations':sorted(self._outstanding),
                'violations':list(self.violations)}
