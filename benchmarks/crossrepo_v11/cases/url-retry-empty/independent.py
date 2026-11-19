from urllib3.util.retry import Retry
assert not Retry(status_forcelist=[500],allowed_methods=frozenset()).is_retry('POST',status_code=500), 'V11-URL-EMPTY-RETRY'
