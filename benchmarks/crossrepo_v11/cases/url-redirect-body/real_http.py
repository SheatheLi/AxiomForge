"""Real localhost HTTP 307 body-preserving redirect, no network dependency."""
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib3

captured=[]
class Server(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_PUT(self):
        if self.headers.get('Transfer-Encoding','').lower()=='chunked':
            body=b''
            while True:
                length=int(self.rfile.readline().split(b';',1)[0],16)
                if not length:
                    self.rfile.readline()
                    break
                body+=self.rfile.read(length)
                self.rfile.read(2)
        else:
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        captured.append((self.path,body))
        if self.path=='/start':
            self.send_response(307);self.send_header('Location','/destination');self.send_header('Content-Length','0');self.end_headers()
        else:
            self.send_response(200);self.send_header('Content-Length','0');self.end_headers()
srv=ThreadingHTTPServer(('127.0.0.1',0),Server)
threading.Thread(target=srv.serve_forever,daemon=True).start()
try:
    response=urllib3.PoolManager().request('PUT',f'http://127.0.0.1:{srv.server_port}/start',
        body=io.BytesIO(b'UPSTREAM-BINARY-PAYLOAD'),retries=urllib3.Retry(total=2),timeout=2)
    assert response.status==200
    assert captured==[('/start',b'UPSTREAM-BINARY-PAYLOAD'),('/destination',b'UPSTREAM-BINARY-PAYLOAD')],f'V11-URL-REDIRECT-LOST-LIVE:{captured!r}'
finally:
    srv.shutdown();srv.server_close()
