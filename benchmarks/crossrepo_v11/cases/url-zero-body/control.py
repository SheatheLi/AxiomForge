from urllib3.connection import HTTPConnection
assert HTTPConnection('127.0.0.1',blocksize=16384).blocksize==16384
assert HTTPConnection('127.0.0.1',blocksize=1).blocksize==1
