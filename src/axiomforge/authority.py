"""No result from a subject is allowed to certify its own normative contract."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from axiomforge.model import Authority, Contract

@dataclass(frozen=True)
class Admission:
    permitted: bool
    reason: str
    evidence: dict[str, Any]

def check_authority(contract: Contract, context: dict[str, Any]) -> Admission:
    checks = context.get("checks", {})
    if contract.authority == Authority.EMPIRICAL_ONLY:
        return Admission(False,"subject-derived relation has no independent semantic authority",{"authority":contract.authority.value})
    if not contract.authority_url and contract.authority != Authority.DOMAIN_DEFINITION:
        return Admission(False,"missing independent source locator",{"authority":contract.authority.value})
    if checks.get("semantic_change", False):
        return Admission(False,"perturbation changes the declared semantic input",{"checks":checks})
    if not checks.get("inputs_equivalent", False):
        return Admission(False,"input-equivalence proof obligation not met",{"checks":checks})
    if not checks.get("domain_valid", False):
        return Admission(False,"domain applicability proof obligation not met",{"checks":checks})
    return Admission(True,"independent source and scenario-specific proof obligations satisfied",
                     {"authority":contract.authority.value,"source":contract.authority_url,"checks":checks})
