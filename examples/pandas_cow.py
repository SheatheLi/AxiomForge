"""Standalone known pandas #63219 historical behavior with CoW enabled."""
import pandas as pd

def compute(use_view:bool):
    with pd.option_context('mode.copy_on_write',True):
        grouping=pd.Series([1,2,1])
        frame=pd.DataFrame({'amount':[1,2,3]})
        group=frame.groupby(grouping)
        if use_view:
            unused=grouping[:]
            assert len(unused)==3
        grouping.iloc[0]=100
        return group.sum().to_dict()

left=compute(False)
right=compute(True)
print('pandas',pd.__version__,'no view',left,'unused view',right)
assert left!=right,'Historical bug not reproduced; this may be a fixed version.'
