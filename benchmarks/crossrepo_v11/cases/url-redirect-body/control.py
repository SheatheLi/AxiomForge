import io
from urllib3 import PoolManager,Retry
class Reply:
 status=200;retries=Retry(total=3)
 def get_redirect_location(self):return None
class Conn:
 def urlopen(self,method,url,**kw):
  assert kw['body'].read()==b'GOOD';return Reply()
p=PoolManager();p.connection_from_host=lambda *a,**kw:Conn()
assert p.urlopen('PUT','http://test.invalid/start',body=io.BytesIO(b'GOOD'),headers={}).status==200
