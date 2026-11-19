from requests.auth import HTTPDigestAuth
x=HTTPDigestAuth('Ondřej'.encode('utf-8'),'p')
x.init_per_thread_state();x._thread_local.chal={'realm':'realm','nonce':'1234','qop':'auth'}
header=x.build_digest_header('GET','http://example.org/')
assert 'username="Ondřej"' in header, 'V11-UNSEEN-6102-NONLATIN-HEADER'
