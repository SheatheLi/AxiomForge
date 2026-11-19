from urllib3.util.retry import Retry
r=Retry(status_forcelist=[500],allowed_methods=set())
assert not r.is_retry('GET',status_code=500), 'V11-URL-EMPTY-RETRY'
