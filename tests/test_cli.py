from __future__ import annotations
import json
from axiomforge.cli import main
from axiomforge.bench import compare

def test_doctor(capsys):
    assert main(['doctor'])==0
    assert 'all_expected' in capsys.readouterr().out

def test_run_verify_report(tmp_path):
    output=str(tmp_path/'a.json');html=str(tmp_path/'report.html')
    assert main(['run','--budget','3','--max-rows','2','--out',output])==0
    assert main(['verify',output,'--rerun'])==0
    assert main(['report',output,'--out',html])==0
    assert (tmp_path/'report.html').read_text().startswith('<!doctype html>')

def test_weak_benchmark_accounting():
    r=compare(per_target=12,seeds=2,search_budget=4)
    assert len(r['raw'])==8
    assert all(x['budget']==4 for x in r['raw'])
