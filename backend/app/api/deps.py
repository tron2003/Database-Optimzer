"""Shared FastAPI dependencies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.meta import get_session
from app.db.models import Connection
from app.db.target import TargetPool, registry


@dataclass(slots=True)
class ResolvedConnection:
    row: Connection
    pool: TargetPool

    @property
    def has_pg_stat_statements(self) -> bool:
        return bool((self.row.extensions or {}).get("pg_stat_statements"))

    @property
    def has_pgstattuple(self) -> bool:
        return bool((self.row.extensions or {}).get("pgstattuple"))


async def get_connection_row(
    connection_id: uuid.UUID = Path(...),
    session: AsyncSession = Depends(get_session),
) -> Connection:
    row = await session.get(Connection, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="connection not found")
    return row


async def get_target(
    row: Connection = Depends(get_connection_row),
) -> ResolvedConnection:
    pool = await registry.get(row.id, row.dsn, row.apply_dsn)
    return ResolvedConnection(row=row, pool=pool)
