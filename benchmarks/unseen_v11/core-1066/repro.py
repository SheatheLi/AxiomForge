import sys,types
sys.modules.setdefault('socksio',types.ModuleType('socksio'))
from httpcore._sync.connection_pool import ConnectionPool
from httpcore._exceptions import ConnectionNotAvailable
from httpcore._models import Request
class Stale:
 def __init__(self):self.attempts=0
 def is_closed(self):return False
 def has_expired(self):return False
 def is_idle(self):return False
 def is_available(self):return True
 def can_handle_request(self,origin):return True
 def handle_request(self,request):
  self.attempts+=1
  if self.attempts>=3:raise AssertionError('V11-UNSEEN-1066-REPEATED-STALE')
  raise ConnectionNotAvailable()
p=ConnectionPool(max_connections=1)
p._connections=[Stale()]
p.handle_request(Request(b'GET',b'http://example.org/'))
