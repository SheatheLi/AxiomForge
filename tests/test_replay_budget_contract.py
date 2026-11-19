"""No extra uncounted replay when ddmin reaches its execution budget."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import pytest
import axiomforge.minimize as m
from axiomforge.model import Status

def test_budget_strict_includes_initial_and_no_duplicate(monkeypatch):
    calls=[]
    def fake(target,payload):
        calls.append(tuple(payload['keys']))
        return SimpleNamespace(status=Status.VERIFIED_RELATIONAL_VIOLATION)
    monkeypatch.setattr(m,'run_one',fake)
    case={'keys':[1,2,3,4,5],'values':[1,2,3,4,5]}
    for budget in (1,2,3,4,9):
        calls.clear()
        result=m.ddmin('irrelevant',case,budget)
        assert result.replays==len(calls)<=budget
        assert result.preserved
        assert result.budget_exhausted == (result.replays>=budget)
        assert len(result.minimized['keys'])==len(result.minimized['values'])

def test_reject_bad_contract(monkeypatch):
    case={'keys':[1,2],'values':[2]}
    with pytest.raises(ValueError):m.ddmin('any',case,10)
    with pytest.raises(ValueError):m.ddmin('any',{'keys':[1],'values':[1]},0)
