import sys,types
sys.modules.setdefault('socksio',types.ModuleType('socksio'))
from httpcore._sync.connection_pool import ConnectionPool
from httpcore._models import Request, Response
class Good:
 def is_closed(self):return False
 def has_expired(self):return False
 def is_idle(self):return True
 def is_available(self):return True
 def can_handle_request(self,origin):return True
 def handle_request(self,request):return Response(200,content=b'OK')
 def close(self):pass
p=ConnectionPool(max_connections=1)
p._connections=[Good()]
r=p.handle_request(Request(b'GET',b'http://example.org/'))
assert r.status==200
r.close()
