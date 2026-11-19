from requests import Response
class Good:
 def stream(self,*a,**kw):yield b'abc'
r=Response();r.status_code=200;r.raw=Good();assert r.content==b'abc';assert r.content==b'abc'
