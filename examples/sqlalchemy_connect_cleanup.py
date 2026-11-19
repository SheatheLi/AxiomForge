"""Standalone explicit-pool-cleanup evidence for sqlalchemy #13548.
Does not assert live Oracle/PostgreSQL sessions from an SQLite database.
"""
from sqlalchemy import create_engine,event
import sqlalchemy

engine=create_engine('sqlite+pysqlite:///:memory:')
pool=engine.pool
creator=pool._invoke_creator
closer=pool._close_connection
new=[];old=[]
def wrapped_create(rec):
    c=creator(rec);new.append(id(c));return c
def wrapped_close(c,**kw):
    old.append(id(c));return closer(c,**kw)
def fail(dbapi,rec):
    raise RuntimeError('controlled connect failure')
pool._invoke_creator=wrapped_create
pool._close_connection=wrapped_close
event.listen(engine,'connect',fail)
for _ in range(3):
    try:
        with engine.connect():pass
    except RuntimeError as e:
        assert str(e)=='controlled connect failure'
engine.dispose()
print('SQLAlchemy',sqlalchemy.__version__,'pool-created',len(new),'pool-closed',len(old))
assert len(new)==3 and not old,'Historical bug not reproduced; may be a fixed version.'
