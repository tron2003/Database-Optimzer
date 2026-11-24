"""Connection management for the databases being monitored.

Design notes, because this is where the safety story lives:

*   Two distinct credentials per target. The monitor DSN is pooled and used for
    every read; the apply DSN is **not** pooled and is opened only for the
    lifetime of a single approved statement, then closed.
*   Every monitor session sets statement_timeout, lock_timeout,
    idle_in_transaction_session_timeout and ``default_transaction_read_only``
    on connect. The server-side role should also be read-only; this is the
    second of the two locks.
*   Pools are capped hard (``target_pool_max``, default 3). A monitoring tool
    that exhausts the connection slots of the database it is watching has
    caused the incident it was installed to prevent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.config import get_settings

log = logging.getLogger(__name__)


class TargetUnavailable(RuntimeError):
    """The target database could not be reached or refused the connection."""


class ApplyNotConfigured(RuntimeError):
    """An execution was requested on a connection with no apply credential."""


def redact_dsn(dsn: str) -> str:
    """Strip the password so a DSN can be logged or returned over the API."""
    try:
        scheme, rest = dsn.split("://", 1)
    except ValueError:
        return "***"
    if "@" not in rest:
        return dsn
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


def _normalise(dsn: str) -> str:
    """asyncpg does not understand SQLAlchemy's ``+driver`` DSN suffix."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgres://"
    )


async def _configure_monitor_session(conn: asyncpg.Connection) -> None:
    settings = get_settings()
    for stmt in settings.session_setup_sql():
        await conn.execute(stmt)
    # Client-side half of the read-only guarantee. The role should carry
    # default_transaction_read_only as well; this covers targets where the
    # operator could not change the role.
    await conn.execute("SET default_transaction_read_only = on")


