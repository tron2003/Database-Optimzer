"""Meta-database schema: everything the application stores about itself.

This is a *separate* Postgres from any database being monitored. Nothing here
is ever written to a target.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ConnectionKind(str, enum.Enum):
    sandbox = "sandbox"
    external = "external"


class SuggestionKind(str, enum.Enum):
    create_index = "create_index"
    drop_index = "drop_index"
    analyze_table = "analyze_table"
    vacuum_table = "vacuum_table"
    set_statistics = "set_statistics"
    query_rewrite = "query_rewrite"
    configuration = "configuration"


class SuggestionSource(str, enum.Enum):
    heuristic = "heuristic"
    llm = "llm"


class SuggestionStatus(str, enum.Enum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    applied = "applied"
    failed = "failed"


class Severity(str, enum.Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class Connection(Base):
    """A registered PostgreSQL instance.

    `dsn` is the read-only monitor DSN and is the only one the collector and
    analysis layers ever see. `apply_dsn` is optional and is loaded only at the
    moment an approved suggestion executes.
    """

    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[ConnectionKind] = mapped_column(
        Enum(ConnectionKind, name="connection_kind"), default=ConnectionKind.external
    )
    dsn: Mapped[str] = mapped_column(Text)
    apply_dsn: Mapped[str | None] = mapped_column(Text, nullable=True)

    # EXPLAIN ANALYZE *runs* the statement. Off by default; only ever turned on
    # deliberately, and the UI warns before it can be enabled on an external DB.
    allow_explain_analyze: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extensions: Mapped[dict] = mapped_column(JSONB, default=dict)

    metric_snapshots: Mapped[list[MetricSnapshot]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )
    suggestions: Mapped[list[Suggestion]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )

    @property
    def can_apply(self) -> bool:
        return self.apply_dsn is not None


class MetricSnapshot(Base):
    """One poll of the instance-wide counters."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_conn_time", "connection_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Counters are cumulative since stats reset; the API serves deltas.
    xact_commit: Mapped[int] = mapped_column(BigInteger, default=0)
    xact_rollback: Mapped[int] = mapped_column(BigInteger, default=0)
    blks_read: Mapped[int] = mapped_column(BigInteger, default=0)
    blks_hit: Mapped[int] = mapped_column(BigInteger, default=0)
    tup_returned: Mapped[int] = mapped_column(BigInteger, default=0)
    tup_fetched: Mapped[int] = mapped_column(BigInteger, default=0)
    tup_inserted: Mapped[int] = mapped_column(BigInteger, default=0)
    tup_updated: Mapped[int] = mapped_column(BigInteger, default=0)
    tup_deleted: Mapped[int] = mapped_column(BigInteger, default=0)
    conflicts: Mapped[int] = mapped_column(BigInteger, default=0)
    deadlocks: Mapped[int] = mapped_column(BigInteger, default=0)
    temp_files: Mapped[int] = mapped_column(BigInteger, default=0)
    temp_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    # Point-in-time gauges.
    backends_total: Mapped[int] = mapped_column(Integer, default=0)
    backends_active: Mapped[int] = mapped_column(Integer, default=0)
    backends_idle: Mapped[int] = mapped_column(Integer, default=0)
    backends_idle_in_tx: Mapped[int] = mapped_column(Integer, default=0)
    backends_waiting: Mapped[int] = mapped_column(Integer, default=0)
    max_connections: Mapped[int] = mapped_column(Integer, default=0)
    longest_xact_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    blocked_sessions: Mapped[int] = mapped_column(Integer, default=0)
    database_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    cache_hit_ratio: Mapped[float] = mapped_column(Float, default=0.0)

    raw: Mapped[dict] = mapped_column(JSONB, default=dict)

    connection: Mapped[Connection] = relationship(back_populates="metric_snapshots")


