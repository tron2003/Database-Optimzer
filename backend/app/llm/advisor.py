"""Turning model proposals into validated suggestions.

This is the join between the LLM layer and the safety layer, and the only
place where something a model said becomes something that could run. Every
proposal passes three gates before it is persisted:

1.  **Schema check.** The table and every column must exist in the catalog we
    just read. A model that hallucinates ``orders.customer_uuid`` produces a
    rejected proposal, not a broken statement.
2.  **Template rendering.** The statement is built by
    ``safety.allowlist.render_create_index`` from the validated fields. The
    model's text is never concatenated into SQL.
3.  **Re-parse.** The rendered statement goes back through
    ``safety.allowlist.validate``. Rendering and validating are deliberately
    independent, so a bug in the renderer is caught by the parser rather than
    trusted.

A proposal that fails any gate is dropped and the reason recorded.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.analysis.types import PlanFinding, SuggestionDraft
from app.db.models import Severity, SuggestionKind, SuggestionSource
from app.llm.base import LLMError, Message
from app.llm.prompts import (
    PLAN_INTERPRETER,
    QUERY_REVIEWER,
    plan_user_message,
    query_user_message,
)
from app.llm.registry import get_provider, provider_is_remote
from app.llm.schemas import Advice, IndexProposal, PlanInterpretation, QueryReview
from app.safety.allowlist import (
    UnsafeIdentifier,
    render_create_index,
    render_drop_index,
    validate,
)
from app.safety.redact import redact_plan, redact_sql

log = logging.getLogger(__name__)

_CONFIDENCE_SEVERITY = {
    "high": Severity.high,
    "medium": Severity.medium,
    "low": Severity.low,
}


# ---------------------------------------------------------------------------
# Schema context
# ---------------------------------------------------------------------------


def build_schema_context(tables: list[dict[str, Any]], limit: int = 25) -> str:
    """Render catalog rows as compact DDL-ish text for the prompt."""
    lines: list[str] = []
    for row in tables[:limit]:
        name = f"{row.get('schemaname', 'public')}.{row.get('table_name')}"
        rows_est = int(row.get("estimated_rows") or 0)
        lines.append(f"TABLE {name}  (~{rows_est:,} rows)")
        for col in row.get("columns") or []:
            null = "" if col.get("not_null") else " NULL"
            lines.append(f"    {col.get('name')} {col.get('type')}{null}")
        for idx in row.get("indexes") or []:
            lines.append(f"    INDEX {idx}")
        lines.append("")
    return "\n".join(lines)


def build_column_map(tables: list[dict[str, Any]]) -> dict[str, set[str]]:
    """``{"public.orders": {"id", "customer_id", ...}}`` for validation."""
    out: dict[str, set[str]] = {}
    for row in tables:
        key = f"{row.get('schemaname', 'public')}.{row.get('table_name')}"
        cols = {c.get("name") for c in (row.get("columns") or []) if c.get("name")}
        out[key] = cols
        # Also key on the bare name so a model that omitted the schema still
        # resolves against the common case.
        out.setdefault(str(row.get("table_name")), cols)
    return out


# ---------------------------------------------------------------------------
# Gate: proposal -> draft
# ---------------------------------------------------------------------------


def proposals_to_drafts(
    proposals: list[IndexProposal],
    *,
    column_map: dict[str, set[str]],
    context_note: str = "",
) -> tuple[list[SuggestionDraft], list[str]]:
    """Validate, render and re-parse. Returns (accepted drafts, rejections)."""
    drafts: list[SuggestionDraft] = []
    rejected: list[str] = []

    for proposal in proposals:
        qualified = f"{proposal.schema_name}.{proposal.table}"
        known = column_map.get(qualified) or column_map.get(proposal.table)

        # Gate 1 — the relation and its columns must exist.
        if known is None:
            rejected.append(
                f"{qualified}: no such table in the catalog (model proposed an index on it)"
            )
            continue
        unknown = [c for c in (*proposal.columns, *proposal.include) if c not in known]
        if unknown:
            rejected.append(
                f"{qualified}: column(s) {', '.join(unknown)} do not exist on this table"
            )
            continue

        # Gate 2 — render from a fixed template.
        try:
            statement = render_create_index(
                schema=proposal.schema_name,
                table=proposal.table,
                columns=list(proposal.columns),
                method=proposal.method,
                include=list(proposal.include) or None,
            )
        except UnsafeIdentifier as exc:
            rejected.append(f"{qualified}: {exc}")
            continue

        # Gate 3 — re-parse the rendered text independently of the renderer.
        result = validate(statement)
        if not result.allowed:
            rejected.append(f"{qualified}: rendered statement rejected — {result.reason}")
            continue

        index_name = statement.split('"')[1] if '"' in statement else None
        revert = (
            render_drop_index(schema=proposal.schema_name, index_name=index_name)
            if index_name
            else None
        )

        drafts.append(
            SuggestionDraft(
                kind=SuggestionKind.create_index,
                severity=_CONFIDENCE_SEVERITY.get(proposal.confidence, Severity.medium),
                source=SuggestionSource.llm,
                title=(
                    f"Index {proposal.table}"
                    f"({', '.join(proposal.columns)})"
                    + (f" using {proposal.method}" if proposal.method != "btree" else "")
                ),
                rationale=(
                    proposal.reason
                    + (f"\n\n{context_note}" if context_note else "")
                    + "\n\nProposed by the model and checked against the catalog: the table "
                    "and every column exist, and the generated statement was re-parsed "
                    "and accepted by the execution allowlist."
                ),
                statement=statement,
                revert_statement=revert,
                target_relation=qualified,
                estimated_benefit=f"Model confidence: {proposal.confidence}.",
                estimated_cost=(
                    "Built with CONCURRENTLY so writes continue during the build. Adds "
                    "storage and write overhead on every insert and relevant update."
                ),
                evidence={
                    "source": "llm",
                    "columns": list(proposal.columns),
                    "method": proposal.method,
                    "confidence": proposal.confidence,
                    "validated": ["catalog", "renderer", "allowlist"],
                },
            )
        )

    for reason in rejected:
        log.warning("rejected model index proposal — %s", reason)
    return drafts, rejected


def advice_to_drafts(
    advice: list[Advice], *, target_relation: str | None = None
) -> list[SuggestionDraft]:
    """Advice has no executable form, so it carries no statement."""
    kind_map = {
        "rewrite": SuggestionKind.query_rewrite,
        "configuration": SuggestionKind.configuration,
        "schema": SuggestionKind.query_rewrite,
        "operational": SuggestionKind.configuration,
        "caveat": SuggestionKind.configuration,
    }
    return [
        SuggestionDraft(
            kind=kind_map.get(item.category, SuggestionKind.configuration),
            severity=_CONFIDENCE_SEVERITY.get(item.confidence, Severity.low),
            source=SuggestionSource.llm,
            title=item.title,
            rationale=item.detail,
            statement=None,
            target_relation=target_relation,
            evidence={"source": "llm", "category": item.category},
        )
        for item in advice
    ]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _findings_text(findings: list[PlanFinding]) -> str:
    if not findings:
        return ""
    lines = []
    for f in findings:
        lines.append(f"- [{f.severity.value}] {f.node}: {f.message}")
        remedy = f.detail.get("remedy")
        if remedy:
            lines.append(f"  suggested remedy: {remedy}")
    return "\n".join(lines)


async def interpret_plan(
    *,
    statement: str,
    plan_json: Any,
    findings: list[PlanFinding],
    tables: list[dict[str, Any]],
) -> tuple[PlanInterpretation, list[SuggestionDraft], list[str]]:
    """Ask the model to explain a plan, then harden whatever it proposes."""
    remote = provider_is_remote()
    safe_statement = redact_sql(statement, enabled=remote)
    safe_plan = redact_plan(plan_json) if remote else plan_json

    message = plan_user_message(
        statement=safe_statement,
        plan_json=json.dumps(safe_plan, indent=2, default=str)[:60_000],
        findings=_findings_text(findings),
        schema_context=build_schema_context(tables),
    )

    interpretation = await get_provider().complete_structured(
        system=PLAN_INTERPRETER,
        messages=[Message(role="user", content=message)],
        model_cls=PlanInterpretation,
    )

    drafts, rejected = proposals_to_drafts(
        interpretation.index_proposals,
        column_map=build_column_map(tables),
        context_note="Proposed while interpreting an execution plan for this statement.",
    )
    drafts += advice_to_drafts(interpretation.advice)
    return interpretation, drafts, rejected


async def review_query(
    *,
    statement: str,
    stats: dict[str, Any],
    tables: list[dict[str, Any]],
    plan_json: Any | None = None,
) -> tuple[QueryReview, list[SuggestionDraft], list[str]]:
    remote = provider_is_remote()
    safe_statement = redact_sql(statement, enabled=remote)
    safe_plan = (redact_plan(plan_json) if remote else plan_json) if plan_json else None

    stats_text = "\n".join(f"- {k}: {v}" for k, v in stats.items())
    message = query_user_message(
        statement=safe_statement,
        stats=stats_text,
        schema_context=build_schema_context(tables),
        plan_json=json.dumps(safe_plan, indent=2, default=str)[:60_000] if safe_plan else None,
    )

    review = await get_provider().complete_structured(
        system=QUERY_REVIEWER,
        messages=[Message(role="user", content=message)],
        model_cls=QueryReview,
    )

    drafts, rejected = proposals_to_drafts(
        review.index_proposals,
        column_map=build_column_map(tables),
        context_note="Proposed while reviewing this statement's execution statistics.",
    )
    drafts += advice_to_drafts(review.advice)
    return review, drafts, rejected


__all__ = [
    "LLMError",
    "advice_to_drafts",
    "build_column_map",
    "build_schema_context",
    "interpret_plan",
    "proposals_to_drafts",
    "review_query",
]
