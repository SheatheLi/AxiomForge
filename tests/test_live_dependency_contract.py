"""Version-independent live integration for old and current SQLAlchemy.

Historical witnesses are tested separately under the frozen reproducer profile.
A fixed upstream library may correctly produce no failure witness.
"""
from __future__ import annotations

from axiomforge.model import Status
from axiomforge.runner import run_one
from axiomforge.targets.registry import input_for


def test_sqlalchemy_connect_observations_have_coherent_live_provenance():
    for seed in range(6):
        payload = input_for("sa-lifecycle", seed)
        observation = run_one("sa-lifecycle", payload, seed)
        assert observation.status != Status.EXECUTION_ERROR
        assert observation.admitted
        assert observation.state["errors"] == payload["attempts"]
        assert observation.state["measure"] == "pool-explicit-closer-invocations-not-server-leak"
        assert observation.left["created"] >= 1
        assert 0 <= observation.right["closed"] <= observation.left["created"]
        assert observation.status == (
            Status.CONSISTENT if observation.relation_holds
            else Status.VERIFIED_RELATIONAL_VIOLATION
        )
