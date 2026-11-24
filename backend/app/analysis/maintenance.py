"""Bloat, vacuum and statistics freshness.

Dead tuples are the easiest real problem to find and the easiest to
overreact to. Two things keep this honest:

*   ``n_dead_tup`` is itself an estimate maintained by the statistics
    collector. It is directionally right and numerically approximate, so the
    thresholds here are generous and the exact figure is only claimed when
    ``pgstattuple`` has actually measured the relation.
*   Dead tuples are not waste in themselves. A table under constant update
    always carries some, and autovacuum is supposed to reclaim them. What
    matters is dead tuples that autovacuum is *not* keeping up with, which is
    why the check looks at the ratio together with when autovacuum last ran.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.analysis.types import SuggestionDraft, human_bytes
from app.db.models import Severity, SuggestionKind
from app.safety.allowlist import UnsafeIdentifier, render_analyze, render_vacuum

DEAD_RATIO_WARN = 0.20
DEAD_RATIO_HIGH = 0.40
MIN_DEAD_TUPLES = 10_000
MIN_TABLE_BYTES = 8 * 1024 * 1024
STALE_ANALYZE_HOURS = 24
MIN_MODS_FOR_STALE_ANALYZE = 10_000


def analyze_maintenance(table_stats: list[dict[str, Any]]) -> list[SuggestionDraft]:
    drafts: list[SuggestionDraft] = []
    now = datetime.now(timezone.utc)

    for row in table_stats:
        drafts.extend(_bloat_check(row, now))
        drafts.extend(_stale_statistics_check(row, now))

    drafts.sort(key=lambda d: d.sort_key())
    return drafts


def _bloat_check(row: dict[str, Any], now: datetime) -> list[SuggestionDraft]:
    live = int(row.get("n_live_tup") or 0)
    dead = int(row.get("n_dead_tup") or 0)
    total_bytes = int(row.get("total_bytes") or 0)

    if dead < MIN_DEAD_TUPLES or total_bytes < MIN_TABLE_BYTES:
        return []
    denominator = live + dead
    if denominator == 0:
        return []
    ratio = dead / denominator
    if ratio < DEAD_RATIO_WARN:
        return []

    schema = row.get("schemaname") or "public"
    table = row.get("table_name") or ""
    last_av = _as_datetime(row.get("last_autovacuum")) or _as_datetime(row.get("last_vacuum"))
    hours_since = (now - last_av).total_seconds() / 3600.0 if last_av else None

    # Autovacuum running recently and still losing ground is the interesting
    # case: it means the table's settings are wrong, not that a manual vacuum
    # is needed.
    autovacuum_behind = hours_since is not None and hours_since < 1 and ratio > DEAD_RATIO_HIGH

    severity = Severity.high if ratio >= DEAD_RATIO_HIGH else Severity.medium
    wasted = int(total_bytes * ratio)

    rationale = (
        f"{table} holds roughly {dead:,} dead tuples against {live:,} live ones "
        f"({ratio:.0%}). On a {human_bytes(total_bytes)} table that is about "
        f"{human_bytes(wasted)} of space that cannot be reused by anything but this "
        f"table, and every sequential scan reads past it.\n\n"
    )
    if last_av is None:
        rationale += (
            "Autovacuum has never run on this table, which usually means it was "
            "created recently, or autovacuum is disabled for it."
        )
    elif autovacuum_behind:
        rationale += (
            f"Autovacuum ran within the last hour and the ratio is still {ratio:.0%}, so "
            f"it is not keeping up with the write rate. A one-off VACUUM will help now, "
            f"but the durable fix is to lower autovacuum_vacuum_scale_factor for this "
            f"table so it triggers more often."
        )
    else:
        rationale += (
            f"The last vacuum was {hours_since:.0f} hours ago."
            if hours_since is not None
            else ""
        )

    try:
        statement = render_vacuum(schema=schema, table=table, analyze=True)
    except UnsafeIdentifier:
        return []

    return [
        SuggestionDraft(
            kind=SuggestionKind.vacuum_table,
            severity=severity,
            title=f"VACUUM {table} — about {ratio:.0%} dead tuples",
            rationale=rationale,
            statement=statement,
            revert_statement=None,  # nothing to revert; vacuum only reclaims
            target_relation=f"{schema}.{table}",
            estimated_benefit=(
                f"Marks up to {human_bytes(wasted)} reusable and shortens every "
                f"sequential scan of this table."
            ),
            estimated_cost=(
                "A plain VACUUM takes no exclusive lock and runs alongside normal "
                "traffic. It generates I/O, so prefer a quiet period on a busy system. "
                "It does not return space to the filesystem — only VACUUM FULL does "
                "that, and this tool will not run one."
            ),
            evidence={
                "n_live_tup": live,
                "n_dead_tup": dead,
                "dead_ratio": round(ratio, 4),
                "total_bytes": total_bytes,
                "last_autovacuum": str(row.get("last_autovacuum") or ""),
                "autovacuum_behind": autovacuum_behind,
                "check": "statistical:estimated-by-collector",
            },
        )
    ]


def _stale_statistics_check(row: dict[str, Any], now: datetime) -> list[SuggestionDraft]:
    mods = int(row.get("n_mod_since_analyze") or 0)
    live = int(row.get("n_live_tup") or 0)
    if mods < MIN_MODS_FOR_STALE_ANALYZE:
        return []

    last = _as_datetime(row.get("last_autoanalyze")) or _as_datetime(row.get("last_analyze"))
    hours = (now - last).total_seconds() / 3600.0 if last else None
    if hours is not None and hours < STALE_ANALYZE_HOURS and mods < max(live * 0.2, 50_000):
        return []

    schema = row.get("schemaname") or "public"
    table = row.get("table_name") or ""
    churn = (mods / live) if live else 1.0

    try:
        statement = render_analyze(schema=schema, table=table)
    except UnsafeIdentifier:
        return []

    return [
        SuggestionDraft(
            kind=SuggestionKind.analyze_table,
            severity=Severity.medium if churn > 0.5 else Severity.low,
            title=f"ANALYZE {table} — statistics are behind the data",
            rationale=(
                f"{mods:,} rows have been inserted, updated or deleted in {table} since "
                f"it was last analyzed"
                + (f", {hours:.0f} hours ago" if hours is not None else " (never)")
                + f". That is {churn:.0%} of the table's {live:,} live rows. The planner "
                f"is choosing join orders and access methods from a distribution that no "
                f"longer matches what is there, which is a common cause of a query that "
                f"was fast last week and is slow today without anything having changed."
            ),
            statement=statement,
            target_relation=f"{schema}.{table}",
            estimated_benefit=(
                "Restores accurate row estimates, which is frequently enough on its own "
                "to return a regressed plan to its previous shape."
            ),
            estimated_cost=(
                "ANALYZE samples the table rather than reading all of it. It is fast, "
                "takes no disruptive lock, and is safe to run during normal traffic."
            ),
            evidence={
                "n_mod_since_analyze": mods,
                "n_live_tup": live,
                "churn_ratio": round(churn, 4),
                "last_autoanalyze": str(row.get("last_autoanalyze") or ""),
                "check": "statistical:collector-counters",
            },
        )
    ]


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def bloat_from_pgstattuple(row: dict[str, Any]) -> dict[str, Any]:
    """Shape an exact pgstattuple measurement for the API.

    Unlike the collector's estimate, these numbers are measured, so the UI is
    entitled to present them without hedging.
    """
    table_bytes = int(row.get("table_bytes") or 0)
    dead_bytes = int(row.get("dead_bytes") or 0)
    free_bytes = int(row.get("free_bytes") or 0)
    return {
        "schema": row.get("schemaname"),
        "table": row.get("table_name"),
        "table_bytes": table_bytes,
        "live_tuples": int(row.get("live_tuples") or 0),
        "live_bytes": int(row.get("live_bytes") or 0),
        "dead_tuples": int(row.get("dead_tuples") or 0),
        "dead_bytes": dead_bytes,
        "free_bytes": free_bytes,
        "free_percent": float(row.get("free_percent") or 0.0),
        "reclaimable_bytes": dead_bytes + free_bytes,
        "measured": True,
    }


def _stale_window() -> timedelta:  # kept for callers that want the constant
    return timedelta(hours=STALE_ANALYZE_HOURS)
