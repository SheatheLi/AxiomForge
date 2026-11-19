from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from axiomforge.semantics.pipeline_audit import (
    AuditConfig, AuditError, audit, audit_files, read_datasets,
    minimize_witness, verify_receipt, write_receipt,
)
from axiomforge.semantics.pipeline_cli import main

FACTS = [{'sku':'A','cost':100}, {'sku':'B','cost':100}, {'sku':'C','cost':0}, {'sku':'D','cost':10}]
DIMS = [{'sku':'A','type':'x'}, {'sku':'A','type':'y'}, {'sku':'B','type':None},
        {'sku':'C','type':'z'}, {'sku':'C','type':'z'}, {'sku':'D','type':'x'},
        {'sku':'Z','type':'unused'}, {'sku':'Z','type':'unused'}]


def write_csv(file, fields, records):
    with file.open('w', encoding='utf8', newline='') as f:
        out = csv.DictWriter(f, fieldnames=fields)
        out.writeheader()
        out.writerows(records)


@pytest.fixture
def pair(tmp_path):
    facts = tmp_path/'facts.csv'
    dims = tmp_path/'dims.csv'
    write_csv(facts, ('sku','cost'), FACTS)
    write_csv(dims, ('sku','type'), [{'sku':v['sku'], 'type':v['type'] or ''} for v in DIMS])
    return facts, dims


def test_compensation_evades_global_aggregation_but_not_entity_invariants():
    a = audit(FACTS, DIMS, AuditConfig())
    assert a['verdict'] == 'CANCELLED_AT_GLOBAL_TOTAL'
    assert a['global']['before'] == a['global']['after'] == 210
    assert a['counts']['affected_fact_rows'] == 3
    assert not a['invariant_levels']['per_entity_pass']
    assert a['backend_cross_check']['status'] == 'THREE_EXECUTION_PATHS_AGREE'
    assert a['counts']['unreferenced_duplicate_dimension_keys'] == 1
    assert all(x['entity'].startswith('entity_') for x in a['top_affected_entities'])


def test_safe_including_unreferenced_duplicate_should_not_fail():
    d = [{'sku':'A','type':'x'}, {'sku':'B','type':'y'}, {'sku':'C','type':'z'},
         {'sku':'D','type':'w'}, {'sku':'Z','type':'other'}, {'sku':'Z','type':'other'}]
    a = audit(FACTS, d, AuditConfig())
    assert a['verdict'] == 'EXACTLY_ONCE_PRESERVED'
    assert a['counts']['unreferenced_duplicate_dimension_keys'] == 1


def test_within_entity_cancelled_but_individual_wrong():
    f = [{'sku':'A','cost':-100}, {'sku':'A','cost':100}]
    d = [{'sku':'A','type':'one'}, {'sku':'A','type':'two'}]
    a = audit(f, d, AuditConfig())
    assert a['verdict'] == 'HIDDEN_AT_ENTITY_AND_GLOBAL_TOTAL'
    assert all(a['invariant_levels'][x] for x in ('global_pass','per_entity_pass'))
    assert not a['invariant_levels']['per_record_exactly_once_pass']


def test_zero_amount_anomaly_is_detected_by_record_contract():
    a = audit([{'sku':'A','cost':0}], [{'sku':'A','type':'x'}, {'sku':'A','type':'x'}], AuditConfig())
    assert a['verdict'] == 'HIDDEN_AT_ENTITY_AND_GLOBAL_TOTAL'
    assert a['global']['residual'] == 0


def test_count_cannot_hide_zero_amount_duplication():
    a = audit([{'sku':'A','cost':0}], [{'sku':'A','type':'x'}, {'sku':'A','type':'x'}], AuditConfig(operation='count'))
    assert a['verdict'] == 'GLOBAL_VALUE_DRIFT'
    assert a['global']['before'] == 1 and a['global']['after'] == 2


def test_dropna_false_changes_allowed_contract_not_just_suppresses_alert():
    a = audit(FACTS, DIMS, AuditConfig(dropna=False))
    assert a['verdict'] == 'GLOBAL_VALUE_DRIFT'
    assert a['counts']['affected_fact_rows'] == 2


def test_dropping_group_when_null_dimension_is_not_a_sql_pandas_mismatch():
    f = [{'sku':'X','cost':9}]
    d = [{'sku':'Y','type':'irrelevant'}]
    assert audit(f, d, AuditConfig(dropna=True))['global']['after'] == 0
    assert audit(f, d, AuditConfig(dropna=False))['verdict'] == 'EXACTLY_ONCE_PRESERVED'


