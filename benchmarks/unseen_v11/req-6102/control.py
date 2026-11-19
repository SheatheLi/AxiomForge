from requests.auth import HTTPDigestAuth
x=HTTPDigestAuth('alice','p')
x.init_per_thread_state();x._thread_local.chal={'realm':'realm','nonce':'1234','qop':'auth'}
assert 'username="alice"' in x.build_digest_header('GET','http://example.org/')
