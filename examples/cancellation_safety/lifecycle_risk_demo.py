"""Controlled (not upstream) case for evaluating optional causal risk filtering."""
import asyncio
from axiomforge.issue_engine.cancellation_safety import Ledger, Scenario, instrument_source

SOURCE = '''
import asyncio
class Service:
    def __init__(self, ledger): self.ledger=ledger
    async def operation(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.ledger.acquire('lease')
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        except Exception:
            self.ledger.release('lease')
            raise
        else:
            self.ledger.release('lease')
'''


def setup():
    unit=instrument_source(SOURCE,filename='risk-window-model.py',namespace={'asyncio':asyncio})
    def factory():
        ledger=Ledger()
        service=unit.namespace['Service'](ledger)
        return Scenario(operation=service.operation,observe=ledger.snapshot,
                        risk_probe=lambda:bool(ledger.held))
    return unit,factory
