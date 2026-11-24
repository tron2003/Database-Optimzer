"""Connection registry endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_connection_row
from app.collector.scheduler import manager
from app.db.meta import get_session
from app.db.models import Connection, ConnectionKind
from app.db.target import probe, redact_dsn, registry
from app.schemas.api import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    ProbeRequest,
    ProbeResult,
)

router = APIRouter(prefix="/connections", tags=["connections"])


def _to_out(row: Connection) -> ConnectionOut:
    status = manager.status()
    return ConnectionOut(
        id=row.id,
        name=row.name,
        kind=row.kind.value if hasattr(row.kind, "value") else str(row.kind),
        dsn_redacted=redact_dsn(row.dsn),
        can_apply=row.apply_dsn is not None,
        allow_explain_analyze=row.allow_explain_analyze,
        enabled=row.enabled,
        created_at=row.created_at,
        last_polled_at=row.last_polled_at,
        last_error=row.last_error,
        server_version=row.server_version,
        extensions=row.extensions or {},
        collector_running=status.get(str(row.id), False),
    )


@router.get("", response_model=list[ConnectionOut])
async def list_connections(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(select(Connection).order_by(Connection.created_at))
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("/probe", response_model=ProbeResult)
async def probe_dsn(payload: ProbeRequest):
    """Check a DSN before registering it.

    Reports what will and will not work — a missing pg_stat_statements, a
    superuser DSN where a read-only role was expected — so the problem
    surfaces here rather than as an empty dashboard later.
    """
    return ProbeResult(**await probe(payload.dsn))


@router.post("", response_model=ConnectionOut, status_code=201)
async def create_connection(
    payload: ConnectionCreate, session: AsyncSession = Depends(get_session)
):
    result = await probe(payload.dsn)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=f"could not connect: {result.get('error')}"
        )

    row = Connection(
        name=payload.name,
        kind=ConnectionKind(payload.kind),
        dsn=payload.dsn,
        apply_dsn=payload.apply_dsn,
        allow_explain_analyze=payload.allow_explain_analyze,
        server_version=result.get("server_version"),
        extensions=result.get("extensions") or {},
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"a connection named {payload.name!r} already exists"
        ) from None
    await session.refresh(row)

    await manager.start(row.id, row.name)
    return _to_out(row)


@router.get("/{connection_id}", response_model=ConnectionOut)
async def get_connection(row: Connection = Depends(get_connection_row)):
    return _to_out(row)


@router.patch("/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    payload: ConnectionUpdate,
    row: Connection = Depends(get_connection_row),
    session: AsyncSession = Depends(get_session),
):
    if payload.name is not None:
        row.name = payload.name
    if payload.allow_explain_analyze is not None:
        row.allow_explain_analyze = payload.allow_explain_analyze
    if payload.apply_dsn is not None:
        # An empty string clears it, which is how a connection is downgraded
        # back to observation-only.
        row.apply_dsn = payload.apply_dsn or None
    if payload.enabled is not None:
        row.enabled = payload.enabled

    await session.commit()
    await session.refresh(row)

    # The pool caches the DSNs, so drop it and let it be rebuilt.
    await registry.drop(row.id)
    if row.enabled:
        await manager.start(row.id, row.name)
    else:
        await manager.stop(row.id)
    return _to_out(row)


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    row: Connection = Depends(get_connection_row),
    session: AsyncSession = Depends(get_session),
):
    connection_id: uuid.UUID = row.id
    await manager.stop(connection_id)
    await session.delete(row)
    await session.commit()
    return None
