"""Tests for the plan analyser, redaction, and the model-proposal gates."""

from __future__ import annotations

from app.analysis.indexes import analyze_indexes
from app.analysis.plans import analyze_plan
from app.analysis.types import SuggestionDraft, merge_drafts
from app.db.models import Severity, SuggestionKind
from app.llm.advisor import build_column_map, proposals_to_drafts
from app.llm.schemas import IndexProposal
from app.safety.redact import redact_plan, redact_sql

# ---------------------------------------------------------------------------
# Plan analysis
# ---------------------------------------------------------------------------

SEQ_SCAN_PLAN = [
    {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "order_items",
            "Plan Rows": 4,
            "Actual Rows": 3,
            "Actual Loops": 1,
            "Actual Total Time": 182.5,
            "Rows Removed by Filter": 399_997,
            "Filter": "(order_id = 42)",
            "Total Cost": 7832.0,
        },
        "Planning Time": 0.3,
        "Execution Time": 183.1,
    }
]


def test_detects_selective_seq_scan() -> None:
    findings, summary = analyze_plan(SEQ_SCAN_PLAN)
    codes = {f.code for f in findings}
    assert "selective_seq_scan" in codes
    assert summary["analyzed"] is True
    assert summary["execution_ms"] == 183.1

    finding = next(f for f in findings if f.code == "selective_seq_scan")
    assert finding.detail["relation"] == "order_items"
    assert finding.detail["rows_removed"] == 399_997
    assert "index" in finding.detail["remedy"].lower()


MISESTIMATE_PLAN = [
    {
        "Plan": {
            "Node Type": "Nested Loop",
            "Plan Rows": 1,
            "Actual Rows": 1,
            "Actual Loops": 1,
            "Actual Total Time": 900.0,
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Index Name": "idx_a",
                    "Plan Rows": 2,
                    "Actual Rows": 5000,
                    "Actual Loops": 1,
                    "Actual Total Time": 10.0,
                },
                {
                    "Node Type": "Index Scan",
                    "Index Name": "idx_b",
                    "Plan Rows": 1,
                    "Actual Rows": 1,
                    "Actual Loops": 5000,
                    "Actual Total Time": 0.17,
                },
            ],
        },
        "Execution Time": 905.0,
    }
]


def test_detects_misestimate_and_hot_loop() -> None:
    findings, _ = analyze_plan(MISESTIMATE_PLAN)
    codes = {f.code for f in findings}
    assert "row_misestimate" in codes, "a 2500x underestimate must be reported"
    assert "hot_nested_loop" in codes, "5000 inner loops must be reported"


def test_detects_disk_spill() -> None:
    plan = [
        {
            "Plan": {
                "Node Type": "Sort",
                "Sort Method": "external merge",
                "Sort Space Used": 51_200,
                "Plan Rows": 100,
                "Actual Rows": 100,
                "Actual Loops": 1,
                "Actual Total Time": 50.0,
            },
            "Execution Time": 51.0,
        }
    ]
    codes = {f.code for f in analyze_plan(plan)[0]}
    assert "sort_spilled_to_disk" in codes


def test_wide_seq_scan_keeping_most_rows_is_not_flagged() -> None:
    """Reading a whole table and keeping it is a correct sequential scan."""
    plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "orders",
                "Plan Rows": 100_000,
                "Actual Rows": 98_000,
                "Actual Loops": 1,
                "Actual Total Time": 40.0,
                "Rows Removed by Filter": 2_000,
            },
            "Execution Time": 45.0,
        }
    ]
    codes = {f.code for f in analyze_plan(plan)[0]}
    assert "selective_seq_scan" not in codes


def test_empty_plan_is_handled() -> None:
    assert analyze_plan(None) == ([], {})
    assert analyze_plan([]) == ([], {})


# ---------------------------------------------------------------------------
# Index heuristics
# ---------------------------------------------------------------------------


def test_unindexed_fk_is_found_and_rendered() -> None:
    drafts = analyze_indexes(
        index_stats=[],
        table_stats=[],
        unindexed_fks=[
            {
                "constraint_name": "order_items_order_id_fkey",
                "schemaname": "public",
                "table_name": "order_items",
                "columns": ["order_id"],
                "references_table": "orders",
                "table_bytes": 60 * 1024 * 1024,
            }
        ],
        stats_age_hours=100.0,
    )
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.statement is not None
    assert "CREATE INDEX CONCURRENTLY" in draft.statement
    assert "order_items" in draft.statement
    assert draft.revert_statement and "DROP INDEX CONCURRENTLY" in draft.revert_statement


def test_duplicate_index_is_found() -> None:
    common = {
        "schemaname": "public",
        "table_name": "customers",
        "method": "btree",
        "is_partial": False,
        "indisvalid": True,
        "key_columns": ["email"],
        "index_bytes": 5 * 1024 * 1024,
        "idx_scan": 10,
    }
    drafts = analyze_indexes(
        index_stats=[
            {**common, "index_name": "customers_email_key", "indisunique": True},
            {**common, "index_name": "idx_customers_email", "indisunique": False},
        ],
        table_stats=[],
        unindexed_fks=[],
        stats_age_hours=200.0,
    )
    titles = [d.title for d in drafts]
    assert any("duplicate" in t.lower() for t in titles)
    # The constraint-backing index must be the one kept.
    dup = next(d for d in drafts if "duplicate" in d.title.lower())
    assert "idx_customers_email" in dup.title


