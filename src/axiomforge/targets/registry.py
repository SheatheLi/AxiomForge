"""Trusted in-tree integrations. New targets implement input_for and execute.

Defining a target does not magically make its contract a proved theorem. A
separate source authority and scenario admission are still required.
"""
from __future__ import annotations
from typing import Any
from axiomforge.targets import pandas_cases,sqlalchemy_cases

CONTRACTS=pandas_cases.CONTRACTS|{"sa-lifecycle":sqlalchemy_cases.CONTRACT}
ISSUES=pandas_cases.ISSUES|{"sa-lifecycle":"https://github.com/sqlalchemy/sqlalchemy/issues/13548"}

def input_for(name:str,seed:int)->dict[str,Any]:
    if name=="sa-lifecycle":return sqlalchemy_cases.input_for(seed)
    return pandas_cases.input_for(name,seed)

def execute(name:str,payload:dict[str,Any],control:dict[str,Any]|None=None)->dict[str,Any]:
    if name=="sa-lifecycle":return sqlalchemy_cases.execute(payload,control)
    return pandas_cases.execute(name,payload,control)
