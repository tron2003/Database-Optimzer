"""EXPLAIN capture.

Three layers stand between a submitted statement and the server:

1.  ``validate_explainable`` parses it and refuses ANALYZE for anything that
    is not a bare SELECT, including a SELECT with a data-modifying CTE.
2.  ANALYZE additionally requires the connection to have
    ``allow_explain_analyze`` set, which is off by default and which the UI
    will not let you turn on for an external database without a confirmation.
3.  The statement runs inside an explicit transaction that is **always rolled
    back**, with the session set read-only first.

Any one of these would mostly do. All three are present because the failure
mode — executing a destructive statement against a production database that
someone connected read-only — is not one worth being clever about.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

from app.analysis.plans import analyze_plan
from app.analysis.types import PlanFinding
from app.config import get_settings
from app.db.target import TargetPool, TargetUnavailable
from app.safety.allowlist import validate_explainable

log = logging.getLogger(__name__)


class ExplainRefused(PermissionError):
    """The statement is not permitted to be explained in the requested mode."""


async def capture_plan(
    pool: TargetPool,
    statement: str,
    *,
    analyze: bool,
    allow_analyze: bool,
    buffers: bool = True,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Run EXPLAIN and return the plan plus deterministic findings."""
    check = validate_explainable(statement, analyze=analyze)
    if not check.allowed:
        raise ExplainRefused(check.reason)

    if analyze and not allow_analyze:
        raise ExplainRefused(
            "EXPLAIN ANALYZE executes the statement. It is disabled for this "
            "connection. Enable it in the connection settings only if running this "
            "query for real is acceptable."
        )

    options = ["FORMAT JSON", "VERBOSE"]
    if analyze:
        options += ["ANALYZE", "SETTINGS"]
        if buffers:
            options.append("BUFFERS")
        if get_settings().statement_timeout_ms:
            options.append("WAL")

    sql = f"EXPLAIN ({', '.join(options)}) {statement}"

    try:
        async with pool.acquire() as conn:
            # Belt: the session refuses writes regardless of what got past the
            # parser. Braces: the transaction is rolled back either way, so a
            # statement that somehow wrote has nothing to commit.
            tx = conn.transaction()
            await tx.start()
            try:
                await conn.execute("SET TRANSACTION READ ONLY")
                if timeout_ms:
                    await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                raw = await conn.fetchval(sql)
            finally:
                await tx.rollback()
    except asyncpg.PostgresError as exc:
        raise TargetUnavailable(f"EXPLAIN failed: {type(exc).__name__}: {exc}") from exc

    plan_json = json.loads(raw) if isinstance(raw, str) else raw
    findings, summary = analyze_plan(plan_json)

    return {
        "plan": plan_json,
        "analyzed": analyze,
        "findings": [f.as_dict() for f in findings],
        "summary": summary,
        "planning_ms": summary.get("planning_ms"),
        "execution_ms": summary.get("execution_ms"),
        "notes": check.notes,
    }


def findings_from_dicts(rows: list[dict[str, Any]]) -> list[PlanFinding]:
    """Rehydrate stored findings for the LLM prompt."""
    from app.db.models import Severity

    out: list[PlanFinding] = []
    for row in rows:
        try:
            severity = Severity(row.get("severity", "info"))
        except ValueError:
            severity = Severity.info
        out.append(
            PlanFinding(
                code=row.get("code", ""),
                severity=severity,
                node=row.get("node", ""),
                message=row.get("message", ""),
                detail=row.get("detail") or {},
            )
        )
    return out
