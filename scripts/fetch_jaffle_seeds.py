"""Fetch upstream test data on request, byte-validate, never vendor unknown-license assets."""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT/'examples'/'jaffle_shop'
EXPECTED = {
 'raw_products.csv':'125285432532ab6add35bd977330019670d8f996',
 'raw_supplies.csv':'058c92bca08517e01fea23777593fb13e8ca0de5'
}
COMMIT = '5beb145b00f5465ec759cfcdd9745e858818cf95'
BASE = 'https://raw.githubusercontent.com/dbt-labs/jaffle-shop/'+COMMIT+'/seeds/jaffle-data/'

def blob_hash(raw):
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    for name, expected in EXPECTED.items():
        with urlopen(BASE+name,timeout=20) as response:
            data = response.read(100000)
        if blob_hash(data)!=expected:
            raise RuntimeError('upstream source changed or invalid download: '+name)
        (DEST/name).write_bytes(data)
        print(name,'verified Git blob',expected)
    return 0

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception as err:
        print('FETCH_ERROR',err,file=sys.stderr);raise SystemExit(3)
