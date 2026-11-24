"""Full-database analysis run: gather catalog facts, derive suggestions, persist.

Suggestions are upserted on their fingerprint rather than inserted, so running
analysis repeatedly keeps one row per distinct finding and preserves whatever
decision a human already made about it. A suggestion the user rejected stays
rejected; one already applied is not re-proposed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.analysis.indexes import analyze_indexes, stats_age_hours
from app.analysis.maintenance import analyze_maintenance
from app.analysis.types import SuggestionDraft, merge_drafts
from app.collector import queries as q
from app.db.meta import session_scope
from app.db.models import Connection, Suggestion, SuggestionStatus
from app.db.target import TargetPool, rows_to_dicts

log = logging.getLogger(__name__)

TABLE_LIMIT = 200
SCHEMA_LIMIT = 100


async def gather_catalog(pool: TargetPool) -> dict[str, Any]:
    """One read of everything the analysers need."""
    table_stats = rows_to_dicts(await pool.fetch(q.TABLE_STATS, TABLE_LIMIT))
    index_stats = rows_to_dicts(await pool.fetch(q.INDEX_STATS))
    unindexed_fks = rows_to_dicts(await pool.fetch(q.UNINDEXED_FOREIGN_KEYS))
    schema = rows_to_dicts(await pool.fetch(q.SCHEMA_OVERVIEW, SCHEMA_LIMIT))
    settings_rows = rows_to_dicts(await pool.fetch(q.NOTABLE_SETTINGS, q.NOTABLE_SETTING_NAMES))
    db_row = dict(await pool.fetchrow(q.DATABASE_STATS) or {})

    return {
        "table_stats": table_stats,
        "index_stats": index_stats,
        "unindexed_fks": unindexed_fks,
        "schema": schema,
        "settings": settings_rows,
        "stats_reset": db_row.get("stats_reset"),
    }


def derive_suggestions(catalog: dict[str, Any]) -> list[SuggestionDraft]:
    age = stats_age_hours(catalog.get("stats_reset"))
    drafts = analyze_indexes(
        index_stats=catalog["index_stats"],
        table_stats=catalog["table_stats"],
        unindexed_fks=catalog["unindexed_fks"],
        stats_age_hours=age,
    )
    drafts += analyze_maintenance(catalog["table_stats"])
    # Several checks can land on the same statement; merge before persisting so
    # the strongest reasoning survives rather than whichever ran last.
    return merge_drafts(drafts)


async def persist_suggestions(
    connection_id: uuid.UUID, drafts: list[SuggestionDraft]
) -> dict[str, int]:
    """Upsert drafts by fingerprint. Returns counts of what changed."""
    created = updated = skipped = 0

    async with session_scope() as session:
        existing_rows = (
            await session.execute(
                select(Suggestion).where(Suggestion.connection_id == connection_id)
            )
        ).scalars().all()
        existing = {row.fingerprint: row for row in existing_rows}

        for draft in drafts:
            row = existing.get(draft.fingerprint)
            if row is None:
                session.add(
                    Suggestion(
                        connection_id=connection_id,
                        kind=draft.kind,
                        source=draft.source,
                        severity=draft.severity,
                        status=SuggestionStatus.proposed,
                        title=draft.title[:300],
                        rationale=draft.rationale,
                        statement=draft.statement,
                        revert_statement=draft.revert_statement,
                        target_relation=draft.target_relation,
                        estimated_benefit=draft.estimated_benefit,
                        estimated_cost=draft.estimated_cost,
                        fingerprint=draft.fingerprint,
                        evidence=draft.evidence,
                    )
                )
                created += 1
                continue

            # A human has already decided about this one. Refresh the evidence
            # so the figures stay current, but do not resurrect it.
            if row.status in (
                SuggestionStatus.rejected,
                SuggestionStatus.applied,
                SuggestionStatus.approved,
            ):
                row.evidence = draft.evidence
                skipped += 1
                continue

            row.severity = draft.severity
            row.title = draft.title[:300]
            row.rationale = draft.rationale
            row.statement = draft.statement
            row.revert_statement = draft.revert_statement
            row.estimated_benefit = draft.estimated_benefit
            row.estimated_cost = draft.estimated_cost
            row.evidence = draft.evidence
            updated += 1

    return {"created": created, "updated": updated, "unchanged": skipped}


async def run_analysis(connection_id: uuid.UUID, pool: TargetPool) -> dict[str, Any]:
    """Gather, derive, persist. The deterministic path — no LLM involved."""
    started = datetime.now(timezone.utc)
    catalog = await gather_catalog(pool)
    drafts = derive_suggestions(catalog)
    counts = await persist_suggestions(connection_id, drafts)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info(
        "analysis for %s: %d drafts in %.2fs (%s)",
        connection_id,
        len(drafts),
        elapsed,
        counts,
    )

    return {
        "connection_id": str(connection_id),
        "ran_at": started.isoformat(),
        "duration_seconds": round(elapsed, 3),
        "suggestions": counts,
        "total_drafts": len(drafts),
        "tables_examined": len(catalog["table_stats"]),
        "indexes_examined": len(catalog["index_stats"]),
        "stats_age_hours": stats_age_hours(catalog.get("stats_reset")),
    }


async def connection_or_404(session, connection_id: uuid.UUID) -> Connection:
    row = await session.get(Connection, connection_id)
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="connection not found")
    return row
