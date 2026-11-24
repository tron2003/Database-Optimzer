"""The fixed tool set available to the natural-language console.

There is no ``run_sql`` tool, and that is a design decision rather than an
omission. A general SQL tool would mean the model composes a statement that
reaches the database, which is exactly the property the rest of this system
is built to avoid. Instead each tool wraps one specific, pre-written,
read-only query. The model chooses which to call and with what arguments; the
SQL itself is fixed at author time.

Arguments are coerced and bounded here. A model that asks for a limit of
1,000,000 gets 200.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.analysis import health as health_mod
from app.collector import queries as q
from app.db.target import TargetPool, rows_to_dicts
from app.services.explain import ExplainRefused, capture_plan

log = logging.getLogger(__name__)

MAX_ROWS = 200


@dataclass(slots=True)
class ToolContext:
    pool: TargetPool
    allow_explain_analyze: bool
    has_pg_stat_statements: bool


def _limit(value: Any, default: int = 20) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, MAX_ROWS))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def tool_get_health(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    db = dict(await ctx.pool.fetchrow(q.DATABASE_STATS) or {})
    act = dict(await ctx.pool.fetchrow(q.ACTIVITY_STATS) or {})
    max_conn = await ctx.pool.fetchval(q.MAX_CONNECTIONS) or 0

    hit = int(db.get("blks_hit") or 0)
    read = int(db.get("blks_read") or 0)
    metrics = {
        **{k: int(v) for k, v in db.items() if isinstance(v, int)},
        **{k: int(v) for k, v in act.items() if isinstance(v, (int, float))},
        "max_connections": int(max_conn),
        "cache_hit_ratio": (hit / (hit + read)) if (hit + read) else 1.0,
        "longest_xact_seconds": float(act.get("longest_xact_seconds") or 0.0),
    }
    return health_mod.evaluate(metrics)


async def tool_list_slow_queries(ctx: ToolContext, limit: Any = 10, **_: Any) -> dict[str, Any]:
    if not ctx.has_pg_stat_statements:
        return {
            "error": "pg_stat_statements is not installed on this database, so no "
            "statement-level statistics are available."
        }
    rows = rows_to_dicts(await ctx.pool.fetch(q.TOP_STATEMENTS, _limit(limit, 10)))
    return {
        "count": len(rows),
        "note": "ordered by cumulative total execution time since the last stats reset",
        "queries": [
            {
                "queryid": str(r["queryid"]),
                "query": (r["query"] or "")[:1500],
                "calls": r["calls"],
                "total_exec_ms": round(float(r["total_exec_ms"] or 0), 1),
                "mean_exec_ms": round(float(r["mean_exec_ms"] or 0), 3),
                "max_exec_ms": round(float(r["max_exec_ms"] or 0), 1),
                "stddev_exec_ms": round(float(r["stddev_exec_ms"] or 0), 3),
                "rows": r["rows"],
                "shared_blks_read": r["shared_blks_read"],
                "shared_blks_hit": r["shared_blks_hit"],
                "temp_blks_written": r["temp_blks_written"],
            }
            for r in rows
        ],
    }


async def tool_describe_schema(
    ctx: ToolContext, table: Any = None, limit: Any = 15, **_: Any
) -> dict[str, Any]:
    rows = rows_to_dicts(await ctx.pool.fetch(q.SCHEMA_OVERVIEW, _limit(limit, 15)))
    name = _text(table)
    if name:
        wanted = name.split(".")[-1].lower()
        rows = [r for r in rows if str(r.get("table_name", "")).lower() == wanted]
        if not rows:
            return {"error": f"no table named {name!r} was found"}
    return {"tables": rows}


async def tool_list_indexes(ctx: ToolContext, table: Any = None, **_: Any) -> dict[str, Any]:
    rows = rows_to_dicts(await ctx.pool.fetch(q.INDEX_STATS))
    name = _text(table)
    if name:
        wanted = name.split(".")[-1].lower()
        rows = [r for r in rows if str(r.get("table_name", "")).lower() == wanted]
    return {
        "count": len(rows),
        "indexes": [
            {
                "name": r["index_name"],
                "table": r["table_name"],
                "columns": list(r.get("key_columns") or []),
                "method": r.get("method"),
                "unique": r.get("indisunique"),
                "primary": r.get("indisprimary"),
                "valid": r.get("indisvalid"),
                "partial": r.get("is_partial"),
                "scans": r.get("idx_scan"),
                "size_bytes": r.get("index_bytes"),
                "definition": r.get("indexdef"),
            }
            for r in rows[:MAX_ROWS]
        ],
    }


async def tool_table_statistics(ctx: ToolContext, limit: Any = 15, **_: Any) -> dict[str, Any]:
    rows = rows_to_dicts(await ctx.pool.fetch(q.TABLE_STATS, _limit(limit, 15)))
    return {
        "count": len(rows),
        "tables": [
            {
                "table": r["table_name"],
                "schema": r["schemaname"],
                "seq_scan": r["seq_scan"],
                "seq_tup_read": r["seq_tup_read"],
                "idx_scan": r["idx_scan"],
                "n_live_tup": r["n_live_tup"],
                "n_dead_tup": r["n_dead_tup"],
                "n_mod_since_analyze": r["n_mod_since_analyze"],
                "last_autovacuum": str(r.get("last_autovacuum") or ""),
                "last_autoanalyze": str(r.get("last_autoanalyze") or ""),
                "total_bytes": r["total_bytes"],
                "index_bytes": r["index_bytes"],
            }
            for r in rows
        ],
    }


async def tool_list_blocking(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    rows = rows_to_dicts(await ctx.pool.fetch(q.BLOCKING_TREE))
    return {"count": len(rows), "blocking": rows}


async def tool_list_long_running(
    ctx: ToolContext, seconds: Any = 30, **_: Any
) -> dict[str, Any]:
    try:
        threshold = max(1.0, min(float(seconds), 86_400.0))
    except (TypeError, ValueError):
        threshold = 30.0
    rows = rows_to_dicts(await ctx.pool.fetch(q.LONG_RUNNING, threshold))
    return {"count": len(rows), "threshold_seconds": threshold, "sessions": rows}


async def tool_explain_query(ctx: ToolContext, sql: Any = None, **_: Any) -> dict[str, Any]:
    statement = _text(sql)
    if not statement:
        return {"error": "the 'sql' argument is required"}
    try:
        # Always plan-only from the chat tool. Executing a statement because a
        # model asked to see its timings is not a trade this console makes,
        # even on a connection where ANALYZE is permitted elsewhere.
        result = await capture_plan(
            ctx.pool, statement, analyze=False, allow_analyze=False
        )
    except ExplainRefused as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"EXPLAIN failed: {exc}"}

    return {
        "analyzed": False,
        "note": "plan only; the statement was not executed",
        "findings": result["findings"],
        "summary": result["summary"],
        "plan": result["plan"],
    }


async def tool_get_settings(ctx: ToolContext, **_: Any) -> dict[str, Any]:
    rows = rows_to_dicts(await ctx.pool.fetch(q.NOTABLE_SETTINGS, q.NOTABLE_SETTING_NAMES))
    return {"settings": rows}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ToolFn = Callable[..., Any]

TOOLS: dict[str, dict[str, Any]] = {
    "get_health": {
        "fn": tool_get_health,
        "args": {},
        "description": "Overall instance health: cache hit ratio, connections, locks, "
        "long transactions, rollbacks, deadlocks, temp spills. Each check comes with "
        "an interpretation.",
    },
    "list_slow_queries": {
        "fn": tool_list_slow_queries,
        "args": {"limit": "int, 1-200, default 10"},
        "description": "Top statements from pg_stat_statements ordered by cumulative "
        "execution time, with call counts, mean/max/stddev and block I/O.",
    },
    "describe_schema": {
        "fn": tool_describe_schema,
        "args": {"table": "optional table name", "limit": "int, default 15"},
        "description": "Table definitions: columns with types and nullability, index "
        "definitions, estimated row counts and on-disk size.",
    },
    "list_indexes": {
        "fn": tool_list_indexes,
        "args": {"table": "optional table name"},
        "description": "Every index with its columns, method, size, scan count and "
        "whether it is unique, primary, partial or invalid.",
    },
    "table_statistics": {
        "fn": tool_table_statistics,
        "args": {"limit": "int, default 15"},
        "description": "Per-table activity: sequential vs index scans, live and dead "
        "tuples, rows modified since the last ANALYZE, vacuum timestamps, sizes.",
    },
    "list_blocking": {
        "fn": tool_list_blocking,
        "args": {},
        "description": "Sessions currently blocked on a lock, paired with the session "
        "blocking them.",
    },
    "list_long_running": {
        "fn": tool_list_long_running,
        "args": {"seconds": "float threshold, default 30"},
        "description": "Sessions whose current query has been running longer than the "
        "threshold.",
    },
    "explain_query": {
        "fn": tool_explain_query,
        "args": {"sql": "the statement to plan"},
        "description": "EXPLAIN a statement without executing it, returning the plan "
        "and the deterministic analyser's findings. Never runs the statement.",
    },
    "get_settings": {
        "fn": tool_get_settings,
        "args": {},
        "description": "Values of the planner and autovacuum settings that most often "
        "explain the behaviour above.",
    },
}


def tool_catalog_text() -> str:
    """Render the tool list for the system prompt."""
    lines = []
    for name, spec in TOOLS.items():
        args = spec["args"]
        arg_text = ", ".join(f"{k} ({v})" for k, v in args.items()) if args else "no arguments"
        lines.append(f"- {name}({arg_text})\n    {spec['description']}")
    return "\n".join(lines)


async def call_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {
            "error": f"no tool named {name!r}. Available: {', '.join(sorted(TOOLS))}"
        }
    try:
        return await spec["fn"](ctx, **(arguments or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:
        log.exception("tool %s failed", name)
        return {"error": f"{name} failed: {exc}"}
