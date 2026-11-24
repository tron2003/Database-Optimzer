"""Execution plan analysis.

Walks an ``EXPLAIN (FORMAT JSON)`` tree and reports what is wrong with it,
deterministically. This runs before any model is consulted, and its findings
are passed to the model as evidence rather than asking the model to read the
raw plan and be trusted about it.

The checks are the ones an experienced reader actually performs:

*   **Row misestimates.** The single most useful signal in a plan. When the
    planner expects 10 rows and gets 100,000, every choice above that node was
    made on bad information, so fixing the estimate often fixes the plan
    without touching indexes at all.
*   **Sequential scans with a selective filter.** A seq scan is not a problem
    in itself; a seq scan that reads a million rows and keeps four is.
*   **Nested loops with a large outer side.** Fine for a handful of rows,
    quadratic when the estimate was wrong.
*   **Disk spills.** Sorts and hashes falling back to disk mean ``work_mem``
    is too small for this query.
*   **Lossy bitmap scans and high heap fetches.** Symptoms of a plan that is
    nominally using an index but paying for the heap anyway.
"""

from __future__ import annotations

from typing import Any

from app.analysis.types import PlanFinding, human_bytes, human_ms
from app.db.models import Severity

# A node must be doing real work before its misestimate matters.
MIN_ROWS_FOR_MISESTIMATE = 100
MISESTIMATE_RATIO_HIGH = 100.0
MISESTIMATE_RATIO_MEDIUM = 10.0

# A seq scan that discards this proportion of what it reads wants an index.
SELECTIVE_FILTER_THRESHOLD = 0.9
MIN_ROWS_FOR_SEQ_SCAN_CONCERN = 10_000

NESTED_LOOP_LOOP_THRESHOLD = 1_000


def analyze_plan(plan_json: Any) -> tuple[list[PlanFinding], dict[str, Any]]:
    """Return findings plus a summary of the plan as a whole.

    ``plan_json`` is whatever ``EXPLAIN (FORMAT JSON)`` returned: either the
    single-element list Postgres emits, or the object inside it.
    """
    root = _unwrap(plan_json)
    if root is None:
        return [], {}

    plan = root.get("Plan") or root
    findings: list[PlanFinding] = []
    summary = {
        "planning_ms": root.get("Planning Time"),
        "execution_ms": root.get("Execution Time"),
        "total_cost": plan.get("Total Cost"),
        "analyzed": "Actual Rows" in plan or "Actual Total Time" in plan,
        "triggers": root.get("Triggers") or [],
        "node_count": 0,
        "max_node_ms": 0.0,
        "slowest_node": None,
    }

    _walk(plan, findings, summary, depth=0, parent=None)

    # Rank most severe first, then by how much time the node actually took.
    order = {
        Severity.critical: 0,
        Severity.high: 1,
        Severity.medium: 2,
        Severity.low: 3,
        Severity.info: 4,
    }
    findings.sort(key=lambda f: (order.get(f.severity, 9), -float(f.detail.get("node_ms") or 0)))
    return findings, summary


def _unwrap(plan_json: Any) -> dict[str, Any] | None:
    if isinstance(plan_json, list):
        return plan_json[0] if plan_json else None
    if isinstance(plan_json, dict):
        return plan_json
    return None


def _node_label(node: dict[str, Any]) -> str:
    label = node.get("Node Type", "Node")
    relation = node.get("Relation Name")
    index = node.get("Index Name")
    if index:
        return f"{label} using {index}"
    if relation:
        return f"{label} on {relation}"
    return str(label)


def _self_time_ms(node: dict[str, Any]) -> float:
    """Time spent in this node alone, excluding children.

    ``Actual Total Time`` is per-loop and inclusive of children, which is the
    detail that makes people misread plans. Multiply by loops, then subtract
    the children.
    """
    total = float(node.get("Actual Total Time") or 0.0) * float(node.get("Actual Loops") or 1)
    child_total = 0.0
    for child in node.get("Plans") or []:
        child_total += float(child.get("Actual Total Time") or 0.0) * float(
            child.get("Actual Loops") or 1
        )
    return max(total - child_total, 0.0)


