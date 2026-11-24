"""Suggestions, the approval queue, and the audit log."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedConnection, get_target
from app.db.meta import get_session
from app.db.models import Execution, Suggestion, SuggestionStatus
from app.db.target import ApplyNotConfigured, TargetUnavailable
from app.schemas.api import (
    AnalysisRunOut,
    ApproveRequest,
    ExecutionOut,
    RejectRequest,
    SuggestionOut,
)
from app.services import execution as execution_service
from app.services.analysis import run_analysis
from app.services.execution import ExecutionRefused

router = APIRouter(prefix="/connections/{connection_id}", tags=["suggestions"])


def _to_out(row: Suggestion) -> SuggestionOut:
    return SuggestionOut(
        id=row.id,
        connection_id=row.connection_id,
        created_at=row.created_at,
        kind=row.kind.value,
        source=row.source.value,
        severity=row.severity.value,
        status=row.status.value,
        title=row.title,
        rationale=row.rationale,
        statement=row.statement,
        revert_statement=row.revert_statement,
        target_relation=row.target_relation,
        estimated_benefit=row.estimated_benefit,
        estimated_cost=row.estimated_cost,
        evidence=row.evidence or {},
    )


@router.post("/analyze", response_model=AnalysisRunOut)
async def analyze(target: ResolvedConnection = Depends(get_target)):
    """Run the deterministic analysis. No LLM involved."""
    try:
        return AnalysisRunOut(**await run_analysis(target.row.id, target.pool))
    except TargetUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/suggestions", response_model=list[SuggestionOut])
async def list_suggestions(
    status: str | None = Query(default=None),
    source: str | None = Query(default=None),
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Suggestion).where(Suggestion.connection_id == target.row.id)
    if status:
        try:
            stmt = stmt.where(Suggestion.status == SuggestionStatus(status))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"unknown status {status!r}") from exc
    if source:
        stmt = stmt.where(Suggestion.source == source)

    rows = (await session.execute(stmt.order_by(Suggestion.created_at.desc()))).scalars().all()

    # Severity first, newest first within a severity.
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    rows = sorted(rows, key=lambda r: (order.get(r.severity.value, 9), -r.created_at.timestamp()))
    return [_to_out(r) for r in rows]


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionOut)
async def get_suggestion(
    suggestion_id: uuid.UUID,
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(Suggestion, suggestion_id)
    if row is None or row.connection_id != target.row.id:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return _to_out(row)


@router.post("/suggestions/{suggestion_id}/approve")
async def approve(
    suggestion_id: uuid.UUID,
    payload: ApproveRequest,
    target: ResolvedConnection = Depends(get_target),
):
    """Execute an approved suggestion.

    The only endpoint in this application that can change a monitored
    database. ``dry_run`` returns the validated statement without running it.
    """
    try:
        return await execution_service.approve_and_execute(
            suggestion_id=suggestion_id,
            pool=target.pool,
            approved_by=payload.approved_by,
            note=payload.note,
            dry_run=payload.dry_run,
        )
    except ApplyNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TargetUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/suggestions/{suggestion_id}/reject", status_code=204)
async def reject(suggestion_id: uuid.UUID, payload: RejectRequest):
    try:
        await execution_service.reject(suggestion_id, note=payload.note)
    except ExecutionRefused as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return None


@router.post("/suggestions/{suggestion_id}/reopen", status_code=204)
async def reopen(suggestion_id: uuid.UUID):
    try:
        await execution_service.reopen(suggestion_id)
    except ExecutionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return None


@router.get("/executions", response_model=list[ExecutionOut])
async def executions(
    limit: int = Query(default=100, ge=1, le=500),
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Execution)
            .where(Execution.connection_id == target.row.id)
            .order_by(Execution.approved_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [ExecutionOut.model_validate(r) for r in rows]
