import sys,types
class E(Exception):pass
class PE(E):pass
class DE(PE):pass
class IE(PE):pass
class SE(PE):pass
class IN(E):pass
class CI(E):pass
class IC(PE):pass
class IS(PE):pass
class CE(PE):pass
m=types.SimpleNamespace(exceptions=types.SimpleNamespace(PostgresError=PE,DataError=DE,IntegrityConstraintViolationError=IE,SyntaxOrAccessError=SE,InterfaceError=IN,InvalidCachedStatementError=IC,InternalServerError=IS,InternalClientError=CI))
sys.modules['asyncpg']=m
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
adapter=AsyncAdapt_asyncpg_dbapi(m)
assert adapter._asyncpg_error_translate.get(DE) is adapter.DataError,'V11-SA-DATA-ERROR-MAPPING'
