from urllib3.connection import HTTPConnection
try:
 HTTPConnection('127.0.0.1',blocksize=0)
except (ValueError,TypeError):pass
else: raise AssertionError('V11-URL-ZERO-BODY')
