"""Metrics and health endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import health as health_mod
from app.api.deps import ResolvedConnection, get_target
from app.collector import queries as q
from app.config import get_settings
from app.db.meta import get_session
from app.db.models import MetricSnapshot
from app.db.target import rows_to_dicts
from app.schemas.api import HealthOut, MetricPoint, MetricsSeries

router = APIRouter(prefix="/connections/{connection_id}", tags=["metrics"])


def _rate(current: int, previous: int, elapsed: float) -> float:
    """Per-second rate between two cumulative counter readings.

    A negative delta means the server's statistics were reset between the two
    snapshots. Reporting zero for that interval is correct; reporting a
    negative rate would put a spike through every chart on the dashboard.
    """
    return max(current - previous, 0) / elapsed


@router.get("/metrics/series", response_model=MetricsSeries)
async def metrics_series(
    minutes: int = Query(default=30, ge=1, le=24 * 60),
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    """Stored snapshots, converted from cumulative counters to per-second rates."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = (
        await session.execute(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.connection_id == target.row.id,
                MetricSnapshot.captured_at >= since,
            )
            .order_by(MetricSnapshot.captured_at)
        )
    ).scalars().all()

    points: list[MetricPoint] = []
    previous: MetricSnapshot | None = None

    for row in rows:
        tps = temp_rate = returned_rate = 0.0
        if previous is not None:
            elapsed = (row.captured_at - previous.captured_at).total_seconds()
            if elapsed > 0:
                tps = _rate(row.xact_commit, previous.xact_commit, elapsed) + _rate(
                    row.xact_rollback, previous.xact_rollback, elapsed
                )
                temp_rate = _rate(row.temp_bytes, previous.temp_bytes, elapsed)
                returned_rate = _rate(row.tup_returned, previous.tup_returned, elapsed)

        points.append(
            MetricPoint(
                captured_at=row.captured_at,
                tps=round(tps, 2),
                cache_hit_ratio=row.cache_hit_ratio,
                backends_active=row.backends_active,
                backends_total=row.backends_total,
                backends_idle_in_tx=row.backends_idle_in_tx,
                blocked_sessions=row.blocked_sessions,
                longest_xact_seconds=row.longest_xact_seconds,
                database_size_bytes=row.database_size_bytes,
                temp_bytes_per_sec=round(temp_rate, 2),
                rows_returned_per_sec=round(returned_rate, 2),
            )
        )
        previous = row

    return MetricsSeries(
        connection_id=target.row.id,
        points=points,
        interval_seconds=get_settings().poll_interval_seconds,
    )


@router.get("/metrics/current")
async def metrics_current(target: ResolvedConnection = Depends(get_target)):
    """A live read, bypassing the collector's cadence."""
    db = dict(await target.pool.fetchrow(q.DATABASE_STATS) or {})
    act = dict(await target.pool.fetchrow(q.ACTIVITY_STATS) or {})
    max_conn = await target.pool.fetchval(q.MAX_CONNECTIONS) or 0

    hit = int(db.get("blks_hit") or 0)
    read = int(db.get("blks_read") or 0)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "database": {k: (v if not isinstance(v, datetime) else v.isoformat()) for k, v in db.items()},
        "activity": dict(act),
        "max_connections": int(max_conn),
        "cache_hit_ratio": (hit / (hit + read)) if (hit + read) else 1.0,
    }


@router.get("/health", response_model=HealthOut)
async def health(
    target: ResolvedConnection = Depends(get_target),
    session: AsyncSession = Depends(get_session),
):
    db = dict(await target.pool.fetchrow(q.DATABASE_STATS) or {})
    act = dict(await target.pool.fetchrow(q.ACTIVITY_STATS) or {})
    max_conn = await target.pool.fetchval(q.MAX_CONNECTIONS) or 0

    hit = int(db.get("blks_hit") or 0)
    read = int(db.get("blks_read") or 0)
    metrics = {
        "cache_hit_ratio": (hit / (hit + read)) if (hit + read) else 1.0,
        "backends_total": int(act.get("backends_total") or 0),
        "backends_idle_in_tx": int(act.get("backends_idle_in_tx") or 0),
        "blocked_sessions": int(act.get("blocked_sessions") or 0),
        "longest_xact_seconds": float(act.get("longest_xact_seconds") or 0.0),
        "max_connections": int(max_conn),
        "deadlocks": int(db.get("deadlocks") or 0),
        "temp_bytes": int(db.get("temp_bytes") or 0),
    }

    # Rates need two points; take them from the last two stored snapshots.
    rates: dict[str, float] = {}
    rows = (
        await session.execute(
            select(MetricSnapshot)
            .where(MetricSnapshot.connection_id == target.row.id)
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(2)
        )
    ).scalars().all()
    if len(rows) == 2:
        newer, older = rows[0], rows[1]
        elapsed = (newer.captured_at - older.captured_at).total_seconds()
        if elapsed > 0:
            rates = {
                "xact_commit_per_sec": max(newer.xact_commit - older.xact_commit, 0) / elapsed,
                "xact_rollback_per_sec": max(newer.xact_rollback - older.xact_rollback, 0)
                / elapsed,
                "temp_bytes_per_sec": max(newer.temp_bytes - older.temp_bytes, 0) / elapsed,
            }

    result = health_mod.evaluate(metrics, rates)
    return HealthOut(**result, captured_at=datetime.now(timezone.utc))


@router.get("/activity/blocking")
async def blocking(target: ResolvedConnection = Depends(get_target)):
    return {"blocking": rows_to_dicts(await target.pool.fetch(q.BLOCKING_TREE))}


@router.get("/activity/long-running")
async def long_running(
    seconds: float = Query(default=30.0, ge=1.0, le=86_400.0),
    target: ResolvedConnection = Depends(get_target),
):
    return {
        "threshold_seconds": seconds,
        "sessions": rows_to_dicts(await target.pool.fetch(q.LONG_RUNNING, seconds)),
    }


@router.get("/settings")
async def server_settings(target: ResolvedConnection = Depends(get_target)):
    return {
        "settings": rows_to_dicts(
            await target.pool.fetch(q.NOTABLE_SETTINGS, q.NOTABLE_SETTING_NAMES)
        )
    }
