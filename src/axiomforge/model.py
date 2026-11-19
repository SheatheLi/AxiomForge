"""Typed public interfaces, source authority and conservative verdicts."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any

class Status(str, Enum):
    VERIFIED_RELATIONAL_VIOLATION = "VERIFIED_RELATIONAL_VIOLATION"
    CONSISTENT = "CONSISTENT"
    ABSTAIN = "ABSTAIN"
    EXECUTION_ERROR = "EXECUTION_ERROR"

class Authority(str, Enum):
    DOCUMENTED = "DOCUMENTED"  # independent of subject's observed output
    DOMAIN_DEFINITION = "DOMAIN_DEFINITION"  # explicit model; may itself be mistaken
    EMPIRICAL_ONLY = "EMPIRICAL_ONLY"  # never independently certifies a defect

@dataclass(frozen=True)
class Contract:
    id: str
    title: str
    statement: str
    applicability: str
    authority: Authority
    authority_url: str
    target: str
    bound: str = ""

    def serial(self) -> dict[str, Any]:
        return asdict(self) | {"authority": self.authority.value}

@dataclass(frozen=True)
class Witness:
    target: str
    contract: str
    seed: int
    input: dict[str, Any]
    state: dict[str, Any]
    left: Any
    right: Any
    relation_holds: bool | None
    admitted: bool
    admission_evidence: dict[str, Any]
    status: Status
    failure_reason: str
    environment: dict[str, str]
    known_issue: str | None = None

    def serial(self) -> dict[str, Any]:
        obj = asdict(self)
        obj["status"] = self.status.value
        return obj

@dataclass(frozen=True)
class SearchResult:
    contract: Contract
    candidates: int
    evaluations: int
    witnesses: list[Witness]
    elapsed_seconds: float
    observations: list[Witness] = field(default_factory=list)

    def serial(self) -> dict[str, Any]:
        return {"contract":self.contract.serial(),"candidates":self.candidates,
                "evaluations":self.evaluations,"witnesses":[x.serial() for x in self.witnesses],
                "elapsed_seconds":self.elapsed_seconds,
                "observations":[x.serial() for x in self.observations]}
