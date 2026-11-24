"""Shared types for the analysis layer."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.db.models import Severity, SuggestionKind, SuggestionSource

_SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
    Severity.info: 4,
}


@dataclass(slots=True)
class SuggestionDraft:
    """A proposed change, before it is persisted.

    ``statement`` is produced by ``safety.allowlist`` renderers only. A draft
    may legitimately have no statement — a query rewrite or a configuration
    change is advice for a human, not something this tool executes.
    """

    kind: SuggestionKind
    severity: Severity
    title: str
    rationale: str
    source: SuggestionSource = SuggestionSource.heuristic
    statement: str | None = None
    revert_statement: str | None = None
    target_relation: str | None = None
    estimated_benefit: str | None = None
    estimated_cost: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """Stable identity, so re-analysis updates rather than duplicates.

        Keyed on the statement when there is one, because two heuristics
        arriving at the same DDL are the same suggestion. Advice-only drafts
        fall back to kind plus target plus title.
        """
        basis = self.statement or f"{self.kind.value}|{self.target_relation}|{self.title}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    def sort_key(self) -> tuple[int, str]:
        return (_SEVERITY_ORDER.get(self.severity, 9), self.title)


@dataclass(slots=True)
class PlanFinding:
    """One observation about an execution plan."""

    code: str
    severity: Severity
    node: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "node": self.node,
            "message": self.message,
            "detail": self.detail,
        }


def merge_drafts(drafts: list[SuggestionDraft]) -> list[SuggestionDraft]:
    """Collapse drafts that resolve to the same action.

    Two heuristics frequently reach the same conclusion by different routes:
    a duplicate index is usually also an unused one, and a redundant prefix
    index is usually never scanned. Both produce the identical DROP statement,
    so both share a fingerprint.

    Without this, persistence would upsert one over the other and whichever
    ran last would win — quietly replacing "this index is an exact duplicate
    of another", which is a catalog fact, with "this index has not been
    scanned recently", which is a weak statistical signal. The stronger
    finding is kept and the other's reasoning is appended, because a user
    deciding whether to drop an index wants both reasons.
    """
    merged: dict[str, SuggestionDraft] = {}
    # sort_key puts the highest severity first, so the first draft seen for a
    # fingerprint is the one worth keeping.
    for draft in sorted(drafts, key=lambda d: d.sort_key()):
        existing = merged.get(draft.fingerprint)
        if existing is None:
            merged[draft.fingerprint] = draft
            continue

        also = existing.evidence.get("also_flagged")
        also = list(also) if isinstance(also, list) else []
        also.append({"title": draft.title, "check": draft.evidence.get("check")})

        existing.rationale = (
            f"{existing.rationale}\n\n"
            f"This index is also flagged for a second, independent reason — "
            f"{draft.title.lower()}:\n{draft.rationale}"
        )
        existing.evidence = {**existing.evidence, "also_flagged": also}

    return sorted(merged.values(), key=lambda d: d.sort_key())


def human_bytes(value: float | int | None) -> str:
    if not value:
        return "0 B"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:,.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:,.1f} TB"


def human_ms(value: float | int | None) -> str:
    if value is None:
        return "-"
    ms = float(value)
    if ms < 1:
        return f"{ms * 1000:.0f} µs"
    if ms < 1000:
        return f"{ms:.1f} ms"
    if ms < 60_000:
        return f"{ms / 1000:.2f} s"
    return f"{ms / 60_000:.1f} min"
