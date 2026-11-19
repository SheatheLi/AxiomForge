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
pool=ConnectionPool(max_connections=2,max_keepalive_connections=2)
c1=Conn();pool._connections=[c1]
r=PoolRequest(Request(b'GET',b'http://example.com/'))
pool._requests=[r];pool._assign_requests_to_connections()
assert c1 not in pool._assign_requests_to_connections()