class TargetPool:
    """A capped, read-only connection pool against one monitored database."""

    def __init__(self, connection_id: uuid.UUID, dsn: str, apply_dsn: str | None) -> None:
        self.connection_id = connection_id
        self._dsn = _normalise(dsn)
        self._apply_dsn = _normalise(apply_dsn) if apply_dsn else None
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def can_apply(self) -> bool:
        return self._apply_dsn is not None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is not None and not self._pool._closed:  # noqa: SLF001
            return self._pool
        async with self._lock:
            if self._pool is not None and not self._pool._closed:  # noqa: SLF001
                return self._pool
            settings = get_settings()
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._dsn,
                    min_size=1,
                    max_size=settings.target_pool_max,
                    timeout=10.0,
                    command_timeout=settings.statement_timeout_ms / 1000.0,
                    setup=_configure_monitor_session,
                    server_settings={"application_name": "dbopt-monitor"},
                )
            except (OSError, asyncpg.PostgresError) as exc:
                raise TargetUnavailable(str(exc)) from exc
        return self._pool

    # -- reads --------------------------------------------------------------

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        pool = await self._ensure_pool()
        try:
            return await pool.fetch(query, *args)
        except asyncpg.PostgresError as exc:
            raise TargetUnavailable(f"{type(exc).__name__}: {exc}") from exc

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        pool = await self._ensure_pool()
        try:
            return await pool.fetchrow(query, *args)
        except asyncpg.PostgresError as exc:
            raise TargetUnavailable(f"{type(exc).__name__}: {exc}") from exc

    async def fetchval(self, query: str, *args: Any) -> Any:
        pool = await self._ensure_pool()
        try:
            return await pool.fetchval(query, *args)
        except asyncpg.PostgresError as exc:
            raise TargetUnavailable(f"{type(exc).__name__}: {exc}") from exc

    @asynccontextmanager
    async def acquire(self):
        """Borrow a read-only connection, e.g. for EXPLAIN inside one session."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            yield conn

    # -- the write path -----------------------------------------------------

    @asynccontextmanager
    async def acquire_apply(self, *, statement_timeout_ms: int | None = None):
        """Open a single-use privileged connection.

        Deliberately not pooled. It exists for the duration of one approved
        statement and is closed in the ``finally``, so there is never an idle
        writable connection sitting against a monitored database.

        ``CREATE INDEX CONCURRENTLY`` cannot run inside a transaction block, so
        callers must not wrap the statement in ``conn.transaction()``.
        """
        if self._apply_dsn is None:
            raise ApplyNotConfigured(
                "this connection has no apply credential; register one to execute changes"
            )
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(
                dsn=self._apply_dsn,
                timeout=10.0,
                server_settings={"application_name": "dbopt-apply"},
            )
            settings = get_settings()
            await conn.execute(f"SET lock_timeout = {settings.lock_timeout_ms}")
            # An index build on a large table legitimately exceeds the monitor
            # timeout, so this path gets its own, explicit budget.
            await conn.execute(f"SET statement_timeout = {statement_timeout_ms or 0}")
            yield conn
        except (OSError, asyncpg.PostgresError) as exc:
            raise TargetUnavailable(str(exc)) from exc
        finally:
            if conn is not None:
                await conn.close(timeout=5)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class TargetRegistry:
    """Process-wide registry of target pools, keyed by connection id."""

    def __init__(self) -> None:
        self._pools: dict[uuid.UUID, TargetPool] = {}
        self._lock = asyncio.Lock()

    async def get(
        self, connection_id: uuid.UUID, dsn: str, apply_dsn: str | None
    ) -> TargetPool:
        async with self._lock:
            pool = self._pools.get(connection_id)
            if pool is None:
                pool = TargetPool(connection_id, dsn, apply_dsn)
                self._pools[connection_id] = pool
            return pool

    async def drop(self, connection_id: uuid.UUID) -> None:
        async with self._lock:
            pool = self._pools.pop(connection_id, None)
        if pool is not None:
            await pool.close()

    async def close_all(self) -> None:
        async with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        await asyncio.gather(*(p.close() for p in pools), return_exceptions=True)


registry = TargetRegistry()


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

REQUIRED_READ_PRIVILEGES = ("pg_monitor",)


async def probe(dsn: str) -> dict[str, Any]:
    """Validate a DSN and report what the tool will and will not be able to do.

    Called when a connection is registered, so the UI can say up front that
    pg_stat_statements is missing rather than silently showing an empty page.
    """
    conn: asyncpg.Connection | None = None
    try:
        conn = await asyncpg.connect(dsn=_normalise(dsn), timeout=10.0)
        version = await conn.fetchval("SHOW server_version")
        is_superuser = await conn.fetchval("SELECT usesuper FROM pg_user WHERE usename = current_user")
        has_monitor = await conn.fetchval(
            "SELECT pg_has_role(current_user, 'pg_monitor', 'member')"
        )
        ext_rows = await conn.fetch("SELECT extname, extversion FROM pg_extension")
        extensions = {r["extname"]: r["extversion"] for r in ext_rows}
        read_only = await conn.fetchval("SHOW default_transaction_read_only")
        can_write = not (read_only == "on")
        return {
            "ok": True,
            "server_version": version,
            "extensions": extensions,
            "has_pg_stat_statements": "pg_stat_statements" in extensions,
            "has_pgstattuple": "pgstattuple" in extensions,
            "has_pg_monitor": bool(has_monitor),
            "is_superuser": bool(is_superuser),
            "session_is_writable": can_write,
            "warnings": _probe_warnings(
                extensions, bool(has_monitor), bool(is_superuser), can_write
            ),
        }
    except (OSError, asyncpg.PostgresError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn is not None:
            await conn.close(timeout=5)


def _probe_warnings(
    extensions: dict[str, str], has_monitor: bool, is_superuser: bool, writable: bool
) -> list[str]:
    warnings: list[str] = []
    if "pg_stat_statements" not in extensions:
        warnings.append(
            "pg_stat_statements is not installed; slow-query analysis will be unavailable. "
            "Add it to shared_preload_libraries and run CREATE EXTENSION."
        )
    if "pgstattuple" not in extensions:
        warnings.append(
            "pgstattuple is not installed; bloat figures will be estimates from "
            "pg_stat_user_tables rather than exact measurements."
        )
    if not has_monitor:
        warnings.append(
            "This role is not a member of pg_monitor; some statistics will be hidden "
            "or show NULL for other users' sessions."
        )
    if is_superuser:
        warnings.append(
            "This role is a superuser. Use a dedicated read-only role for monitoring: "
            "a superuser DSN removes every guardrail this tool relies on."
        )
    if writable:
        warnings.append(
            "This role can write. Set default_transaction_read_only = on for it, "
            "so the monitor path is read-only at the server and not only in the client."
        )
    return warnings


def row_to_dict(record: asyncpg.Record | None) -> dict[str, Any]:
    return dict(record) if record is not None else {}


def rows_to_dicts(records: Sequence[asyncpg.Record]) -> list[dict[str, Any]]:
    return [dict(r) for r in records]
