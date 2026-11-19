"""A negative/positive bounded-exploration matrix, not a scheduler proof."""
import json
from axiomforge.issue_engine.cleanup_model import verify_bounded

def matrix():
    rows=[]
    for retry_budget in (2,3,4):
        for interrupts in range(1,retry_budget+1):
            baseline=verify_bounded('early-commit',attempts_limit=retry_budget,max_interruptions=interrupts)
            deferred=verify_bounded('defer-commit',attempts_limit=retry_budget,max_interruptions=interrupts)
            rows.append({'close_attempt_budget':retry_budget,'max_interruptions':interrupts,
                         'early_bad_terminal_count':baseline['bad_terminal_count'],
                         'deferred_bad_terminal_count':deferred['bad_terminal_count'],
                         'improved':deferred['bad_terminal_count']<baseline['bad_terminal_count']})
    return {'rows':rows,'boundary':'Finite single-connection model; absent retry after budget depletion remains unsafe'}
if __name__=='__main__':print(json.dumps(matrix(),indent=2))
