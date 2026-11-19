from calculation import calculate
from pathlib import Path
assert 'CASE' in Path('fixture.txt').read_text(encoding='utf8')
if calculate(4) != 5:
    raise AssertionError('DEMO-SPECIFIC-INCORRECT-CALCULATION')
