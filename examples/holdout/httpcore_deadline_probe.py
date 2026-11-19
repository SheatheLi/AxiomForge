"""Executable, offline probe for upstream HTTPcore SOCKS handshake deadlines.

Use `AXIOMFORGE_ASSERT_TIMEOUT=1` to require an explicit timeout on all
reads and writes. The unpatched 1.0.9 implementation loses it.
No actual proxy or network is contacted.
"""
import os
import sys
from types import ModuleType, SimpleNamespace

socksio = ModuleType('socksio')
class Auth:
    NO_AUTH_REQUIRED = b'\x00'
    USERNAME_PASSWORD = b'\x02'
class Command:
    CONNECT = 1
class ReplyCode:
    SUCCEEDED = 0
class Reply:
    def __init__(self):
        self.reply_code = ReplyCode.SUCCEEDED
class PasswordReply:
    success = True
class AuthReply:
    def __init__(self, authenticated=False):
        self.method = Auth.USERNAME_PASSWORD if authenticated else Auth.NO_AUTH_REQUIRED
class RequestKind:
    def __init__(self, *args, **kwargs):
        pass
class CommandRequest:
    @classmethod
    def from_address(cls, *args, **kwargs):
        return cls()
class Protocol:
    def __init__(self):
        self.count = 0
    def send(self, value):
        pass
    def data_to_send(self):
        return b'handshake'
    def receive_data(self, value):
        self.count += 1
        if self.count == 1:
            return AuthReply(authenticated=os.environ.get('AXIOMFORGE_TEST_VARIANT') == 'auth')
        if self.count == 2 and os.environ.get('AXIOMFORGE_TEST_VARIANT') == 'auth':
            return PasswordReply()
        return Reply()
socksio.socks5 = SimpleNamespace(SOCKS5Connection=Protocol, SOCKS5AuthMethod=Auth,
    SOCKS5AuthMethodsRequest=RequestKind, SOCKS5AuthReply=AuthReply,
    SOCKS5UsernamePasswordRequest=RequestKind, SOCKS5UsernamePasswordReply=PasswordReply,
    SOCKS5Command=Command, SOCKS5CommandRequest=CommandRequest,
    SOCKS5Reply=Reply, SOCKS5ReplyCode=ReplyCode)
sys.modules['socksio'] = socksio

from httpcore._models import Origin, Request
from httpcore._sync import socks_proxy

class Stream:
    def __init__(self, required):
        self.required = required
        self.observed = []
    def write(self, data, timeout=None):
        self.observed.append(('write', timeout))
        if self.required and timeout != 0.25:
            raise AssertionError('SOCKS-DEADLINE-LOST: write')
    def read(self, max_bytes, timeout=None):
        self.observed.append(('read', timeout))
        if self.required and timeout != 0.25:
            raise AssertionError('SOCKS-DEADLINE-LOST: read')
        return b'reply'
    def get_extra_info(self, key):
        return None

class Backend:
    def __init__(self, stream):
        self.stream = stream
    def connect_tcp(self, **kwargs):
        if kwargs['timeout'] != 0.25 and self.stream.required:
            raise AssertionError('SOCKS-DEADLINE-LOST: connection')
        return self.stream

class HTTP11:
    def __init__(self, **kwargs):
        pass
    def handle_request(self, request):
        return 'ok'

socks_proxy.HTTP11Connection = HTTP11
required = os.environ.get('AXIOMFORGE_ASSERT_TIMEOUT') == '1'
stream = Stream(required)
conn = socks_proxy.Socks5Connection(
    proxy_origin=Origin(b'http', b'proxy.local', 8080),
    remote_origin=Origin(b'http', b'example.com', 80),
    network_backend=Backend(stream),
    proxy_auth=(b"test", b"pass") if os.environ.get("AXIOMFORGE_TEST_VARIANT") == "auth" else None)
req = Request(b'GET', b'http://example.com/', extensions={'timeout': {'connect': 0.25}})
assert conn.handle_request(req) == 'ok'
assert len(stream.observed) == (6 if os.environ.get('AXIOMFORGE_TEST_VARIANT') == 'auth' else 4)
if required:
    assert all(timeout == 0.25 for _, timeout in stream.observed)
else:
    assert all(operation in ('read', 'write') for operation, _ in stream.observed)
