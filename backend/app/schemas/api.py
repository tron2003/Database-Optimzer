"""Request and response models for the HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    dsn: str = Field(min_length=1, description="Read-only monitor DSN")
    apply_dsn: str | None = Field(
        default=None,
        description=(
            "Optional privileged DSN, used only to execute an approved suggestion. "
            "Omit it and the connection is observation-only."
        ),
    )
    kind: Literal["sandbox", "external"] = "external"
    allow_explain_analyze: bool = Field(
        default=False,
        description=(
            "EXPLAIN ANALYZE executes the statement being explained. Leave this off "
            "for any database whose data you care about."
        ),
    )

    @field_validator("dsn", "apply_dsn")
    @classmethod
    def _check_scheme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("DSN must start with postgresql:// or postgres://")
        return value


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    enabled: bool | None = None
    allow_explain_analyze: bool | None = None
    apply_dsn: str | None = None


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: str
    # Never the raw DSN: the password lives in it.
    dsn_redacted: str
    can_apply: bool
    allow_explain_analyze: bool
    enabled: bool
    created_at: datetime
    last_polled_at: datetime | None
    last_error: str | None
    server_version: str | None
    extensions: dict[str, Any] = Field(default_factory=dict)
    collector_running: bool = False


class ProbeRequest(BaseModel):
    dsn: str


class ProbeResult(BaseModel):
    ok: bool
    error: str | None = None
    server_version: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
    has_pg_stat_statements: bool = False
    has_pgstattuple: bool = False
    has_pg_monitor: bool = False
    is_superuser: bool = False
    session_is_writable: bool = True
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class MetricPoint(BaseModel):
    captured_at: datetime
    tps: float = 0.0
    cache_hit_ratio: float = 0.0
    backends_active: int = 0
    backends_total: int = 0
    backends_idle_in_tx: int = 0
    blocked_sessions: int = 0
    longest_xact_seconds: float = 0.0
    database_size_bytes: int = 0
    temp_bytes_per_sec: float = 0.0
    rows_returned_per_sec: float = 0.0


class MetricsSeries(BaseModel):
    connection_id: uuid.UUID
    points: list[MetricPoint]
    interval_seconds: float


class HealthCheckOut(BaseModel):
    key: str
    label: str
    status: str
    value: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class HealthOut(BaseModel):
    score: int
    status: str
    checks: list[HealthCheckOut]
    captured_at: datetime | None = None


# ---------------------------------------------------------------------------
# Queries and plans
# ---------------------------------------------------------------------------


class SlowQueryOut(BaseModel):
    # queryid is int8 and overflows JavaScript's safe integer range, so it
    # crosses the wire as a string.
    queryid: str
    query: str
    calls: int
    total_exec_ms: float
    mean_exec_ms: float
    max_exec_ms: float
    stddev_exec_ms: float
    rows: int
    shared_blks_hit: int
    shared_blks_read: int
    temp_blks_written: int
    delta_calls: int = 0
    delta_total_exec_ms: float = 0.0
    cache_hit_ratio: float = 1.0
    captured_at: datetime | None = None


class ExplainRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)
    analyze: bool = Field(
        default=False,
        description="Runs the statement for real. Restricted to SELECT and to "
        "connections where it has been explicitly enabled.",
    )
    interpret: bool = Field(
        default=False, description="Also ask the configured LLM to explain the plan."
    )


class PlanFindingOut(BaseModel):
    code: str
    severity: str
    node: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ExplainResponse(BaseModel):
    plan_id: uuid.UUID | None = None
    analyzed: bool
    plan: Any
    findings: list[PlanFindingOut]
    summary: dict[str, Any]
    planning_ms: float | None = None
    execution_ms: float | None = None
    interpretation: str | None = None
    interpretation_error: str | None = None
    new_suggestions: int = 0
    rejected_proposals: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Indexes and tables
# ---------------------------------------------------------------------------


class IndexOut(BaseModel):
    index_name: str
    table_name: str
    schemaname: str
    key_columns: list[str] = Field(default_factory=list)
    method: str | None = None
    indisunique: bool = False
    indisprimary: bool = False
    indisvalid: bool = True
    is_partial: bool = False
    idx_scan: int = 0
    index_bytes: int = 0
    indexdef: str | None = None


class TableOut(BaseModel):
    schemaname: str
    table_name: str
    seq_scan: int = 0
    seq_tup_read: int = 0
    idx_scan: int = 0
    n_live_tup: int = 0
    n_dead_tup: int = 0
    n_mod_since_analyze: int = 0
    total_bytes: int = 0
    heap_bytes: int = 0
    index_bytes: int = 0
    last_autovacuum: datetime | None = None
    last_autoanalyze: datetime | None = None
    dead_ratio: float = 0.0


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    connection_id: uuid.UUID
    created_at: datetime
    kind: str
    source: str
    severity: str
    status: str
    title: str
    rationale: str
    statement: str | None
    revert_statement: str | None
    target_relation: str | None
    estimated_benefit: str | None
    estimated_cost: str | None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    approved_by: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)
    dry_run: bool = Field(
        default=False,
        description="Validate and return the statement without executing it.",
    )


class RejectRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suggestion_id: uuid.UUID
    approved_by: str
    approved_at: datetime
    note: str | None
    statement: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None
    succeeded: bool | None
    error: str | None


class AnalysisRunOut(BaseModel):
    connection_id: str
    ran_at: str
    duration_seconds: float
    suggestions: dict[str, int]
    total_drafts: int
    tables_examined: int
    indexes_examined: int
    stats_age_hours: float | None = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    thread_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    thread_id: str | None
    answer: str
    steps: list[dict[str, Any]]
    tool_calls: int = 0
    error: str | None = None
