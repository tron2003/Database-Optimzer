"""End-to-end check against a live database using the real asyncpg path.

Exercises the two queries the psycopg harness could not rewrite, then runs the
full analysis pipeline and asserts it finds the problems that 02-schema.sql
deliberately planted.
"""

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import asyncpg  # noqa: E402

from app.analysis.indexes import analyze_indexes, stats_age_hours  # noqa: E402
from app.analysis.maintenance import analyze_maintenance, bloat_from_pgstattuple  # noqa: E402
from app.analysis.plans import analyze_plan  # noqa: E402
from app.analysis.types import merge_drafts  # noqa: E402
from app.collector import queries as q  # noqa: E402
from app.safety.allowlist import validate  # noqa: E402

DSN = sys.argv[1]
APPLY_DSN = sys.argv[2]
failures = 0


def check(ok: bool, msg: str) -> None:
    global failures
    if ok:
        print(f"  OK    {msg}")
    else:
        failures += 1
        print(f"  FAIL  {msg}")


async def main() -> None:
    global failures
    conn = await asyncpg.connect(DSN)

    print("--- queries the psycopg harness could not rewrite ---")
    try:
        rows = await conn.fetch(q.TOP_STATEMENTS, 20)
        check(True, f"TOP_STATEMENTS executed, {len(rows)} row(s)")
    except Exception as exc:
        check(False, f"TOP_STATEMENTS: {type(exc).__name__}: {exc}")

    try:
        row = await conn.fetchrow(q.EXACT_BLOAT, "public", "products")
        measured = bloat_from_pgstattuple(dict(row))
        check(
            measured["table_bytes"] > 0 and measured["dead_tuples"] >= 0,
            f"EXACT_BLOAT via pgstattuple: table={measured['table_bytes']} bytes, "
            f"dead={measured['dead_tuples']}, reclaimable={measured['reclaimable_bytes']}",
        )
    except Exception as exc:
        check(False, f"EXACT_BLOAT: {type(exc).__name__}: {exc}")

    print()
    print("--- read-only enforcement ---")
    ro = await conn.fetchval("SHOW default_transaction_read_only")
    check(ro == "on", f"monitor role session is read-only (default_transaction_read_only={ro})")
    try:
        await conn.execute("CREATE TABLE should_not_exist (x int)")
        check(False, "monitor role was able to CREATE TABLE — read-only is NOT enforced")
    except asyncpg.PostgresError as exc:
        check(True, f"monitor role refused a write: {type(exc).__name__}")

    print()
    print("--- analysis pipeline on real catalog data ---")
    table_stats = [dict(r) for r in await conn.fetch(q.TABLE_STATS, 100)]
    index_stats = [dict(r) for r in await conn.fetch(q.INDEX_STATS)]
    fks = [dict(r) for r in await conn.fetch(q.UNINDEXED_FOREIGN_KEYS)]
    db_row = dict(await conn.fetchrow(q.DATABASE_STATS))
    age = stats_age_hours(db_row.get("stats_reset"))

    drafts = analyze_indexes(
        index_stats=index_stats,
        table_stats=table_stats,
        unindexed_fks=fks,
        stats_age_hours=age,
    )
    drafts += analyze_maintenance(table_stats)
    drafts = merge_drafts(drafts)

    print(f"  {len(drafts)} suggestion(s) derived:")
    for d in drafts:
        stmt = (d.statement or "(advisory only)").replace("\n", " ")
        print(f"    [{d.severity.value:<8}] {d.title}")
        print(f"               {stmt[:110]}")

    titles = " || ".join(d.title.lower() for d in drafts)

    check(
        "order_items" in titles and "foreign key" in titles,
        "found the planted unindexed foreign key on order_items",
    )
    check("duplicate" in titles, "found the planted duplicate index on customers(email)")
    check(
        any("prefix" in d.title.lower() for d in drafts),
        "found the planted redundant prefix index idx_orders_customer",
    )

    print()
    print("--- every generated statement must pass the allowlist ---")
    bad = []
    for d in drafts:
        if not d.statement:
            continue
        result = validate(d.statement)
        if not result.allowed:
            bad.append((d.statement, result.reason))
    check(not bad, f"all {sum(1 for d in drafts if d.statement)} generated statements validated")
    for stmt, reason in bad:
        print(f"         {stmt[:80]} -> {reason}")

    print()
    print("--- revert statements must also pass ---")
    bad_revert = []
    for d in drafts:
        if not d.revert_statement:
            continue
        result = validate(d.revert_statement)
        if not result.allowed:
            bad_revert.append((d.revert_statement, result.reason))
    check(not bad_revert, "all revert statements validated")
    for stmt, reason in bad_revert:
        print(f"         {stmt[:80]} -> {reason}")

    print()
    print("--- EXPLAIN on the planted slow query ---")
    slow_sql = "SELECT oi.id, oi.product_id FROM order_items oi WHERE oi.order_id = 4242"
    raw = await conn.fetchval(f"EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS, VERBOSE) {slow_sql}")
    import json

    plan = json.loads(raw) if isinstance(raw, str) else raw
    findings, summary = analyze_plan(plan)
    print(f"  execution {summary.get('execution_ms')} ms, {summary.get('node_count')} node(s)")
    for f in findings:
        print(f"    [{f.severity.value:<8}] {f.code}: {f.node}")
    check(
        any(f.code == "selective_seq_scan" for f in findings),
        "plan analyser flagged the sequential scan on the unindexed order_items.order_id",
    )

    await conn.close()

    # --- the write path, end to end -------------------------------------
    print()
    print("--- apply path: build the recommended index for real ---")
    target = next(
        (
            d
            for d in drafts
            if d.statement
            and "order_items" in (d.target_relation or "")
            and d.kind.value == "create_index"
        ),
        None,
    )
    if target is None:
        check(False, "no order_items index suggestion to apply")
        return

    result = validate(target.statement)
    check(result.allowed, f"statement revalidated at execution time ({result.kind})")

    apply_conn = await asyncpg.connect(APPLY_DSN)
    try:
        await apply_conn.execute("SET lock_timeout = 3000")
        # No surrounding transaction: CONCURRENTLY forbids one.
        await apply_conn.execute(target.statement)
        check(True, f"executed: {target.statement}")
    except asyncpg.PostgresError as exc:
        check(False, f"apply failed: {type(exc).__name__}: {exc}")
        await apply_conn.close()
        return

    # Re-plan the same query and confirm the index is actually used.
    raw2 = await apply_conn.fetchval(f"EXPLAIN (FORMAT JSON, ANALYZE) {slow_sql}")
    plan2 = json.loads(raw2) if isinstance(raw2, str) else raw2
    findings2, summary2 = analyze_plan(plan2)
    node_type = (plan2[0]["Plan"]["Node Type"]) if plan2 else "?"
    print(f"  after:  {node_type}, execution {summary2.get('execution_ms')} ms")
    check(
        "Seq Scan" not in node_type,
        f"the planner switched off the sequential scan (now {node_type})",
    )
    check(
        not any(f.code == "selective_seq_scan" for f in findings2),
        "the selective-seq-scan finding is gone after applying the index",
    )
    before = summary.get("execution_ms") or 0
    after = summary2.get("execution_ms") or 0
    if before and after:
        print(f"  speedup: {before:.1f} ms -> {after:.3f} ms  ({before / max(after, 1e-6):,.0f}x)")

    # Revert, so the fixture is left as it was.
    if target.revert_statement:
        await apply_conn.execute(target.revert_statement)
        check(True, f"reverted: {target.revert_statement}")

    await apply_conn.close()


asyncio.run(main())
print()
print("=" * 72)
print("FAILURES:", failures)
sys.exit(1 if failures else 0)
