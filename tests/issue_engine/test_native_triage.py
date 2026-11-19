"""Behavioral and integration tests for real-source native failure triage."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import pytest
from axiomforge.issue_engine import runtime_triage as rt


def scenario(n, *, stray=False):
    rows = [{"iteration":i, "active_pool_connections": min(i,3), "pending_requests":0}
            for i in range(1,min(n,3)+1)]
    if n >= 4:
        rows.append({"iteration":4,"inconclusive_or_blocked":"TimeoutError"})
    if stray:
        rows[0]["pending_requests"] = 1
    return {"single":{"rows":[{"iteration":1,"active_pool_connections":0}]},
            "double":{"rows":rows,"cleanup_events":[{"stage":"HTTP11ConnectionByteStream.aclose",
                         "event":"CANCELLED_DURING_CLEANUP","closed_flag":True}],
                      "protocol_summary":{"finding_kinds":["STRANDED_ACTIVE_POOL_CONNECTION"]}}}


def test_actual_outcome_oracle_and_negative_controls():
    ok=rt.symptom_evidence(scenario(4),"pool_exhaustion")
    assert ok["present"] and ok["first_iteration"]==4
    assert ok["early_committed_close_interrupted"]==1
    assert not rt.symptom_evidence(scenario(3),"pool_exhaustion")["present"]
    assert rt.symptom_evidence(scenario(2),"stranded_active")["present"]
    assert not rt.symptom_evidence(scenario(1,stray=True),"stranded_active")["present"]
    assert not rt.symptom_evidence({"double":{"rows":[{"iteration":1,
        "inconclusive_or_blocked":"TimeoutError"}],"cleanup_events":[]}},"pool_exhaustion")["present"]
    with pytest.raises(ValueError):rt.symptom_evidence(scenario(4),"other")


def test_bounded_prefix_shrinks_to_four_and_compares_fresh_patch(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(rt,"target_identity",lambda:{"target":"pinned"})
    def fake_native(script,n,dir,label,timeout):
        seen.append((script.name,n,label))
        if script==rt.PATCHED:
            return {"repetitions":n,"independent_python_process":True,"patch_dry_run":True,
                    "double_active":[0]*n,"double_blocked":0,"applied_diff_sha256":"known-patch"}
        return scenario(n)
    monkeypatch.setattr(rt,"_native",fake_native)
    report=rt.triage(tmp_path,repetitions=6,confirmations=2)
    assert report["status"]=="REPRODUCED"
    assert report["prefix_reduction"]["minimum_repetitions"]==4
    assert [x["repetitions"] for x in report["prefix_reduction"]["attempts"]]==[1,2,3,4]
    assert all(x["present"] for x in report["prefix_reduction"]["confirmation_runs"])
    assert report["independent_patch_comparison"]["eliminated_observed_symptom"]
    assert seen[-1][0]==rt.PATCHED.name
    assert (tmp_path/"triage.json").is_file()


def test_no_false_success_for_nonreproduction_and_intermittent(tmp_path,monkeypatch):
    monkeypatch.setattr(rt,"target_identity",lambda:{"pinned":True})
    monkeypatch.setattr(rt,"_native",lambda *args,**kw:scenario(2))
    absent=rt.triage(tmp_path/"absent",repetitions=3)
    assert absent["status"]=="NOT_REPRODUCED" and absent["replay"] is None
    calls={"confirm":0}
    def flaky(script,n,dir,label,timeout):
        if label.startswith('confirm_'):
            calls["confirm"]+=1
            return scenario(2)
        return scenario(n)
    monkeypatch.setattr(rt,"_native",flaky)
    intermittent=rt.triage(tmp_path/"flaky",repetitions=5)
    assert intermittent["status"]=="INTERMITTENT"
    assert intermittent["independent_patch_comparison"] is None
    assert calls["confirm"]==2


def test_replay_enforces_identity_then_runs_native(tmp_path,monkeypatch):
    monkeypatch.setattr(rt,"target_identity",lambda:{"version":"same"})
    record={"schema":"axiomforge.native-issue-triage.v1","target":{"version":"different"},
            "symptom":"stranded_active","prefix_reduction":{"minimum_repetitions":1}}
    p=tmp_path/"triage.json";p.write_text(json.dumps(record),encoding="utf-8")
    monkeypatch.setattr(rt,"_native",lambda *a,**k:pytest.fail("Should not execute on mismatch"))
    assert rt.replay(p,tmp_path/"mismatch.json")["status"]=="TARGET_MISMATCH"
    record["target"]={"version":"same"}
    p.write_text(json.dumps(record),encoding="utf-8")
    monkeypatch.setattr(rt,"_native",lambda *a,**k:scenario(1))
    result=rt.replay(p,tmp_path/"replayed.json")
    assert result["status"]=="REPRODUCED"
    assert result["source_triage_sha256"]==rt._digest(p)


@pytest.mark.parametrize('repetitions,checks',[(-1,1),(13,1),(1,0),(1,4)])
def test_reject_unsupported_bounds(tmp_path,repetitions,checks):
    with pytest.raises(ValueError):rt.triage(tmp_path,repetitions=repetitions,confirmations=checks)


def test_normal_cli_accepts_reliability_model(capsys):
    from axiomforge.issue_engine.reliability_cli import main
    assert main(['model-check','--attempts','2'])==0
    assert 'shortest_counterexample' in capsys.readouterr().out


@pytest.mark.slow
def test_native_end_to_end_and_replay(tmp_path):
    try:rt.target_identity()
    except rt.NativeRunFailure as exc:pytest.skip(str(exc))
    report=rt.triage(tmp_path,symptom="pool_exhaustion",repetitions=5,confirmations=2)
    assert report["status"]=="REPRODUCED"
    assert report["prefix_reduction"]["minimum_repetitions"]==4
    assert report["independent_patch_comparison"]["eliminated_observed_symptom"] is True
    assert rt.replay(tmp_path/"triage.json",tmp_path/"replay.json")["status"]=="REPRODUCED"
