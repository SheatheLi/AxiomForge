"""Build an inspectable demo database from the exact checked-in CSV inputs."""
import csv,sqlite3,pathlib

ROOT=pathlib.Path(__file__).resolve().parents[1]
base=ROOT/'examples'/'factorized'
db=base/'working.sqlite'
if db.exists():db.unlink()
with sqlite3.connect(db) as c:
    c.execute('CREATE TABLE facts (sku TEXT NOT NULL, cost INTEGER NOT NULL)')
    c.execute('CREATE TABLE regions (sku TEXT NOT NULL, region TEXT)')
    c.execute('CREATE TABLE tags (sku TEXT NOT NULL, tag TEXT)')
    for file, table, fields in [
        ('facts.csv','facts',('sku','cost')),
        ('regions.csv','regions',('sku','region')),
        ('tags.csv','tags',('sku','tag')),
    ]:
        with (base/file).open(encoding='utf8',newline='') as stream:
            rows=list(csv.DictReader(stream))
        payload=[]
        for row in rows:
            payload.append(tuple((int(row[field]) if field=='cost' else row[field] or None) for field in fields))
        c.executemany(f'INSERT INTO {table} VALUES (?,?)',payload)
print(db)
