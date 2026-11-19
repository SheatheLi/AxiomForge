from __future__ import annotations
import copy,json,hashlib
import pytest
from axiomforge.model import Authority,Contract
from axiomforge.authority import check_authority
from axiomforge.evidence import write,verify,canonical
from axiomforge.suite import run_suite

def sample_contract(authority):
    return Contract('test','t','s','pre',authority,'https://example.com','test')

def test_empirical_subject_witness_not_admitted():
    c=sample_contract(Authority.EMPIRICAL_ONLY)
    assert not check_authority(c,{'checks':{'inputs_equivalent':True,'domain_valid':True}}).permitted

def test_missing_validity_rejected():
    c=sample_contract(Authority.DOCUMENTED)
    assert not check_authority(c,{'checks':{'domain_valid':False,'inputs_equivalent':True}}).permitted

def test_semantic_change_cannot_be_labelled_irrelevant():
    c=sample_contract(Authority.DOMAIN_DEFINITION)
    assert not check_authority(c,{'checks':{'domain_valid':True,'inputs_equivalent':False,'semantic_change':True}}).permitted

def test_bundle_roundtrip_with_real_sut_rerun(tmp_path):
    p=tmp_path/'evidence.json';write(p,run_suite(budget=5,max_rows=2))
    verdict=verify(p,rerun=True)
    assert verdict['status']=='VERIFIED_WITH_REAL_LIBRARY_REPLAY'

def test_digest_tamper_rejected(tmp_path):
    p=tmp_path/'e.json';write(p,run_suite(budget=3,max_rows=2))
    data=json.loads(p.read_text())
    data['payload']['searches'][0]['witnesses'][0]['status']='CONSISTENT'
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='digest'):verify(p)

def test_forged_rehashed_row_rejected_by_replay(tmp_path):
    p=tmp_path/'e.json';write(p,run_suite(budget=3,max_rows=2))
    data=json.loads(p.read_text());w=data['payload']['searches'][0]['witnesses'][0]
    w['left']=[987654]
    data['payload_sha256']=hashlib.sha256(canonical(data['payload'])).hexdigest()
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='replay mismatch'):verify(p,rerun=True)

def test_rehashed_false_claim_rejected_by_structure(tmp_path):
    p=tmp_path/'e.json';write(p,run_suite(budget=3,max_rows=2))
    data=json.loads(p.read_text());data['payload']['guard']['false_positives']=99
    data['payload_sha256']=hashlib.sha256(canonical(data['payload'])).hexdigest()
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='unsound guard'):verify(p,rerun=False)

def test_three_independent_contracts_exposed():
    p=run_suite(budget=4,max_rows=2)
    assert len(p['searches'])==3
    assert all(s['witnesses'] for s in p['searches'])
    assert len({s['contract']['target'] for s in p['searches']})==2

def test_host_platform_metadata_can_differ_but_library_versions_cannot(tmp_path):
    p=tmp_path/'e.json';write(p,run_suite(budget=3,max_rows=2))
    obj=json.loads(p.read_text())
    for group in obj['payload']['searches']:
        for row in group['witnesses']:
            row['environment']['platform']='DifferentOS'
            row['environment']['python']='3.99'
    obj['payload_sha256']=hashlib.sha256(canonical(obj['payload'])).hexdigest()
    p.write_text(json.dumps(obj))
    assert verify(p,rerun=True)['status']=='VERIFIED_WITH_REAL_LIBRARY_REPLAY'
    obj=json.loads(p.read_text())
    obj['payload']['searches'][0]['witnesses'][0]['environment']['pandas']='0.0.0'
    obj['payload_sha256']=hashlib.sha256(canonical(obj['payload'])).hexdigest()
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='dependency version drift'):
        verify(p,rerun=True)

def test_independent_guard_rejects_adapter_self_authorizing_sort_true():
    from axiomforge.admissibility import independent_checks
    p={'keys':[2,1],'values':[3,4],'sort':False}
    checked=independent_checks('group-sort',p,{'sort':True},{})
    assert not checked['domain_valid']

def test_independent_guard_rejects_adapter_self_authorizing_mutated_world():
    from axiomforge.admissibility import independent_checks
    checked=independent_checks('cow-alias',{'keys':[1,2],'values':[3,4],'mutated_key':100},
         {'semantic_change':True},{})
    assert not checked['inputs_equivalent'] and checked['semantic_change']

def test_independent_guard_does_not_trust_unknown_target():
    from axiomforge.admissibility import independent_checks
    assert not independent_checks('unknown',{},None,{})['domain_valid']


def test_all_attempts_provenance_saved_and_replayed(tmp_path):
    p=tmp_path/'raw.json';write(p,run_suite(budget=8,max_rows=2))
    data=json.loads(p.read_text())
    assert all(len(group['observations'])==8 for group in data['payload']['searches'])
    assert len(data['payload']['searches'][2]['witnesses'])==6 # repeated seed inputs deduplicated
    result=verify(p,rerun=True)
    assert result['raw_external_attempts']==24
    assert result['raw_violation_observations']>=20

def test_deleted_observation_rejected_even_if_digest_recomputed(tmp_path):
    p=tmp_path/'raw.json';write(p,run_suite(budget=4,max_rows=2))
    obj=json.loads(p.read_text())
    obj['payload']['searches'][2]['observations'].pop()
    obj['payload_sha256']=hashlib.sha256(canonical(obj['payload'])).hexdigest()
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='raw observations'):
        verify(p,rerun=True)

def test_forged_consistent_observation_rejected_even_if_rehashed(tmp_path):
    p=tmp_path/'raw.json';write(p,run_suite(budget=4,max_rows=2))
    obj=json.loads(p.read_text());obj['payload']['searches'][2]['observations'][0]['status']='CONSISTENT'
    obj['payload_sha256']=hashlib.sha256(canonical(obj['payload'])).hexdigest()
    p.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match='replay mismatch|published witnesses'):
        verify(p,rerun=True)
