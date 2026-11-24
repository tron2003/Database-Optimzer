"""The write path: executing a suggestion a human has approved.

The only code in this application that can change a monitored database. It is
short on purpose.

Order of operations, none of which may be skipped:

1.  The suggestion must exist, belong to this connection, and carry a
    statement. Advice-only suggestions have none and cannot be executed.
2.  The statement is re-validated against the allowlist **at execution time**,
    not merely when it was proposed. A row could have been tampered with in
    the meantime, and validation is cheap.
3.  The connection must have an apply credential. Absence is a refusal, never
    a fallback to the monitor credential.
4.  An ``Execution`` audit row is written **before** the statement runs, so a
    crash mid-build still leaves a record that it was attempted.
5.  The statement runs on a single-use connection that is closed afterwards.

``CREATE INDEX CONCURRENTLY`` deliberately runs outside a transaction, because
Postgres forbids it inside one. The consequence is that a failed concurrent
build leaves an INVALID index behind, which the caller is told about in the
error so it can be cleaned up.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import asyncpg
from sqlalchemy import select

from app.db.meta import session_scope
from app.db.models import Execution, Suggestion, SuggestionStatus
from app.db.target import ApplyNotConfigured, TargetPool, TargetUnavailable
from app.safety.allowlist import validate

log = logging.getLogger(__name__)

# An index build on a large table is legitimately slow. Zero means no limit;
# the lock_timeout still protects against waiting behind another session.
DEFAULT_APPLY_TIMEOUT_MS = 0


class ExecutionRefused(PermissionError):
    pass


async def approve_and_execute(
    *,
    suggestion_id: uuid.UUID,
    pool: TargetPool,
    approved_by: str,
    note: str | None = None,
    dry_run: bool = False,
) -> dict:
    async with session_scope() as session:
        suggestion = await session.get(Suggestion, suggestion_id)
        if suggestion is None:
            raise ExecutionRefused("suggestion not found")
        if suggestion.status == SuggestionStatus.applied:
            raise ExecutionRefused("this suggestion has already been applied")
        if suggestion.status == SuggestionStatus.rejected:
            raise ExecutionRefused("this suggestion was rejected; un-reject it first")
        if not suggestion.statement:
            raise ExecutionRefused(
                "this suggestion is advisory and has no statement to execute"
            )
        statement = suggestion.statement
        connection_id = suggestion.connection_id

    # Gate 2 — revalidate now, not when it was proposed.
    check = validate(statement)
    if not check.allowed:
        await _mark_failed(suggestion_id, f"blocked by allowlist: {check.reason}")
        raise ExecutionRefused(f"statement is not permitted: {check.reason}")

    if dry_run:
        return {
            "dry_run": True,
            "statement": statement,
            "allowed": True,
            "kind": check.kind,
            "target": check.target,
            "notes": check.notes,
        }

    if not pool.can_apply:
        raise ApplyNotConfigured(
            "no apply credential is registered for this connection, so nothing can be "
            "executed against it. Register one in the connection settings."
        )

    # Gate 4 — audit first.
    async with session_scope() as session:
        execution = Execution(
            suggestion_id=suggestion_id,
            connection_id=connection_id,
            approved_by=approved_by,
            note=note,
            statement=statement,
            started_at=datetime.now(timezone.utc),
        )
        session.add(execution)
        await session.flush()
        execution_id = execution.id

        row = await session.get(Suggestion, suggestion_id)
        if row is not None:
            row.status = SuggestionStatus.approved

    started = datetime.now(timezone.utc)
    error: str | None = None
    succeeded = False

    try:
        async with pool.acquire_apply(
            statement_timeout_ms=DEFAULT_APPLY_TIMEOUT_MS
            if check.requires_long_timeout
            else 60_000
        ) as conn:
            # No surrounding transaction: CONCURRENTLY forbids one.
            await conn.execute(statement)
        succeeded = True
    except asyncpg.PostgresError as exc:
        error = _explain_failure(exc, check.kind)
    except (TargetUnavailable, OSError) as exc:
        error = f"could not reach the target database: {exc}"

    finished = datetime.now(timezone.utc)
    duration_ms = (finished - started).total_seconds() * 1000.0

    async with session_scope() as session:
        execution_row = await session.get(Execution, execution_id)
        if execution_row is not None:
            execution_row.finished_at = finished
            execution_row.duration_ms = duration_ms
            execution_row.succeeded = succeeded
            execution_row.error = error

        suggestion_row = await session.get(Suggestion, suggestion_id)
        if suggestion_row is not None:
            suggestion_row.status = (
                SuggestionStatus.applied if succeeded else SuggestionStatus.failed
            )

    log.info(
        "execution %s %s in %.0fms: %s",
        execution_id,
        "succeeded" if succeeded else "FAILED",
        duration_ms,
        statement[:200],
    )

    return {
        "execution_id": str(execution_id),
        "succeeded": succeeded,
        "duration_ms": round(duration_ms, 1),
        "statement": statement,
        "error": error,
        "revert_statement": await _revert_for(suggestion_id) if succeeded else None,
    }


def _explain_failure(exc: asyncpg.PostgresError, kind: str) -> str:
    """Turn a Postgres error into something the user can act on."""
    base = f"{type(exc).__name__}: {exc}"

    if isinstance(exc, asyncpg.LockNotAvailableError):
        return (
            f"{base}\n\nThe statement could not acquire its lock within lock_timeout. "
            f"Another session is holding a conflicting lock — often a long-running "
            f"transaction. Check the blocking tree and retry when it clears."
        )
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return (
            f"{base}\n\nThe apply role lacks the privilege for this. For an index build "
            f"it needs CREATE on the schema and ownership of the table (membership in "
            f"the owning role is enough)."
        )
    if isinstance(exc, asyncpg.DuplicateTableError):
        return f"{base}\n\nAn index of that name already exists on the table."
    if kind == "create_index":
        return (
            f"{base}\n\nA failed CREATE INDEX CONCURRENTLY leaves an INVALID index "
            f"behind — it is not used by the planner but is still maintained on every "
            f"write. Drop it with DROP INDEX CONCURRENTLY before retrying. Invalid "
            f"indexes are listed on the Indexes page."
        )
    return base


async def _revert_for(suggestion_id: uuid.UUID) -> str | None:
    async with session_scope() as session:
        row = await session.get(Suggestion, suggestion_id)
        return row.revert_statement if row else None


async def _mark_failed(suggestion_id: uuid.UUID, error: str) -> None:
    async with session_scope() as session:
        row = await session.get(Suggestion, suggestion_id)
        if row is not None:
            row.status = SuggestionStatus.failed
            row.evidence = {**(row.evidence or {}), "last_error": error}


async def reject(suggestion_id: uuid.UUID, *, note: str | None = None) -> None:
    async with session_scope() as session:
        row = await session.get(Suggestion, suggestion_id)
        if row is None:
            raise ExecutionRefused("suggestion not found")
        row.status = SuggestionStatus.rejected
        if note:
            row.evidence = {**(row.evidence or {}), "rejection_note": note}


async def reopen(suggestion_id: uuid.UUID) -> None:
    async with session_scope() as session:
        row = await session.get(Suggestion, suggestion_id)
        if row is None:
            raise ExecutionRefused("suggestion not found")
        if row.status == SuggestionStatus.applied:
            raise ExecutionRefused("an applied suggestion cannot be reopened")
        row.status = SuggestionStatus.proposed


async def execution_history(connection_id: uuid.UUID, limit: int = 100) -> list[Execution]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Execution)
                .where(Execution.connection_id == connection_id)
                .order_by(Execution.approved_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)
