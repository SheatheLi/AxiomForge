"""Cross-layer resource ownership protocol checker (experimental).

An event-sourced runtime verifier inspired by history checking and fault
lineage research. It checks *observed* lifecycle contracts, not OS handles.

The trace adapter must provide actual allocation, transfer, transaction,
return and close events. Uninstrumented driver state remains unknown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


VALID = frozenset({"allocate", "transfer", "checkout", "checkin", "tx_begin", "tx_end",
                   "retire", "scope_abort", "scope_finish", "pool_observe", "pool_quiescence"})


@dataclass
class Resource:
    token: str
    generation: int
    owner: str
    physical: str = "OPEN"
    transaction_depth: int = 0
    history: list[int] = field(default_factory=list)


@dataclass
class Finding:
    kind: str
    token: str
    generation: int | None
    at: int
    message: str
    witness: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "resource": self.token, "generation": self.generation,
                "at_event": self.at, "message": self.message,
                "causal_witness": self.witness}


class ProtocolChecker:
    """Replay explicit events in order and verify resource conservation.

    Scopes identify an allocation context. A resource transferred to a live
    pool owner remains valid even if still physically OPEN. An aborted scope
    retaining an unretired resource is an orphan. `tx_begin` / `checkin`
    detect a different bug: dirty transaction re-entry into a pool.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.active: dict[str, Resource] = {}
        self.generations: dict[str, int] = {}
        self.scopes: dict[str, str] = {}
        self.findings: list[Finding] = []
        self._dedupe: set[tuple[str, str, int | None]] = set()

    def _issue(self, kind: str, token: str, generation: int | None, at: int,
               message: str) -> None:
        key = (kind, token, generation)
        if key not in self._dedupe:
            self._dedupe.add(key)
            self.findings.append(Finding(kind, token, generation, at, message))

    def emit(self, kind: str, *, resource: str = "", owner: str = "",
             scope: str = "", pool: str = "", available: int | None = None,
             active: bool | None = None, pending_requests: int | None = None,
             note: str = "") -> None:
        if kind not in VALID:
            raise ValueError(f"Unknown event kind: {kind}")
        if kind in {"allocate", "transfer", "checkout", "checkin", "tx_begin", "tx_end", "retire"} and not resource:
            raise ValueError(f"{kind} requires resource")
        if kind in {"allocate", "transfer", "checkout", "checkin"} and not owner:
            raise ValueError(f"{kind} requires owner")
        if kind in {"scope_abort", "scope_finish"} and not scope:
            raise ValueError(f"{kind} requires scope")
        if kind == "pool_observe" and (not pool or available is None or available < 0):
            raise ValueError("pool_observe requires pool and nonnegative available")
        if kind == "pool_quiescence" and (not pool or not resource or
              not isinstance(active, bool) or not isinstance(pending_requests,int) or pending_requests < 0):
            raise ValueError("pool_quiescence requires pool/resource/active and nonnegative pending_requests")
        event = {"seq": len(self.events), "kind": kind}
        for key, val in (("resource", resource), ("owner", owner), ("scope", scope),
                         ("pool", pool), ("available", available), ("active", active),
                         ("pending_requests", pending_requests), ("note", note)):
            if val is not None and val != "":
                event[key] = val
        self.events.append(event)
        at = event["seq"]
        if kind.startswith("scope_"):
            self.scopes[scope] = "ABORTED" if kind == "scope_abort" else "FINISHED"
            return
        if kind == "pool_observe":
            return
        if kind == "pool_quiescence":
            if active and pending_requests==0:
                self._issue("STRANDED_ACTIVE_POOL_CONNECTION", resource,
                            self.generations.get(resource), at,
                            "Pool connection remains active after observed requests drained")
            return
        if kind == "allocate":
            if resource in self.active:
                self._issue("DUPLICATE_ALLOCATION", resource, self.active[resource].generation,
                            at, "Resource token was allocated again before retirement")
                return
            generation = self.generations.get(resource, 0) + 1
            self.generations[resource] = generation
            self.active[resource] = Resource(resource, generation, owner, history=[at])
            return
        rec = self.active.get(resource)
        if rec is None:
            self._issue("UNKNOWN_RESOURCE", resource, self.generations.get(resource), at,
                        f"{kind} without an active resource")
            return
        rec.history.append(at)
        if kind == "transfer" or kind == "checkout":
            rec.owner = owner
        elif kind == "checkin":
            if rec.transaction_depth:
                self._issue("DIRTY_POOL_RETURN", resource, rec.generation, at,
                            f"Returned to {owner} with {rec.transaction_depth} open transactions")
            rec.owner = owner
        elif kind == "tx_begin":
            rec.transaction_depth += 1
        elif kind == "tx_end":
            if rec.transaction_depth == 0:
                self._issue("TRANSACTION_UNDERFLOW", resource, rec.generation, at,
                            "tx_end without a matching tx_begin")
            else:
                rec.transaction_depth -= 1
        elif kind == "retire":
            if rec.transaction_depth:
                self._issue("CLOSE_WITH_OPEN_TX", resource, rec.generation, at,
                            "Resource retired while transaction state remained active")
            rec.physical = "RETIRED"
            del self.active[resource]

    def finalize(self) -> dict[str, Any]:
        """Finalize a finite observation window; no unseen-release assumptions."""
        at = len(self.events)
        for rec in self.active.values():
            if self.scopes.get(rec.owner) in {"ABORTED", "FINISHED"}:
                self._issue("ORPHANED_RESOURCE", rec.token, rec.generation, at,
                            f"Open resource retained by {self.scopes[rec.owner].lower()} scope {rec.owner}")
        for finding in self.findings:
            # Causal projection: actual events for this resource and owner-scope
            # exit events. This is not a proof of globally minimum witness.
            rec_events = [event for event in self.events if event.get("resource") == finding.token]
            scopes = {event.get("owner") for event in rec_events if event.get("owner")}
            first_related = [event for event in self.events if event["kind"].startswith("scope_")
                             and event.get("scope") in scopes]
            relevant = {e["seq"]: e for e in rec_events + first_related}
            finding.witness = [relevant[i] for i in sorted(relevant)]
        return {
            "schema": "axiomforge.resource-protocol.v1",
            "events": len(self.events),
            "active_resources": len(self.active),
            "findings": [f.to_dict() for f in self.findings],
            "finding_kinds": sorted({f.kind for f in self.findings}),
            "limits": ("Only instrumented events are checked. Remaining open resources owned "
                       "by a healthy pool are not leaks. No server-side sockets or "
                       "transaction state is inferred without adapter evidence."),
        }

    @classmethod
    def replay(cls, events: Iterable[Mapping[str, Any]]) -> tuple["ProtocolChecker", dict[str, Any]]:
        checker = cls()
        for event in events:
            arguments = {key: value for key, value in event.items()
                         if key in {"resource", "owner", "scope", "pool", "available", "active", "pending_requests", "note"}}
            checker.emit(str(event["kind"]), **arguments)
        return checker, checker.finalize()