def _walk(
    node: dict[str, Any],
    findings: list[PlanFinding],
    summary: dict[str, Any],
    depth: int,
    parent: dict[str, Any] | None,
) -> None:
    summary["node_count"] += 1
    label = _node_label(node)
    node_ms = _self_time_ms(node) if summary["analyzed"] else 0.0
    if node_ms > summary["max_node_ms"]:
        summary["max_node_ms"] = node_ms
        summary["slowest_node"] = label

    _check_misestimate(node, label, node_ms, findings)
    _check_seq_scan(node, label, node_ms, findings)
    _check_nested_loop(node, label, node_ms, findings)
    _check_spills(node, label, node_ms, findings)
    _check_bitmap(node, label, node_ms, findings)
    _check_index_efficiency(node, label, node_ms, findings)
    _check_parallel(node, label, node_ms, findings)

    for child in node.get("Plans") or []:
        _walk(child, findings, summary, depth + 1, node)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_misestimate(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    if "Actual Rows" not in node:
        return
    loops = float(node.get("Actual Loops") or 1)
    actual = float(node.get("Actual Rows") or 0) * loops
    planned = float(node.get("Plan Rows") or 0) * loops
    if max(actual, planned) < MIN_ROWS_FOR_MISESTIMATE:
        return

    # Guard the divide; a planner estimate of zero is reported as one row.
    planned = max(planned, 1.0)
    actual_safe = max(actual, 1.0)
    ratio = max(actual_safe / planned, planned / actual_safe)
    if ratio < MISESTIMATE_RATIO_MEDIUM:
        return

    under = actual > planned
    severity = Severity.high if ratio >= MISESTIMATE_RATIO_HIGH else Severity.medium
    direction = "under" if under else "over"

    findings.append(
        PlanFinding(
            code="row_misestimate",
            severity=severity,
            node=label,
            message=(
                f"The planner {direction}estimated this node by {ratio:,.0f}x: it expected "
                f"{planned:,.0f} rows and got {actual:,.0f}. Every join method and join "
                f"order chosen above this point was decided on that wrong number, so the "
                f"plan may be wrong in ways that have nothing to do with this node."
            ),
            detail={
                "planned_rows": planned,
                "actual_rows": actual,
                "ratio": round(ratio, 1),
                "direction": direction,
                "node_ms": node_ms,
                "remedy": (
                    "Run ANALYZE on the underlying table. If the estimate is still wrong, "
                    "the column is probably skewed or correlated with another: raise its "
                    "statistics target, or create extended statistics "
                    "(CREATE STATISTICS) across the correlated columns."
                ),
            },
        )
    )


def _check_seq_scan(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    if node.get("Node Type") != "Seq Scan":
        return
    removed = float(node.get("Rows Removed by Filter") or 0)
    actual = float(node.get("Actual Rows") or 0)
    loops = float(node.get("Actual Loops") or 1)
    read = (removed + actual) * loops
    if read < MIN_ROWS_FOR_SEQ_SCAN_CONCERN:
        return
    selectivity = removed / (removed + actual) if (removed + actual) else 0.0
    if selectivity < SELECTIVE_FILTER_THRESHOLD:
        # Reading most of the table and keeping most of it is a legitimate
        # sequential scan; an index would be slower.
        return

    findings.append(
        PlanFinding(
            code="selective_seq_scan",
            severity=Severity.high,
            node=label,
            message=(
                f"Sequential scan read {read:,.0f} rows and discarded "
                f"{removed * loops:,.0f} of them ({selectivity:.1%}) to the filter. "
                f"Reading a whole table to keep {1 - selectivity:.1%} of it is what an "
                f"index exists to avoid."
            ),
            detail={
                "relation": node.get("Relation Name"),
                "filter": node.get("Filter"),
                "rows_read": read,
                "rows_removed": removed * loops,
                "selectivity": round(selectivity, 4),
                "node_ms": node_ms,
                "remedy": (
                    "Index the column(s) in the filter shown above. If the filter wraps a "
                    "column in a function or a cast, the index must match that expression "
                    "or it will not be used."
                ),
            },
        )
    )


def _check_nested_loop(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    if node.get("Node Type") != "Nested Loop":
        return
    children = node.get("Plans") or []
    if len(children) < 2:
        return
    inner = children[1]
    loops = float(inner.get("Actual Loops") or 0)
    if loops < NESTED_LOOP_LOOP_THRESHOLD:
        return

    inner_total = float(inner.get("Actual Total Time") or 0.0) * loops
    findings.append(
        PlanFinding(
            code="hot_nested_loop",
            severity=Severity.high if loops > 50_000 else Severity.medium,
            node=label,
            message=(
                f"The inner side of this nested loop ran {loops:,.0f} times, costing "
                f"{human_ms(inner_total)} in total. A nested loop is the right choice for "
                f"a handful of outer rows; at this count a hash or merge join is usually "
                f"far cheaper. This is often a consequence of a row misestimate further "
                f"down rather than a problem with the join itself."
            ),
            detail={
                "loops": loops,
                "inner_node": _node_label(inner),
                "inner_total_ms": inner_total,
                "node_ms": node_ms,
                "remedy": (
                    "Fix the row estimate on the outer side first. If the estimate is "
                    "accurate and the loop is genuinely hot, index the inner side's join "
                    "key so each iteration is a lookup rather than a scan."
                ),
            },
        )
    )


def _check_spills(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    sort_method = node.get("Sort Method") or ""
    if "external" in sort_method.lower():
        space_kb = float(node.get("Sort Space Used") or 0)
        findings.append(
            PlanFinding(
                code="sort_spilled_to_disk",
                severity=Severity.medium,
                node=label,
                message=(
                    f"The sort spilled to disk ({sort_method}), using "
                    f"{human_bytes(space_kb * 1024)}. work_mem is smaller than this sort "
                    f"needs, so it is paying disk I/O for what should be a memory operation."
                ),
                detail={
                    "sort_method": sort_method,
                    "sort_space_bytes": space_kb * 1024,
                    "node_ms": node_ms,
                    "remedy": (
                        "Raise work_mem for this query's session, or reduce the number of "
                        "rows reaching the sort. work_mem is allocated per sort node per "
                        "parallel worker, so raising it globally multiplies across the "
                        "whole server."
                    ),
                },
            )
        )

    batches = int(node.get("Hash Batches") or 0)
    if batches > 1:
        findings.append(
            PlanFinding(
                code="hash_spilled_to_disk",
                severity=Severity.medium,
                node=label,
                message=(
                    f"The hash table was split into {batches} batches, which means it did "
                    f"not fit in work_mem and was written to and re-read from disk."
                ),
                detail={
                    "hash_batches": batches,
                    "peak_memory_kb": node.get("Peak Memory Usage"),
                    "node_ms": node_ms,
                    "remedy": "Raise work_mem, or filter the hashed side down before the join.",
                },
            )
        )


def _check_bitmap(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    lossy = float(node.get("Lossy Heap Blocks") or 0)
    exact = float(node.get("Exact Heap Blocks") or 0)
    if lossy > 0 and lossy > exact * 0.1:
        findings.append(
            PlanFinding(
                code="lossy_bitmap_scan",
                severity=Severity.medium,
                node=label,
                message=(
                    f"The bitmap became lossy: {lossy:,.0f} blocks had to be rechecked "
                    f"page-by-page against {exact:,.0f} exact blocks. The bitmap outgrew "
                    f"work_mem and degraded to tracking whole pages instead of rows."
                ),
                detail={
                    "lossy_heap_blocks": lossy,
                    "exact_heap_blocks": exact,
                    "node_ms": node_ms,
                    "remedy": "Raise work_mem, or make the index condition more selective.",
                },
            )
        )


def _check_index_efficiency(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    if node.get("Node Type") not in ("Index Scan", "Index Only Scan", "Bitmap Heap Scan"):
        return
    removed = float(node.get("Rows Removed by Index Recheck") or 0) + float(
        node.get("Rows Removed by Filter") or 0
    )
    actual = float(node.get("Actual Rows") or 0)
    if removed > 1_000 and removed > actual:
        findings.append(
            PlanFinding(
                code="index_scan_overfetch",
                severity=Severity.medium,
                node=label,
                message=(
                    f"This index scan fetched {removed + actual:,.0f} rows and threw away "
                    f"{removed:,.0f} of them after the fact. The index narrows the search "
                    f"but not enough; the remaining predicate is being applied as a filter."
                ),
                detail={
                    "rows_removed": removed,
                    "rows_returned": actual,
                    "filter": node.get("Filter"),
                    "index_cond": node.get("Index Cond"),
                    "node_ms": node_ms,
                    "remedy": (
                        "Extend the index to include the filtered column, so the condition "
                        "is evaluated in the index rather than against fetched rows."
                    ),
                },
            )
        )

    fetches = float(node.get("Heap Fetches") or 0)
    if node.get("Node Type") == "Index Only Scan" and fetches > max(actual * 0.1, 1_000):
        findings.append(
            PlanFinding(
                code="index_only_scan_heap_fetches",
                severity=Severity.low,
                node=label,
                message=(
                    f"This index-only scan still visited the heap {fetches:,.0f} times. "
                    f"The visibility map is stale, so the scan cannot confirm row "
                    f"visibility from the index alone."
                ),
                detail={
                    "heap_fetches": fetches,
                    "node_ms": node_ms,
                    "remedy": (
                        "VACUUM the table to refresh the visibility map. If it goes stale "
                        "again quickly, autovacuum is not keeping up with the write rate "
                        "on this table."
                    ),
                },
            )
        )


def _check_parallel(
    node: dict[str, Any], label: str, node_ms: float, findings: list[PlanFinding]
) -> None:
    planned = int(node.get("Workers Planned") or 0)
    launched = int(node.get("Workers Launched") or 0)
    if planned > 0 and launched < planned:
        findings.append(
            PlanFinding(
                code="parallel_workers_not_launched",
                severity=Severity.low,
                node=label,
                message=(
                    f"The plan asked for {planned} parallel workers but only {launched} "
                    f"started. The rest of the work fell back to the leader, so the query "
                    f"ran slower than the plan's cost estimate assumed."
                ),
                detail={
                    "workers_planned": planned,
                    "workers_launched": launched,
                    "node_ms": node_ms,
                    "remedy": (
                        "The server was at its worker limit. Raise "
                        "max_parallel_workers / max_worker_processes, or reduce competing "
                        "parallel queries."
                    ),
                },
            )
        )


def summarize_findings(findings: list[PlanFinding]) -> str:
    """One-line plain-text summary, used where a full render is too much."""
    if not findings:
        return "No structural problems found in this plan."
    worst = findings[0]
    extra = len(findings) - 1
    tail = f" (+{extra} more finding{'s' if extra != 1 else ''})" if extra else ""
    return f"{worst.severity.value.upper()}: {worst.message.split('.')[0]}.{tail}"
