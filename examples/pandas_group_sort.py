"""Standalone known pandas #56966 reproducer; requires pandas 2.2.3.
No AxiomForge internals, no patched upstream library.
"""
import pandas as pd

df=pd.DataFrame({'k1':[2,0],'k2':[1,1],'amount':[10,20]})
group=df.groupby(['k1','k2'],sort=False)
expected=[(2,1),(0,1)]
observed=list(group.groups)
print('pandas',pd.__version__,'first-seen',expected,'groups',observed,
      'aggregate-order',list(group['amount'].sum().index))
assert observed!=expected,'Historical bug not reproduced; this may be a fixed version.'