def test_csv_round_trip_and_stable_verification(pair, tmp_path):
    f, d = pair
    a = audit_files(f, d, AuditConfig())
    receipt = tmp_path/'receipt.json'
    write_receipt(receipt, a)
    assert verify_receipt(receipt, f, d)['verdict'] == 'CANCELLED_AT_GLOBAL_TOTAL'


def test_self_rehashed_manipulation_fails_real_replay(pair, tmp_path):
    f,d = pair
    receipt = tmp_path/'receipt.json'
    write_receipt(receipt, audit_files(f,d, AuditConfig()))
    obj = json.loads(receipt.read_text())
    obj['payload']['global']['after'] += 1
    obj['digest'] = hashlib.sha256(json.dumps(obj['payload'],sort_keys=True,ensure_ascii=False,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
    receipt.write_text(json.dumps(obj))
    with pytest.raises(AuditError, match='real cross-backend execution'):
        verify_receipt(receipt, f, d)


def test_modifying_csv_invalidates_receipt(pair, tmp_path):
    f,d = pair
    receipt = tmp_path/'receipt.json'
    write_receipt(receipt, audit_files(f,d, AuditConfig()))
    f.write_text(f.read_text()+'A,2\n')
    with pytest.raises(AuditError, match='source changed'):
        verify_receipt(receipt, f, d)


def test_rejects_unsafe_inputs(pair,tmp_path):
    f,d = pair
    f.write_text('sku,cost\nA,NaN\n')
    with pytest.raises(AuditError, match='noninteger'):
        read_datasets(f,d,AuditConfig())
    f.write_text('sku,cost\n,100\n')
    with pytest.raises(AuditError, match='NULL/empty'):
        read_datasets(f,d,AuditConfig())
    with pytest.raises(AuditError, match='unsafe column'):
        AuditConfig(join_key='x; DROP TABLE f')


def test_budget_fails_closed():
    with pytest.raises(AuditError, match='execution budget'):
        audit(FACTS,DIMS,AuditConfig(max_join_rows=4))


def test_inconsistent_backends_fail_closed(monkeypatch):
    from axiomforge.semantics import pipeline_audit as mod
    old = mod._backend_execute
    def bad(backend, facts, dims, conf):
        value = old(backend,facts,dims,conf)
        if backend == 'sqlalchemy':
            return (value[0],value[1]+1,*value[2:])
        return value
    monkeypatch.setattr(mod,'_backend_execute',bad)
    with pytest.raises(AuditError, match='INCONCLUSIVE'):
        audit(FACTS,DIMS,AuditConfig())


def test_minimizer_keeps_cross_entity_compensation():
    report = minimize_witness(FACTS,DIMS,AuditConfig())
    assert report['witness_rows'] == 2
    assert set(report['retained_fact_indices']) == {0,1}
    assert report['three_backend_verdict'] == 'CANCELLED_AT_GLOBAL_TOTAL'


def test_minimizer_within_entity():
    facts=[{'sku':'A','cost':-100}, {'sku':'A','cost':100}, {'sku':'B','cost':4}]
    dims=[{'sku':'A','type':'a'}, {'sku':'A','type':'a'}, {'sku':'B','type':'b'}]
    report=minimize_witness(facts,dims,AuditConfig())
    assert report['witness_rows'] == 2
    assert report['target'] == 'HIDDEN_AT_ENTITY_AND_GLOBAL_TOTAL'


def test_cli_generates_receipt_html_gate_and_verifies(pair,tmp_path):
    f,d=pair
    receipt=tmp_path/'my_receipt.json'
    html=tmp_path/'my_report.html'
    opts=['--facts',str(f),'--dimensions',str(d),'--out',str(receipt),'--html',str(html)]
    assert main(['audit',*opts,'--gate']) == 3
    assert receipt.is_file() and html.is_file()
    assert '<script' not in html.read_text()
    assert 'CANCELLED_AT_GLOBAL_TOTAL' in html.read_text()
    assert main(['verify','--receipt',str(receipt),'--facts',str(f),'--dimensions',str(d)])==0
    minimized=tmp_path/'minimal.json'
    assert main(['minimize','--facts',str(f),'--dimensions',str(d),'--out',str(minimized)])==0
    assert json.loads(minimized.read_text())['witness_rows']==2


def test_exact_string_key_not_conflated_with_null():
    a=audit([{'sku':'0','cost':1}], [{'sku':'0','type':'x'}],AuditConfig())
    assert a['verdict']=='EXACTLY_ONCE_PRESERVED'
