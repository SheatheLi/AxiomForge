"""Controlled pool bug/regression models grounded in HTTPcore PR #305.

These are independently authored models, NOT the actual historical httpcore
source. They model the verified `except Exception` cancellation cleanup bug
versus `except BaseException` fix in PR #305.
"""
from __future__ import annotations

import asyncio
from axiomforge.issue_engine.cancellation_safety import Ledger, Scenario, instrument_source

MODEL = '''
import asyncio
class Pool:
    def __init__(self, *, corrected, ledger):
        self.corrected = corrected
        self.ledger = ledger
        self.permits = asyncio.Semaphore(1)
    def _release(self):
        self.ledger.release("connection-permit")
        self.permits.release()
    async def request(self):
        await self.permits.acquire()
        self.ledger.acquire("connection-permit")
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        except BaseException:
            if self.corrected:
                self._release()
            raise
        else:
            self._release()
'''
# Previous version's actual issue is cancellation not caught by Exception.
BUGGY_MODEL = MODEL.replace('except BaseException:', 'except Exception:')


def build(corrected: bool):
    source = MODEL if corrected else BUGGY_MODEL
    instrumented = instrument_source(source, filename='pool_resource_model.py', namespace={'asyncio': asyncio})
    def factory():
        ledger = Ledger()
        pool = instrumented.namespace['Pool'](corrected=corrected, ledger=ledger)
        return Scenario(operation=pool.request, observe=ledger.snapshot, recovery=pool.request)
    return instrumented, factory


def buggy():
    return build(corrected=False)


def fixed():
    return build(corrected=True)

NESTED = '''
import asyncio
class Pool:
    def __init__(self,*,corrected,ledger):
        self.ledger=ledger
        self.corrected=corrected
    def _release(self):
        self.ledger.release('r')
    async def _cleanup(self):
        await asyncio.sleep(0)
        if not self.corrected:
            self._release()
    async def request(self):
        self.ledger.acquire('r')
        try:
            await asyncio.sleep(0)
        except BaseException:
            if self.corrected:
                self._release()  # detach owned resource before cancellable cleanup
            await self._cleanup()
            raise
        else:
            self._release()
'''


def build_nested(corrected:bool):
    unit=instrument_source(NESTED,filename='nested_cleanup.py',namespace={'asyncio':asyncio})
    def factory():
        ledger=Ledger()
        pool=unit.namespace['Pool'](corrected=corrected,ledger=ledger)
        return Scenario(operation=pool.request,observe=ledger.snapshot)
    return unit,factory


def nested_buggy(): return build_nested(False)
def nested_fixed(): return build_nested(True)
