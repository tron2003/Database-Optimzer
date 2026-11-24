"""Slow-query browsing, EXPLAIN capture, and LLM review."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedConnection, get_target
from app.collector import queries as q
from app.db.meta import get_session
from app.db.models import QueryPlan, StatementSnapshot
from app.db.target import TargetUnavailable, rows_to_dicts
from app.llm.advisor import interpret_plan, review_query
from app.llm.base import LLMError
from app.schemas.api import ExplainRequest, ExplainResponse, SlowQueryOut
from app.services.analysis import gather_catalog, persist_suggestions
from app.services.explain import ExplainRefused, capture_plan

log = logging.getLogger(__name__)

router = APIRouter(prefix="/connections/{connection_id}", tags=["queries"])


@router.get("/queries", response_model=list[SlowQueryOut])
async def slow_queries(
    limit: int = Query(default=25, ge=1, le=200),
    order: str = Query(default="total", pattern="^(total|mean|calls|recent)$"),
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    """Top statements.

    ``order=recent`` ranks by execution time accumulated since the previous
    poll, which is the only ordering that answers "what is slow *now*" —
    the others are cumulative since the last statistics reset and are
    dominated by whatever has been running longest.
    """
    if not target.has_pg_stat_statements:
        raise HTTPException(
            status_code=409,
            detail=(
                "pg_stat_statements is not installed on this database. Add it to "
                "shared_preload_libraries, restart, and run CREATE EXTENSION "
                "pg_stat_statements."
            ),
        )

    if order == "recent":
        rows = (
            await session.execute(
                select(StatementSnapshot)
                .where(StatementSnapshot.connection_id == target.row.id)
                .order_by(
                    StatementSnapshot.captured_at.desc(),
                    StatementSnapshot.delta_total_exec_ms.desc(),
                )
                .limit(limit)
            )
        ).scalars().all()
        return [
            SlowQueryOut(
                queryid=str(r.queryid),
                query=r.query,
                calls=r.calls,
                total_exec_ms=r.total_exec_ms,
                mean_exec_ms=r.mean_exec_ms,
                max_exec_ms=r.max_exec_ms,
                stddev_exec_ms=r.stddev_exec_ms,
                rows=r.rows,
                shared_blks_hit=r.shared_blks_hit,
                shared_blks_read=r.shared_blks_read,
                temp_blks_written=r.temp_blks_written,
                delta_calls=r.delta_calls,
                delta_total_exec_ms=r.delta_total_exec_ms,
                cache_hit_ratio=_hit_ratio(r.shared_blks_hit, r.shared_blks_read),
                captured_at=r.captured_at,
            )
            for r in rows
        ]

    live = rows_to_dicts(await target.pool.fetch(q.TOP_STATEMENTS, limit))
    key = {"total": "total_exec_ms", "mean": "mean_exec_ms", "calls": "calls"}[order]
    live.sort(key=lambda r: float(r.get(key) or 0), reverse=True)

    return [
        SlowQueryOut(
            queryid=str(r["queryid"]),
            query=r["query"] or "",
            calls=int(r["calls"] or 0),
            total_exec_ms=float(r["total_exec_ms"] or 0),
            mean_exec_ms=float(r["mean_exec_ms"] or 0),
            max_exec_ms=float(r["max_exec_ms"] or 0),
            stddev_exec_ms=float(r["stddev_exec_ms"] or 0),
            rows=int(r["rows"] or 0),
            shared_blks_hit=int(r["shared_blks_hit"] or 0),
            shared_blks_read=int(r["shared_blks_read"] or 0),
            temp_blks_written=int(r["temp_blks_written"] or 0),
            cache_hit_ratio=_hit_ratio(
                int(r["shared_blks_hit"] or 0), int(r["shared_blks_read"] or 0)
            ),
        )
        for r in live
    ]


def _hit_ratio(hit: int, read: int) -> float:
    total = hit + read
    return (hit / total) if total else 1.0


@router.post("/explain", response_model=ExplainResponse)
async def explain(
    payload: ExplainRequest,
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await capture_plan(
            target.pool,
            payload.sql,
            analyze=payload.analyze,
            allow_analyze=target.row.allow_explain_analyze,
        )
    except ExplainRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TargetUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    plan_row = QueryPlan(
        connection_id=target.row.id,
        sql_text=payload.sql,
        analyzed=result["analyzed"],
        plan=result["plan"],
        planning_ms=result.get("planning_ms"),
        execution_ms=result.get("execution_ms"),
        findings=result["findings"],
    )
    session.add(plan_row)
    await session.commit()
    await session.refresh(plan_row)

    response = ExplainResponse(
        plan_id=plan_row.id,
        analyzed=result["analyzed"],
        plan=result["plan"],
        findings=result["findings"],
        summary=result["summary"],
        planning_ms=result.get("planning_ms"),
        execution_ms=result.get("execution_ms"),
        notes=result.get("notes") or [],
    )

    if not payload.interpret:
        return response

    # The LLM is strictly additive: the plan and its deterministic findings
    # are already returned above, so a provider failure degrades the response
    # rather than failing the request.
    try:
        catalog = await gather_catalog(target.pool)
        from app.services.explain import findings_from_dicts

        interpretation, drafts, rejected = await interpret_plan(
            statement=payload.sql,
            plan_json=result["plan"],
            findings=findings_from_dicts(result["findings"]),
            tables=catalog["schema"],
        )
        counts = await persist_suggestions(target.row.id, drafts) if drafts else {}

        text = interpretation.summary
        if interpretation.explanation:
            text += "\n\n" + interpretation.explanation
        if interpretation.insufficient_evidence:
            text += (
                "\n\n_The model reported that the plan does not support a confident "
                "conclusion._"
            )

        plan_row.interpretation = text
        await session.commit()

        response.interpretation = text
        response.new_suggestions = counts.get("created", 0)
        response.rejected_proposals = rejected
    except LLMError as exc:
        response.interpretation_error = str(exc)
    except Exception as exc:
        log.exception("plan interpretation failed")
        response.interpretation_error = f"interpretation failed: {exc}"

    return response


@router.post("/queries/{queryid}/review")
async def review(
    queryid: str,
    target: ResolvedConnection = Depends(get_target),
):
    """Ask the LLM to review one statement from pg_stat_statements."""
    try:
        qid = int(queryid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="queryid must be an integer") from exc

    row = dict(await target.pool.fetchrow(q.STATEMENT_BY_ID, qid) or {})
    if not row:
        raise HTTPException(status_code=404, detail="no such statement in pg_stat_statements")

    catalog = await gather_catalog(target.pool)
    try:
        result, drafts, rejected = await review_query(
            statement=row["query"],
            stats={
                "calls": row.get("calls"),
                "total_exec_ms": round(float(row.get("total_exec_ms") or 0), 1),
                "mean_exec_ms": round(float(row.get("mean_exec_ms") or 0), 3),
                "rows": row.get("rows"),
            },
            tables=catalog["schema"],
        )
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    counts = await persist_suggestions(target.row.id, drafts) if drafts else {}
    return {
        "queryid": queryid,
        "summary": result.summary,
        "likely_cause": result.likely_cause,
        "rewrite_sql": result.rewrite_sql,
        "insufficient_evidence": result.insufficient_evidence,
        "advice": [a.model_dump() for a in result.advice],
        "new_suggestions": counts.get("created", 0),
        "rejected_proposals": rejected,
    }


@router.get("/plans")
async def recent_plans(
    limit: int = Query(default=20, ge=1, le=100),
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(QueryPlan)
            .where(QueryPlan.connection_id == target.row.id)
            .order_by(QueryPlan.captured_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(r.id),
            "captured_at": r.captured_at.isoformat(),
            "sql_text": r.sql_text[:2000],
            "analyzed": r.analyzed,
            "planning_ms": r.planning_ms,
            "execution_ms": r.execution_ms,
            "finding_count": len(r.findings or []),
            "has_interpretation": bool(r.interpretation),
        }
        for r in rows
    ]


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    import uuid as _uuid

    try:
        pid = _uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid plan id") from exc

    row = await session.get(QueryPlan, pid)
    if row is None or row.connection_id != target.row.id:
        raise HTTPException(status_code=404, detail="plan not found")
    return {
        "id": str(row.id),
        "captured_at": row.captured_at.isoformat(),
        "sql_text": row.sql_text,
        "analyzed": row.analyzed,
        "plan": row.plan,
        "findings": row.findings,
        "planning_ms": row.planning_ms,
        "execution_ms": row.execution_ms,
        "interpretation": row.interpretation,
    }
