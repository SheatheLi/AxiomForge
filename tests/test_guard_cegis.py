from __future__ import annotations
import pytest
from axiomforge.targets.partition import bounded_cases,actual,independent_model,feature_values,PartitionInput
from axiomforge.guard_cegis import derive,matches,guard_from_serial,search_grammar

def test_exhaustive_domain_count():
    assert sum(1 for x in bounded_cases(3))==1638

def test_real_library_matches_independent_reference():
    for x in bounded_cases(2):assert actual(x)==independent_model(x)

def test_cegis_learns_guard_and_finite_domain_proves_soundness():
    result=derive(3)
    assert result.total_domain_cases==1638
    assert result.cegis_rounds>=2
    assert result.counterexamples
    assert result.false_positives==0
    assert result.false_negatives==0
    assert result.semantic_reference_disagreements==0
    guard=guard_from_serial(result.serial())
    for x in bounded_cases(3):
        if matches(guard,feature_values(x)):
            assert independent_model(x)[-1]

def test_out_of_domain_has_no_automatic_proof():
    # A JSON description documents max_rows=3; never silently promote to 4+
    assert derive(2).domain["max_rows"]==2

def test_bad_grammar_fail_closed():
    with pytest.raises(ValueError):search_grammar(3,2)
    with pytest.raises(ValueError):list(bounded_cases(5))

def test_negative_missing_key_nonzero_sum():
    x=PartitionInput((None,"A"),(3,2),True)
    assert actual(x)==(5,2,False)

def test_zero_sum_missing_rows_can_be_admitted():
    x=PartitionInput((None,None,"A"),(3,-3,2),True)
    assert actual(x)==(2,2,True)

def test_guard_ablation_quantifies_hand_engineered_feature():
    fully=derive(3,include_sum_feature=True)
    weak=derive(3,include_sum_feature=False)
    assert fully.false_positives==weak.false_positives==0
    assert fully.accepted>weak.accepted
    assert weak.false_negatives>0

def test_admission_is_bounded_and_abstains_outside_domain():
    from axiomforge.guard_cegis import assess_bounded
    proof=derive(2).serial()
    assert assess_bounded(proof,PartitionInput(("A","B"),(1,0),True))['status']=='ADMITTED_CONSISTENT'
    assert assess_bounded(proof,PartitionInput(("A","B","A"),(1,0,0),True))['status']=='ABSTAIN_OUTSIDE_CERTIFIED_DOMAIN'
    assert assess_bounded(proof,PartitionInput((None,"A"),(1,1),True))['status']=='ABSTAIN_GUARD_NOT_MET'
    assert assess_bounded(proof,PartitionInput(("A",),(2,),True))['status']=='ABSTAIN_OUTSIDE_CERTIFIED_DOMAIN'

def test_symbolic_partition_model():
    from axiomforge.symbolic import prove_partition_model
    result=prove_partition_model()
    if result['status']=='NOT_RUN':pytest.skip('optional SymPy missing')
    assert result['status']=='MODEL_PROVED'
    assert all(result['proof_obligations'].values())
