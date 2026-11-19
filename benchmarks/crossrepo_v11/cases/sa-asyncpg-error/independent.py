"""Independent exception subtype boundary: driver DataError class must remain specific."""
import sys,types
class DBError(Exception): pass
class PostgreSQLError(DBError): pass
class DataFault(PostgreSQLError): pass
class IntegrityFault(PostgreSQLError): pass
class SyntaxFault(PostgreSQLError): pass
class InterfaceFault(DBError): pass
class UnknownFault(PostgreSQLError): pass
class InternalClientFault(PostgreSQLError): pass
class InternalServerFault(PostgreSQLError): pass
m=types.SimpleNamespace(exceptions=types.SimpleNamespace(
 DataError=DataFault,PostgresError=PostgreSQLError,
 IntegrityConstraintViolationError=IntegrityFault,
 SyntaxOrAccessError=SyntaxFault,InterfaceError=InterfaceFault,
 InvalidCachedStatementError=UnknownFault,
 InternalClientError=InternalClientFault,InternalServerError=InternalServerFault))
sys.modules['asyncpg']=m
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi
adapter=AsyncAdapt_asyncpg_dbapi(m)
mapping=adapter._asyncpg_error_translate
assert mapping.get(DataFault) is adapter.DataError,'V11-SA-DATA-ERROR-MAPPING:subclass variant'
assert mapping.get(IntegrityFault) is adapter.IntegrityError
