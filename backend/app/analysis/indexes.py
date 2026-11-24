"""Index advice derived from catalog facts, not from a model.

Four checks, in descending order of confidence:

1.  **Unindexed foreign keys** — exact. The catalog states whether an index
    whose leading columns match the constraint exists. No guessing.
2.  **Duplicate and redundant indexes** — exact. Two indexes on the same
    columns with the same method, or one whose key is a leading prefix of
    another's, where the shorter earns nothing the longer does not.
3.  **Unused indexes** — statistical, and the weakest of the four. ``idx_scan``
    is cumulative since the last stats reset, so a zero only means something
    if the counters have been running long enough to cover the workload's full
    cycle. Weekly reports and quarter-end jobs are exactly the queries a short
    observation window misses, so these are reported as low severity with the
    observation window stated.
4.  **Sequential-scan-heavy tables** — a pointer, not a prescription. It says
    where to look; it does not claim to know which column wants the index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analysis.types import SuggestionDraft, human_bytes
from app.db.models import Severity, SuggestionKind
from app.safety.allowlist import (
    UnsafeIdentifier,
    render_create_index,
    render_drop_index,
)

# An index below this size is not worth the risk of dropping it.
MIN_INTERESTING_INDEX_BYTES = 1 * 1024 * 1024  # 1 MiB
# A table below this size will be seq-scanned by the planner regardless.
MIN_INTERESTING_TABLE_BYTES = 4 * 1024 * 1024  # 4 MiB
# Below this many rows read per scan, a seq scan is not evidence of anything.
MIN_SEQ_TUP_PER_SCAN = 1_000


def analyze_indexes(
    *,
    index_stats: list[dict[str, Any]],
    table_stats: list[dict[str, Any]],
    unindexed_fks: list[dict[str, Any]],
    stats_age_hours: float | None = None,
) -> list[SuggestionDraft]:
    drafts: list[SuggestionDraft] = []
    drafts += _missing_fk_indexes(unindexed_fks)
    drafts += _duplicate_indexes(index_stats)
    drafts += _unused_indexes(index_stats, stats_age_hours)
    drafts += _seq_scan_heavy(table_stats, index_stats)
    drafts.sort(key=lambda d: d.sort_key())
    return drafts


# ---------------------------------------------------------------------------
# 1. Unindexed foreign keys
# ---------------------------------------------------------------------------


def _missing_fk_indexes(rows: list[dict[str, Any]]) -> list[SuggestionDraft]:
    drafts: list[SuggestionDraft] = []
    for row in rows:
        schema = row.get("schemaname") or "public"
        table = row["table_name"]
        columns = list(row.get("columns") or [])
        if not columns:
            continue
        table_bytes = int(row.get("table_bytes") or 0)

        # A foreign key with no index makes every parent-side delete or update
        # scan the child table, and makes the obvious join a sequential scan.
        severity = Severity.high if table_bytes >= MIN_INTERESTING_TABLE_BYTES else Severity.low

        try:
            statement = render_create_index(
                schema=schema,
                table=table,
                columns=columns,
                index_name=f"idx_{table}_{'_'.join(columns)}",
            )
        except UnsafeIdentifier:
            continue

        index_name = statement.split('"')[1] if '"' in statement else None
        revert = (
            render_drop_index(schema=schema, index_name=index_name) if index_name else None
        )

        drafts.append(
            SuggestionDraft(
                kind=SuggestionKind.create_index,
                severity=severity,
                title=f"Index the foreign key {table}({', '.join(columns)})",
                rationale=(
                    f"The constraint {row.get('constraint_name')} references "
                    f"{row.get('references_table')}, but no index on {table} leads with "
                    f"{', '.join(columns)}. Without one, every join across this key and "
                    f"every delete or key update on the parent must scan "
                    f"{table} ({human_bytes(table_bytes)}) in full. This is read directly "
                    f"from the catalog, not inferred from the workload."
                ),
                statement=statement,
                revert_statement=revert,
                target_relation=f"{schema}.{table}",
                estimated_benefit=(
                    "Turns sequential scans of this table into index lookups on the join "
                    "and removes the scan from cascading parent deletes."
                ),
                estimated_cost=(
                    f"One index build (CONCURRENTLY, no write lock) plus roughly "
                    f"{human_bytes(table_bytes * 0.05)} of storage and a small write overhead."
                ),
                evidence={
                    "constraint": row.get("constraint_name"),
                    "columns": columns,
                    "references": row.get("references_table"),
                    "table_bytes": table_bytes,
                    "check": "catalog:exact",
                },
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# 2. Duplicate and prefix-redundant indexes
# ---------------------------------------------------------------------------


def _index_signature(row: dict[str, Any]) -> tuple[str, str, tuple[str, ...], bool]:
    cols = tuple(row.get("key_columns") or ())
    return (row.get("table_name") or "", row.get("method") or "btree", cols, bool(row.get("is_partial")))


def _duplicate_indexes(index_stats: list[dict[str, Any]]) -> list[SuggestionDraft]:
    drafts: list[SuggestionDraft] = []
    by_table: dict[str, list[dict[str, Any]]] = {}
    for row in index_stats:
        if not row.get("indisvalid", True):
            continue
        by_table.setdefault(row.get("table_name") or "", []).append(row)

    for table, rows in by_table.items():
        schema = rows[0].get("schemaname") or "public"

        # --- exact duplicates --------------------------------------------
        seen: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            seen.setdefault(_index_signature(row), []).append(row)

        for sig, group in seen.items():
            if len(group) < 2:
                continue
            # Keep a constraint-backing index; drop a plain one.
            group.sort(key=lambda r: (not r.get("indisprimary"), not r.get("indisunique")))
            keeper, *redundant = group
            for row in redundant:
                drafts.append(
                    _drop_draft(
                        schema=schema,
                        table=table,
                        row=row,
                        severity=Severity.medium,
                        title=f"Drop duplicate index {row['index_name']}",
                        rationale=(
                            f"{row['index_name']} and {keeper['index_name']} both index "
                            f"{table}({', '.join(sig[2])}) using {sig[1]}. They are "
                            f"interchangeable, so the second one only costs: every insert "
                            f"and every update of these columns maintains both, and it "
                            f"occupies {human_bytes(row.get('index_bytes'))} that could be "
                            f"cache. {keeper['index_name']} is kept because it backs a "
                            f"constraint or is otherwise preferred."
                            if keeper.get("indisunique") or keeper.get("indisprimary")
                            else
                            f"{row['index_name']} and {keeper['index_name']} both index "
                            f"{table}({', '.join(sig[2])}) using {sig[1]}. One of them is "
                            f"pure overhead on every write and "
                            f"{human_bytes(row.get('index_bytes'))} of wasted space."
                        ),
                        evidence={
                            "duplicate_of": keeper["index_name"],
                            "columns": list(sig[2]),
                            "method": sig[1],
                            "check": "catalog:exact",
                        },
                    )
                )

        # --- prefix redundancy -------------------------------------------
        btrees = [
            r
            for r in rows
            if (r.get("method") or "btree") == "btree"
            and not r.get("is_partial")
            and not r.get("indisprimary")
            and not r.get("indisunique")
            and r.get("key_columns")
        ]
        for short in btrees:
            short_cols = tuple(short["key_columns"])
            for long in btrees:
                if short is long:
                    continue
                long_cols = tuple(long["key_columns"])
                if len(long_cols) <= len(short_cols):
                    continue
                if long_cols[: len(short_cols)] != short_cols:
                    continue
                # A shorter index is still worth keeping if it is doing real
                # work that the longer one is not — but a left-prefix index can
                # always be answered by the longer one, so scan counts only
                # change how confidently we say so.
                scans = int(short.get("idx_scan") or 0)
                severity = Severity.low if scans > 0 else Severity.medium
                drafts.append(
                    _drop_draft(
                        schema=schema,
                        table=table,
                        row=short,
                        severity=severity,
                        title=f"Drop redundant prefix index {short['index_name']}",
                        rationale=(
                            f"{short['index_name']} covers ({', '.join(short_cols)}), which is "
                            f"a leading prefix of {long['index_name']} on "
                            f"({', '.join(long_cols)}). Any lookup the shorter index can "
                            f"serve, the longer one can serve too. It costs "
                            f"{human_bytes(short.get('index_bytes'))} and is maintained on "
                            f"every write."
                            + (
                                f" It has been used {scans:,} times, so confirm the planner "
                                f"picks the wider index for those queries before dropping it: "
                                f"a wider index means a larger scan."
                                if scans > 0
                                else " It has never been used."
                            )
                        ),
                        evidence={
                            "covered_by": long["index_name"],
                            "prefix_of": list(long_cols),
                            "idx_scan": scans,
                            "check": "catalog:exact",
                        },
                    )
                )
    return drafts


# ---------------------------------------------------------------------------
# 3. Unused indexes
# ---------------------------------------------------------------------------


def _unused_indexes(
    index_stats: list[dict[str, Any]], stats_age_hours: float | None
) -> list[SuggestionDraft]:
    drafts: list[SuggestionDraft] = []
    window = (
        f"{stats_age_hours:.0f} hours" if stats_age_hours else "an unknown period"
    )
    # With a short observation window this check is close to worthless, so say
    # so in the severity rather than pretending otherwise.
    confident = bool(stats_age_hours and stats_age_hours >= 24 * 7)

    for row in index_stats:
        if row.get("indisprimary") or row.get("indisunique"):
            continue  # these enforce constraints; scan count is irrelevant
        if int(row.get("idx_scan") or 0) > 0:
            continue
        size = int(row.get("index_bytes") or 0)
        if size < MIN_INTERESTING_INDEX_BYTES:
            continue

        schema = row.get("schemaname") or "public"
        table = row.get("table_name") or ""
        drafts.append(
            _drop_draft(
                schema=schema,
                table=table,
                row=row,
                severity=Severity.medium if confident else Severity.low,
                title=f"Index {row['index_name']} has never been scanned",
                rationale=(
                    f"{row['index_name']} on {table} occupies "
                    f"{human_bytes(size)} and its scan count has been zero for {window}. "
                    f"It is maintained on every insert and on every update touching its "
                    f"columns.\n\n"
                    + (
                        "The observation window covers more than a week, so periodic "
                        "jobs have most likely run at least once."
                        if confident
                        else "The observation window is short. A weekly report, a "
                        "month-end job, or a rarely-hit admin page could still need "
                        "this index. Confirm against a longer window, or reset the "
                        "statistics and re-check after a full business cycle, before "
                        "dropping it."
                    )
                ),
                evidence={
                    "idx_scan": 0,
                    "index_bytes": size,
                    "stats_age_hours": stats_age_hours,
                    "check": "statistical:observation-window-dependent",
                },
            )
        )
    return drafts


def _drop_draft(
    *,
    schema: str,
    table: str,
    row: dict[str, Any],
    severity: Severity,
    title: str,
    rationale: str,
    evidence: dict[str, Any],
) -> SuggestionDraft:
    index_name = row["index_name"]
    try:
        statement = render_drop_index(schema=schema, index_name=index_name)
    except UnsafeIdentifier:
        statement = None
    # The definition is captured now so the suggestion carries its own undo.
    indexdef = row.get("indexdef")
    revert = None
    if indexdef:
        revert = str(indexdef).replace("CREATE INDEX ", "CREATE INDEX CONCURRENTLY ", 1)

    return SuggestionDraft(
        kind=SuggestionKind.drop_index,
        severity=severity,
        title=title,
        rationale=rationale,
        statement=statement,
        revert_statement=revert,
        target_relation=f"{schema}.{table}",
        estimated_benefit=(
            f"Reclaims {human_bytes(row.get('index_bytes'))} and removes write overhead "
            f"on every insert and relevant update."
        ),
        estimated_cost=(
            "If the index turns out to be needed, rebuilding it on a large table is "
            "slow. The revert statement is stored with this suggestion."
        ),
        evidence={**evidence, "index_name": index_name, "indexdef": indexdef},
    )


# ---------------------------------------------------------------------------
# 4. Sequential-scan-heavy tables
# ---------------------------------------------------------------------------


def _seq_scan_heavy(
    table_stats: list[dict[str, Any]], index_stats: list[dict[str, Any]]
) -> list[SuggestionDraft]:
    """Flag tables the planner keeps scanning end to end.

    Deliberately produces no statement. Knowing a table is scanned a lot does
    not tell you which column to index — that needs the query text, which is
    what the EXPLAIN flow and the LLM advisor are for. Emitting a confident
    CREATE INDEX here would be guessing.
    """
    indexes_by_table: dict[str, int] = {}
    for row in index_stats:
        indexes_by_table[row.get("table_name") or ""] = (
            indexes_by_table.get(row.get("table_name") or "", 0) + 1
        )

    drafts: list[SuggestionDraft] = []
    for row in table_stats:
        seq_scan = int(row.get("seq_scan") or 0)
        seq_tup = int(row.get("seq_tup_read") or 0)
        idx_scan = int(row.get("idx_scan") or 0)
        total_bytes = int(row.get("total_bytes") or 0)

        if seq_scan < 50 or total_bytes < MIN_INTERESTING_TABLE_BYTES:
            continue
        tup_per_scan = seq_tup / seq_scan if seq_scan else 0
        if tup_per_scan < MIN_SEQ_TUP_PER_SCAN:
            continue

        scan_share = seq_scan / (seq_scan + idx_scan) if (seq_scan + idx_scan) else 1.0
        if scan_share < 0.3:
            continue

        severity = Severity.high if scan_share > 0.8 and tup_per_scan > 50_000 else Severity.medium
        table = row.get("table_name") or ""
        schema = row.get("schemaname") or "public"

        drafts.append(
            SuggestionDraft(
                kind=SuggestionKind.query_rewrite,
                severity=severity,
                title=f"{table} is read by sequential scan {scan_share:.0%} of the time",
                rationale=(
                    f"{seq_scan:,} sequential scans have read {seq_tup:,} rows in total — "
                    f"about {tup_per_scan:,.0f} rows per scan — against {idx_scan:,} index "
                    f"scans. The table is {human_bytes(total_bytes)} with "
                    f"{indexes_by_table.get(table, 0)} index(es).\n\n"
                    f"This says where to look, not what to do. Which index would help "
                    f"depends on the predicates in the statements doing the scanning: open "
                    f"the slow-query list filtered to {table}, run EXPLAIN on the worst "
                    f"offender, and index the column it filters on."
                ),
                statement=None,  # intentionally advisory
                target_relation=f"{schema}.{table}",
                estimated_benefit="Depends entirely on the predicate; investigate before acting.",
                evidence={
                    "seq_scan": seq_scan,
                    "seq_tup_read": seq_tup,
                    "idx_scan": idx_scan,
                    "rows_per_seq_scan": round(tup_per_scan),
                    "total_bytes": total_bytes,
                    "check": "statistical:directional",
                },
            )
        )
    return drafts


def stats_age_hours(stats_reset: Any) -> float | None:
    """Hours since the statistics were last reset, if the server reported it."""
    if not stats_reset:
        return None
    if isinstance(stats_reset, str):
        try:
            stats_reset = datetime.fromisoformat(stats_reset)
        except ValueError:
            return None
    if not isinstance(stats_reset, datetime):
        return None
    if stats_reset.tzinfo is None:
        stats_reset = stats_reset.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - stats_reset
    return max(delta.total_seconds() / 3600.0, 0.0)