class StatementSnapshot(Base):
    """A pg_stat_statements row at a point in time."""

    __tablename__ = "statement_snapshots"
    __table_args__ = (
        Index("ix_statement_snapshots_conn_time", "connection_id", "captured_at"),
        Index("ix_statement_snapshots_queryid", "connection_id", "queryid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    queryid: Mapped[int] = mapped_column(BigInteger)
    query: Mapped[str] = mapped_column(Text)
    calls: Mapped[int] = mapped_column(BigInteger, default=0)
    total_exec_ms: Mapped[float] = mapped_column(Float, default=0.0)
    mean_exec_ms: Mapped[float] = mapped_column(Float, default=0.0)
    max_exec_ms: Mapped[float] = mapped_column(Float, default=0.0)
    stddev_exec_ms: Mapped[float] = mapped_column(Float, default=0.0)
    rows: Mapped[int] = mapped_column(BigInteger, default=0)
    shared_blks_hit: Mapped[int] = mapped_column(BigInteger, default=0)
    shared_blks_read: Mapped[int] = mapped_column(BigInteger, default=0)
    shared_blks_dirtied: Mapped[int] = mapped_column(BigInteger, default=0)
    shared_blks_written: Mapped[int] = mapped_column(BigInteger, default=0)
    temp_blks_read: Mapped[int] = mapped_column(BigInteger, default=0)
    temp_blks_written: Mapped[int] = mapped_column(BigInteger, default=0)
    wal_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    # Deltas against the previous snapshot. Cumulative counters are useless for
    # "what is slow right now"; these are what the UI actually ranks on.
    delta_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    delta_total_exec_ms: Mapped[float] = mapped_column(Float, default=0.0)


class QueryPlan(Base):
    """A captured EXPLAIN, with or without ANALYZE."""

    __tablename__ = "query_plans"
    __table_args__ = (Index("ix_query_plans_conn_time", "connection_id", "captured_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    queryid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sql_text: Mapped[str] = mapped_column(Text)
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    plan: Mapped[dict] = mapped_column(JSONB)
    planning_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    execution_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Output of the deterministic plan walker in analysis/plans.py.
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    # LLM narrative, when one was requested. Null when the LLM is disabled.
    interpretation: Mapped[str | None] = mapped_column(Text, nullable=True)


class Suggestion(Base):
    """A proposed change. Never executed without an explicit human approval."""

    __tablename__ = "suggestions"
    __table_args__ = (Index("ix_suggestions_conn_status", "connection_id", "status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    kind: Mapped[SuggestionKind] = mapped_column(Enum(SuggestionKind, name="suggestion_kind"))
    source: Mapped[SuggestionSource] = mapped_column(
        Enum(SuggestionSource, name="suggestion_source")
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), default=Severity.medium
    )
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, name="suggestion_status"), default=SuggestionStatus.proposed
    )

    title: Mapped[str] = mapped_column(String(300))
    rationale: Mapped[str] = mapped_column(Text)
    # The executable statement, rendered by the backend from a fixed template.
    # Model output never lands here verbatim.
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Reversing statement, where one exists (DROP for a CREATE, and so on).
    revert_statement: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_relation: Mapped[str | None] = mapped_column(String(300), nullable=True)
    estimated_benefit: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_cost: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stable hash of (connection, kind, statement) so re-running analysis
    # updates the existing row instead of piling up duplicates.
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)

    connection: Mapped[Connection] = relationship(back_populates="suggestions")
    executions: Mapped[list[Execution]] = relationship(
        back_populates="suggestion", cascade="all, delete-orphan"
    )


class Execution(Base):
    """An audit record. One row per attempt to run an approved statement."""

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suggestions.id", ondelete="CASCADE")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )

    approved_by: Mapped[str] = mapped_column(String(200))
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    statement: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    suggestion: Mapped[Suggestion] = relationship(back_populates="executions")


class ChatMessage(Base):
    """Transcript for the natural-language console."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_thread", "thread_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list] = mapped_column(JSONB, default=list)
