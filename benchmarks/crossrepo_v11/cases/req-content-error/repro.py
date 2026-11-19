from requests import Response
from requests.exceptions import ChunkedEncodingError
from urllib3.exceptions import ProtocolError
class Broken:
 def stream(self,*a,**kw):raise ProtocolError('simulated broken stream')
r=Response();r.status_code=200;r.raw=Broken()
try:r.content
except ChunkedEncodingError:pass
else:raise AssertionError('V11-REQ-FIRST-EXCEPTION')
try:r.content
except ChunkedEncodingError:pass
else:raise AssertionError('V11-REQ-ERROR-FORGOTTEN')