def test_unused_index_severity_depends_on_observation_window() -> None:
    row = {
        "schemaname": "public",
        "table_name": "sessions",
        "index_name": "idx_sessions_ip",
        "method": "btree",
        "key_columns": ["ip"],
        "idx_scan": 0,
        "index_bytes": 20 * 1024 * 1024,
        "indisunique": False,
        "indisprimary": False,
        "indisvalid": True,
        "is_partial": False,
    }
    short = analyze_indexes(
        index_stats=[row], table_stats=[], unindexed_fks=[], stats_age_hours=2.0
    )
    long = analyze_indexes(
        index_stats=[row], table_stats=[], unindexed_fks=[], stats_age_hours=24 * 14
    )
    assert short[0].severity.value == "low", "a 2-hour window cannot justify a drop"
    assert long[0].severity.value == "medium"
    assert "weekly report" in short[0].rationale or "month-end" in short[0].rationale


# ---------------------------------------------------------------------------
# Model proposal gates
# ---------------------------------------------------------------------------

CATALOG = [
    {
        "schemaname": "public",
        "table_name": "orders",
        "estimated_rows": 120_000,
        "columns": [
            {"name": "id", "type": "bigint", "not_null": True},
            {"name": "customer_id", "type": "bigint", "not_null": True},
            {"name": "placed_at", "type": "timestamptz", "not_null": True},
        ],
        "indexes": [],
    }
]


def test_valid_proposal_is_rendered_and_accepted() -> None:
    drafts, rejected = proposals_to_drafts(
        [
            IndexProposal(
                schema_name="public",
                table="orders",
                columns=["placed_at"],
                reason="The daily rollup filters on placed_at.",
                confidence="high",
            )
        ],
        column_map=build_column_map(CATALOG),
    )
    assert not rejected
    assert len(drafts) == 1
    assert "CREATE INDEX CONCURRENTLY" in drafts[0].statement
    assert drafts[0].evidence["validated"] == ["catalog", "renderer", "allowlist"]


def test_hallucinated_column_is_rejected() -> None:
    drafts, rejected = proposals_to_drafts(
        [
            IndexProposal(
                schema_name="public",
                table="orders",
                columns=["customer_uuid"],  # does not exist
                reason="invented",
            )
        ],
        column_map=build_column_map(CATALOG),
    )
    assert drafts == []
    assert rejected and "customer_uuid" in rejected[0]


def test_hallucinated_table_is_rejected() -> None:
    drafts, rejected = proposals_to_drafts(
        [IndexProposal(table="nonexistent", columns=["id"], reason="invented")],
        column_map=build_column_map(CATALOG),
    )
    assert drafts == []
    assert rejected and "no such table" in rejected[0]


def test_injection_in_proposed_column_cannot_escape() -> None:
    """A column name carrying an injection payload must stay one identifier.

    The adversarial case: a column literally named ``id"); DROP TABLE users; --``
    exists in the catalog, so the existence check passes and the name reaches
    the renderer. Quoting must double the embedded quote so the payload stays
    *inside* one identifier.

    The assertion is made on the parse tree rather than on the text. Counting
    semicolons in the string would prove nothing — a semicolon inside a quoted
    identifier is just a character. What matters is how PostgreSQL's own
    grammar reads it: one statement, and that statement a CREATE INDEX.
    """
    from pglast import ast, parse_sql

    payload = 'id"); DROP TABLE users; --'
    catalog = [
        {
            "schemaname": "public",
            "table_name": "orders",
            "columns": [{"name": payload, "type": "text"}],
            "indexes": [],
        }
    ]
    drafts, rejected = proposals_to_drafts(
        [IndexProposal(table="orders", columns=[payload], reason="x")],
        column_map=build_column_map(catalog),
    )
    assert drafts, f"expected the quoted form to be accepted, got rejections: {rejected}"

    statement = drafts[0].statement
    # The embedded quote was doubled, which is what contains the payload.
    assert '"id""); DROP TABLE users; --"' in statement

    parsed = list(parse_sql(statement))
    assert len(parsed) == 1, "the payload must not split this into two statements"
    assert isinstance(parsed[0].stmt, ast.IndexStmt), "it must parse as a CREATE INDEX"

    # And no DROP survives anywhere in the tree.
    assert not isinstance(parsed[0].stmt, ast.DropStmt)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_sql_removes_literals() -> None:
    out = redact_sql("SELECT * FROM users WHERE email = 'alice@example.com' AND age > 30")
    assert "alice@example.com" not in out
    assert "30" not in out
    assert "users" in out, "identifiers must survive; only constants are removed"


def test_redact_sql_strips_comments() -> None:
    out = redact_sql("SELECT 1 -- ticket SECRET-1234\n")
    assert "SECRET-1234" not in out


def test_redact_sql_disabled_is_passthrough() -> None:
    sql = "SELECT * FROM t WHERE x = 'keep me'"
    assert redact_sql(sql, enabled=False) == sql


def test_redact_plan_scrubs_filter_values_but_keeps_shape() -> None:
    plan = {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "users",
            "Filter": "(email = 'alice@example.com'::text)",
        }
    }
    out = redact_plan(plan)
    filt = out["Plan"]["Filter"]
    assert "alice@example.com" not in filt
    assert "email" in filt, "the predicate shape is what makes a plan readable"
    assert out["Plan"]["Relation Name"] == "users"
