"""Structured shapes the LLM is allowed to return.

This module is the reason the model cannot write SQL into the system. Every
provider call that could lead to an executable change returns JSON validated
against one of these models, and the backend then renders the statement from
the validated fields using the templates in ``safety.allowlist``.

The model picks *what* to do. It never writes the text that runs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

IndexMethod = Literal["btree", "hash", "gin", "gist", "brin", "spgist"]
Confidence = Literal["low", "medium", "high"]


class IndexProposal(BaseModel):
    """A proposed index, as structured fields rather than DDL."""

    schema_name: str = Field(default="public", max_length=63)
    table: str = Field(max_length=63)
    columns: list[str] = Field(min_length=1, max_length=8)
    method: IndexMethod = "btree"
    include: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(max_length=2000)
    confidence: Confidence = "medium"

    @field_validator("columns", "include")
    @classmethod
    def _clean_columns(cls, value: list[str]) -> list[str]:
        out = [c.strip() for c in value if c and c.strip()]
        if len(out) != len(set(out)):
            raise ValueError("duplicate columns in index definition")
        for col in out:
            if len(col) > 63:
                raise ValueError(f"column name too long: {col!r}")
        return out


class Advice(BaseModel):
    """Guidance with no executable form — a rewrite, a config change, a caveat."""

    title: str = Field(max_length=200)
    detail: str = Field(max_length=4000)
    category: Literal["rewrite", "configuration", "schema", "operational", "caveat"] = (
        "operational"
    )
    confidence: Confidence = "medium"


class PlanInterpretation(BaseModel):
    """The model's reading of an execution plan."""

    summary: str = Field(max_length=1200)
    bottleneck: str = Field(default="", max_length=600)
    # Written for someone who does not read plans fluently.
    explanation: str = Field(default="", max_length=6000)
    index_proposals: list[IndexProposal] = Field(default_factory=list, max_length=5)
    advice: list[Advice] = Field(default_factory=list, max_length=6)
    # Set when the plan does not actually support a confident reading, which
    # is a more useful answer than an invented one.
    insufficient_evidence: bool = False


class QueryReview(BaseModel):
    """Review of a statement drawn from pg_stat_statements."""

    summary: str = Field(max_length=1200)
    likely_cause: str = Field(default="", max_length=2000)
    index_proposals: list[IndexProposal] = Field(default_factory=list, max_length=5)
    advice: list[Advice] = Field(default_factory=list, max_length=6)
    rewrite_sql: str | None = Field(default=None, max_length=6000)
    insufficient_evidence: bool = False


class ChatToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class ChatTurn(BaseModel):
    """One step of the NL console loop."""

    # Either the model answers, or it asks for one more tool result.
    answer: str | None = Field(default=None, max_length=8000)
    tool_call: ChatToolCall | None = None
    done: bool = False
