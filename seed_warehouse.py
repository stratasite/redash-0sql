"""Build the demo warehouse from the model repo's Parquet.

    python seed_warehouse.py /warehouse/customer_service.duckdb [/cs/warehouse/data]

One table per Parquet file, which is what customer-service/bin/seed does with
the duckdb CLI; this does it with the duckdb Python package already in the
Redash image, so the image needs no extra tool. Idempotent: an existing file
is kept, so re-running demo.sh does not rebuild it.
"""

import glob
import os
import sys

import duckdb

path = sys.argv[1]
data = sys.argv[2] if len(sys.argv) > 2 else "/cs/warehouse/data"

if os.path.exists(path):
    print(f"{path} exists, keeping it")
    sys.exit(0)

files = sorted(glob.glob(os.path.join(data, "*.parquet")))
if not files:
    sys.exit(f"No .parquet in {data}. Is the customer-service submodule checked out?")

# ponytail: build beside the target and rename, so an interrupted run leaves
# no half-built file that the `exists` check above would then keep forever.
tmp = path + ".building"
for leftover in (tmp, tmp + ".wal"):
    if os.path.exists(leftover):
        os.remove(leftover)

con = duckdb.connect(tmp)
for f in files:
    table = os.path.basename(f)[: -len(".parquet")]
    con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM read_parquet(?)', [f])
con.close()
os.replace(tmp, path)

con = duckdb.connect(path, read_only=True)
tables = con.execute("SELECT count(*) FROM information_schema.tables").fetchone()[0]
contacts = con.execute("SELECT count(*) FROM cs_contact_f").fetchone()[0]
con.close()
print(f"{path}: {tables} tables, {contacts:,} contacts")
