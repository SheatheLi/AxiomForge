import io
from urllib3 import PoolManager,Retry
class Reply:
 def __init__(self,status,loc=None):self.status=status;self.loc=loc;self.retries=Retry(total=3)
 def get_redirect_location(self):return self.loc
 def drain_conn(self):pass
class Conn:
 def __init__(self):self.data=[]
 def urlopen(self,method,url,**kw):
  self.data.append(kw['body'].read() if kw.get('body') else b'')
  return Reply(307,'/target') if len(self.data)==1 else Reply(200)
 def is_same_host(self,url):return True
c=Conn();p=PoolManager();p.connection_from_host=lambda *a,**kw:c
p.urlopen('PUT','http://test.invalid/start',body=io.BytesIO(b'PAYLOAD'),retries=Retry(total=3),headers={})
assert c.data==[b'PAYLOAD',b'PAYLOAD'],f'V11-URL-REDIRECT-LOST:{c.data!r}'
