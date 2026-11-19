"""Create the optional self-owned SQLite demo fixture, without dependencies."""
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
db_path = ROOT / 'examples/sql_review/orders.sqlite'


def create_demo() -> Path:
    db_path.parent.mkdir(exist_ok=True, parents=True)
    # Do not replace the fixture behind an existing evidence receipt.
    if db_path.exists():
        return db_path
    with sqlite3.connect(db_path) as db:
        db.executescript('''
CREATE TABLE orders (id TEXT PRIMARY KEY, sku TEXT NOT NULL, price INTEGER NOT NULL);
CREATE TABLE supplies (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, cost INTEGER NOT NULL);
INSERT INTO orders VALUES ('order-1','A',100),('order-2','B',200),('order-3','C',-100);
INSERT INTO supplies VALUES (1,'A',5),(2,'A',9),(3,'B',10),(4,'C',12),(5,'C',8);
''')
    return db_path


if __name__ == '__main__':
    print(create_demo())
