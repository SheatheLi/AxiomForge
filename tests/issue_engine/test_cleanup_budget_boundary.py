from benchmarks.reliability.cleanup_budget_boundary import matrix

def test_deferred_cleanup_has_scoped_win_not_universal_proof():
    rows=matrix()['rows']
    assert len(rows)==9
    assert any(row['improved'] for row in rows)
    assert all(row['deferred_bad_terminal_count']==0 for row in rows
               if row['max_interruptions']<row['close_attempt_budget'])
    assert all(row['deferred_bad_terminal_count']>0 for row in rows
               if row['max_interruptions']==row['close_attempt_budget'])
