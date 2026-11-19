import sys,types
sys.modules.setdefault('socksio',types.ModuleType('socksio'))
from httpcore._sync.connection_pool import ConnectionPool,PoolRequest
from httpcore._models import Request
class Conn:
 def is_closed(self):return False
 def has_expired(self):return False
 def is_idle(self):return True
 def is_available(self):return True
 def can_handle_request(self,origin):return True
pool=ConnectionPool(max_connections=1,max_keepalive_connections=1)
c=Conn();pool._connections=[c]
a=PoolRequest(Request(b'GET',b'http://example.com/'))
b=PoolRequest(Request(b'GET',b'http://example.com/'))
a.assign_to_connection(c)
pool._requests=[a,b]
pool._assign_requests_to_connections()
assert b.is_queued(),'V11-CORE-ASSIGNED-CLOSED:reused active reservation'
