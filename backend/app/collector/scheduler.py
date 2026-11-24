"""The polling loop: one asyncio task per enabled connection.

Each tick reads the instance counters and the top of pg_stat_statements,
persists a snapshot to the meta database, computes deltas against the previous
tick, and publishes the result to SSE subscribers.

Failure handling matters more than the happy path here. A target that is down,
slow, or has had its credentials rotated must not take down the collector for
every other target, and must not spin: errors are recorded on the connection
row and the loop backs off exponentially up to a ceiling.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update

from app.collector import queries as q
from app.collector.broadcast import Event, broadcaster
from app.config import get_settings
from app.db.meta import session_scope
from app.db.models import Connection, MetricSnapshot, StatementSnapshot
from app.db.target import TargetPool, TargetUnavailable, registry, rows_to_dicts

log = logging.getLogger(__name__)

TOP_STATEMENT_LIMIT = 50
BACKOFF_CEILING_SECONDS = 300.0


class ConnectionCollector:
    """Polls one target database."""

    def __init__(self, connection_id: uuid.UUID, name: str) -> None:
        self.connection_id = connection_id
        self.name = name
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._failures = 0
        # Previous cumulative counters, kept in memory so a delta can be
        # produced on the very next tick rather than after a database round
        # trip for the prior row.
        self._prev_statements: dict[int, tuple[int, float]] = {}
        self._prev_db: dict[str, int] | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name=f"collector-{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- main loop ---------------------------------------------------------

    async def _run(self) -> None:
        settings = get_settings()
        log.info("collector started for %s", self.name)
        while not self._stop.is_set():
            interval = settings.poll_interval_seconds
            try:
                await self._tick()
                self._failures = 0
            except asyncio.CancelledError:
                raise
            except TargetUnavailable as exc:
                self._failures += 1
                interval = self._backoff(settings.poll_interval_seconds)
                await self._record_error(str(exc))
                log.warning(
                    "collector %s unavailable (attempt %d, next in %.0fs): %s",
                    self.name,
                    self._failures,
                    interval,
                    exc,
                )
            except Exception as exc:  # a bug in our own code
                self._failures += 1
                interval = self._backoff(settings.poll_interval_seconds)
                await self._record_error(f"internal error: {exc}")
                log.exception("collector %s failed", self.name)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue
        log.info("collector stopped for %s", self.name)

    def _backoff(self, base: float) -> float:
        return min(base * (2 ** min(self._failures, 8)), BACKOFF_CEILING_SECONDS)

    async def _record_error(self, message: str) -> None:
        try:
            async with session_scope() as session:
                await session.execute(
                    update(Connection)
                    .where(Connection.id == self.connection_id)
                    .values(last_error=message[:2000])
                )
        except Exception:
            log.exception("could not record collector error for %s", self.name)

    # -- one poll ----------------------------------------------------------

    async def _tick(self) -> None:
        async with session_scope() as session:
            conn_row = await session.get(Connection, self.connection_id)
            if conn_row is None or not conn_row.enabled:
                self._stop.set()
                return
            dsn, apply_dsn = conn_row.dsn, conn_row.apply_dsn
            has_pgss = bool(conn_row.extensions.get("pg_stat_statements"))

        pool = await registry.get(self.connection_id, dsn, apply_dsn)

        metrics = await self._collect_metrics(pool)
        statements = await self._collect_statements(pool) if has_pgss else []

        async with session_scope() as session:
            snapshot = MetricSnapshot(connection_id=self.connection_id, **metrics["columns"])
            session.add(snapshot)

            for row in statements:
                session.add(StatementSnapshot(connection_id=self.connection_id, **row))

            await session.execute(
                update(Connection)
                .where(Connection.id == self.connection_id)
                .values(last_polled_at=datetime.now(timezone.utc), last_error=None)
            )

        await broadcaster.publish(
            Event(
                type="metrics",
                connection_id=str(self.connection_id),
                payload={
                    "metrics": metrics["columns"],
                    "rates": metrics["rates"],
                    "top_statements": [
                        {
                            "queryid": s["queryid"],
                            "query": s["query"][:400],
                            "mean_exec_ms": s["mean_exec_ms"],
                            "delta_calls": s["delta_calls"],
                            "delta_total_exec_ms": s["delta_total_exec_ms"],
                        }
                        for s in statements[:10]
                    ],
                },
            )
        )

    async def _collect_metrics(self, pool: TargetPool) -> dict[str, Any]:
        db = dict(await pool.fetchrow(q.DATABASE_STATS) or {})
        act = dict(await pool.fetchrow(q.ACTIVITY_STATS) or {})
        max_conn = await pool.fetchval(q.MAX_CONNECTIONS) or 0

        blks_hit = int(db.get("blks_hit") or 0)
        blks_read = int(db.get("blks_read") or 0)
        total_blks = blks_hit + blks_read
        cache_hit_ratio = (blks_hit / total_blks) if total_blks else 1.0

        columns: dict[str, Any] = {
            "xact_commit": int(db.get("xact_commit") or 0),
            "xact_rollback": int(db.get("xact_rollback") or 0),
            "blks_read": blks_read,
            "blks_hit": blks_hit,
            "tup_returned": int(db.get("tup_returned") or 0),
            "tup_fetched": int(db.get("tup_fetched") or 0),
            "tup_inserted": int(db.get("tup_inserted") or 0),
            "tup_updated": int(db.get("tup_updated") or 0),
            "tup_deleted": int(db.get("tup_deleted") or 0),
            "conflicts": int(db.get("conflicts") or 0),
            "deadlocks": int(db.get("deadlocks") or 0),
            "temp_files": int(db.get("temp_files") or 0),
            "temp_bytes": int(db.get("temp_bytes") or 0),
            "backends_total": int(act.get("backends_total") or 0),
            "backends_active": int(act.get("backends_active") or 0),
            "backends_idle": int(act.get("backends_idle") or 0),
            "backends_idle_in_tx": int(act.get("backends_idle_in_tx") or 0),
            "backends_waiting": int(act.get("backends_waiting") or 0),
            "blocked_sessions": int(act.get("blocked_sessions") or 0),
            "max_connections": int(max_conn),
            "longest_xact_seconds": float(act.get("longest_xact_seconds") or 0.0),
            "database_size_bytes": int(db.get("database_size_bytes") or 0),
            "cache_hit_ratio": cache_hit_ratio,
            "raw": {"stats_reset": str(db.get("stats_reset") or "")},
        }

        rates = self._rates(columns)
        self._prev_db = {
            k: columns[k]
            for k in (
                "xact_commit",
                "xact_rollback",
                "tup_returned",
                "tup_fetched",
                "tup_inserted",
                "tup_updated",
                "tup_deleted",
                "blks_read",
                "blks_hit",
                "deadlocks",
                "temp_bytes",
            )
        }
        return {"columns": columns, "rates": rates}

    def _rates(self, columns: dict[str, Any]) -> dict[str, float]:
        """Per-second rates against the previous tick.

        A negative delta means the server's statistics were reset between
        ticks; the correct response is to report zero for that interval rather
        than a nonsensical negative rate.
        """
        if self._prev_db is None:
            return {}
        interval = get_settings().poll_interval_seconds or 1.0
        rates: dict[str, float] = {}
        for key, prev in self._prev_db.items():
            delta = int(columns[key]) - int(prev)
            rates[f"{key}_per_sec"] = max(delta, 0) / interval
        rates["tps"] = rates.get("xact_commit_per_sec", 0.0) + rates.get(
            "xact_rollback_per_sec", 0.0
        )
        return rates

    async def _collect_statements(self, pool: TargetPool) -> list[dict[str, Any]]:
        rows = rows_to_dicts(await pool.fetch(q.TOP_STATEMENTS, TOP_STATEMENT_LIMIT))
        out: list[dict[str, Any]] = []
        current: dict[int, tuple[int, float]] = {}

        for row in rows:
            queryid = int(row["queryid"])
            calls = int(row["calls"] or 0)
            total_ms = float(row["total_exec_ms"] or 0.0)
            current[queryid] = (calls, total_ms)

            prev_calls, prev_total = self._prev_statements.get(queryid, (0, 0.0))
            delta_calls = calls - prev_calls
            delta_total = total_ms - prev_total
            if delta_calls < 0 or delta_total < 0:
                # pg_stat_statements was reset, or the entry was evicted and
                # re-added. Treat it as a fresh observation.
                delta_calls, delta_total = 0, 0.0

            out.append(
                {
                    "queryid": queryid,
                    "query": row["query"] or "",
                    "calls": calls,
                    "total_exec_ms": total_ms,
                    "mean_exec_ms": float(row["mean_exec_ms"] or 0.0),
                    "max_exec_ms": float(row["max_exec_ms"] or 0.0),
                    "stddev_exec_ms": float(row["stddev_exec_ms"] or 0.0),
                    "rows": int(row["rows"] or 0),
                    "shared_blks_hit": int(row["shared_blks_hit"] or 0),
                    "shared_blks_read": int(row["shared_blks_read"] or 0),
                    "shared_blks_dirtied": int(row["shared_blks_dirtied"] or 0),
                    "shared_blks_written": int(row["shared_blks_written"] or 0),
                    "temp_blks_read": int(row["temp_blks_read"] or 0),
                    "temp_blks_written": int(row["temp_blks_written"] or 0),
                    "wal_bytes": int(row["wal_bytes"] or 0),
                    "delta_calls": delta_calls,
                    "delta_total_exec_ms": delta_total,
                }
            )

        self._prev_statements = current
        out.sort(key=lambda r: r["delta_total_exec_ms"], reverse=True)
        return out


class CollectorManager:
    """Owns the set of per-connection collectors and the retention sweeper."""

    def __init__(self) -> None:
        self._collectors: dict[uuid.UUID, ConnectionCollector] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start_all(self) -> None:
        async with session_scope() as session:
            rows = (
                await session.execute(select(Connection).where(Connection.enabled.is_(True)))
            ).scalars().all()
        for row in rows:
            await self.start(row.id, row.name)
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop(), name="retention-sweeper")

    async def start(self, connection_id: uuid.UUID, name: str) -> None:
        async with self._lock:
            collector = self._collectors.get(connection_id)
            if collector is None:
                collector = ConnectionCollector(connection_id, name)
                self._collectors[connection_id] = collector
            collector.start()

    async def stop(self, connection_id: uuid.UUID) -> None:
        async with self._lock:
            collector = self._collectors.pop(connection_id, None)
        if collector is not None:
            await collector.stop()
        await registry.drop(connection_id)

    async def stop_all(self) -> None:
        async with self._lock:
            collectors = list(self._collectors.values())
            self._collectors.clear()
        for collector in collectors:
            await collector.stop()
        if self._sweeper is not None:
            self._sweeper.cancel()
            self._sweeper = None
        await registry.close_all()

    def status(self) -> dict[str, bool]:
        return {str(cid): c.running for cid, c in self._collectors.items()}

    async def _sweep_loop(self) -> None:
        """Delete snapshots older than the retention window, hourly."""
        settings = get_settings()
        while True:
            try:
                await asyncio.sleep(3600)
                cutoff = datetime.now(timezone.utc) - timedelta(
                    hours=settings.snapshot_retention_hours
                )
                async with session_scope() as session:
                    for model in (MetricSnapshot, StatementSnapshot):
                        await session.execute(
                            delete(model).where(model.captured_at < cutoff)
                        )
                log.info("retention sweep complete (cutoff %s)", cutoff.isoformat())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("retention sweep failed")


manager = CollectorManager()
