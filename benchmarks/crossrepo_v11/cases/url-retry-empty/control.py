from urllib3.util.retry import Retry
assert Retry(status_forcelist=[500],allowed_methods=None).is_retry('GET',status_code=500)
assert Retry(status_forcelist=[500],allowed_methods={'GET'}).is_retry('GET',status_code=500)
assert not Retry(status_forcelist=[500],allowed_methods={'POST'}).is_retry('GET',status_code=500)
