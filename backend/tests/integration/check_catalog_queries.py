"""Run every statement in app.collector.queries against a live database.

Executed as dbopt_monitor, so it also proves the read-only role actually has
the privileges each query needs.
"""

import pathlib
import sys

import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from app.collector import queries as q  # noqa: E402

DSN = sys.argv[1]

CASES = [
    ("DATABASE_STATS", q.DATABASE_STATS, ()),
    ("ACTIVITY_STATS", q.ACTIVITY_STATS, ()),
    ("MAX_CONNECTIONS", q.MAX_CONNECTIONS, ()),
    ("SERVER_VERSION", q.SERVER_VERSION, ()),
    ("INSTALLED_EXTENSIONS", q.INSTALLED_EXTENSIONS, ()),
    ("TOP_STATEMENTS", q.TOP_STATEMENTS, (20,)),
    ("STATEMENT_BY_ID", q.STATEMENT_BY_ID, (1,)),
    ("BLOCKING_TREE", q.BLOCKING_TREE, ()),
    ("LONG_RUNNING", q.LONG_RUNNING, (30.0,)),
    ("TABLE_STATS", q.TABLE_STATS, (50,)),
    ("INDEX_STATS", q.INDEX_STATS, ()),
    ("UNINDEXED_FOREIGN_KEYS", q.UNINDEXED_FOREIGN_KEYS, ()),
    ("SCHEMA_OVERVIEW", q.SCHEMA_OVERVIEW, (50,)),
    ("NOTABLE_SETTINGS", q.NOTABLE_SETTINGS, (q.NOTABLE_SETTING_NAMES,)),
    ("EXACT_BLOAT", q.EXACT_BLOAT, ("public", "products")),
]

failures = 0
results = {}

with psycopg.connect(DSN) as conn:
    for name, sql, params in CASES:
        # asyncpg uses $1; psycopg uses %s. Rewrite for this harness only.
        text = sql
        for i in range(len(params), 0, -1):
            text = text.replace(f"${i}", "%s")
        try:
            with conn.cursor() as cur:
                cur.execute(text, params or None)
                rows = cur.fetchall() if cur.description else []
                cols = [d.name for d in (cur.description or [])]
            results[name] = (rows, cols)
            print(f"  OK    {name:<26} {len(rows):>5} row(s), {len(cols)} col(s)")
        except Exception as exc:
            failures += 1
            print(f"  FAIL  {name:<26} {type(exc).__name__}: {exc}")
            conn.rollback()

print()
print("=" * 72)

# --- ground-truth assertions against the fixture -------------------------
fk_rows, fk_cols = results.get("UNINDEXED_FOREIGN_KEYS", ([], []))
fk_idx = {c: i for i, c in enumerate(fk_cols)}
found = {
    (r[fk_idx["table_name"]], tuple(r[fk_idx["columns"]] or ()))
    for r in fk_rows
}
print("Unindexed foreign keys detected:")
for table, cols in sorted(found):
    print(f"    {table}({', '.join(cols)})")

# 02-schema.sql declares order_items.order_id as the headline missing index.
expected_missing = ("order_items", ("order_id",))
expected_present = ("orders", ("customer_id",))  # covered by idx_orders_customer

if expected_missing in found:
    print(f"  OK    detects the declared missing FK index {expected_missing}")
else:
    failures += 1
    print(f"  FAIL  did NOT detect {expected_missing}")

if expected_present in found:
    failures += 1
    print(
        f"  FAIL  false positive: {expected_present} IS indexed "
        f"(idx_orders_customer) but was reported as unindexed"
    )
else:
    print(f"  OK    no false positive for the indexed FK {expected_present}")

# --- index stats sanity ---------------------------------------------------
ix_rows, ix_cols = results.get("INDEX_STATS", ([], []))
ix_idx = {c: i for i, c in enumerate(ix_cols)}
by_name = {r[ix_idx["index_name"]]: r for r in ix_rows}

print()
print("Index key-column extraction:")
for name in ("idx_orders_customer_status", "customers_email_key", "idx_customers_email"):
    row = by_name.get(name)
    if row is None:
        failures += 1
        print(f"  FAIL  {name} missing from INDEX_STATS")
        continue
    cols = row[ix_idx["key_columns"]]
    print(f"  OK    {name:<30} -> {cols}")

composite = by_name.get("idx_orders_customer_status")
if composite is not None:
    cols = list(composite[ix_idx["key_columns"]] or [])
    if cols == ["customer_id", "status"]:
        print("  OK    composite key order preserved (customer_id, status)")
    else:
        failures += 1
        print(f"  FAIL  composite key order wrong: {cols}")

print()
print("=" * 72)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
