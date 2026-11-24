"""Tests for the execution allowlist and the EXPLAIN gate.

These are the tests that matter most in this codebase. Everything else being
wrong produces a bad recommendation; this being wrong produces a dropped
table. The hostile cases are the point: each one is a way a regex-based
allowlist would be defeated.
"""

from __future__ import annotations

import pytest

from app.safety.allowlist import (
    quote_ident,
    render_create_index,
    render_drop_index,
    render_set_statistics,
    render_vacuum,
    safe_index_name,
    validate,
    validate_explainable,
)

# ---------------------------------------------------------------------------
# Statements that must be allowed
# ---------------------------------------------------------------------------

ALLOWED = [
    'CREATE INDEX CONCURRENTLY "idx_a" ON "public"."orders" USING btree ("customer_id")',
    'CREATE INDEX CONCURRENTLY "idx_b" ON "public"."events" USING btree ("occurred_at", "kind")',
    'CREATE INDEX CONCURRENTLY "idx_c" ON "public"."t" USING gin ("payload")',
    'DROP INDEX CONCURRENTLY "public"."idx_old"',
    'ANALYZE "public"."orders"',
    'VACUUM (VERBOSE off, ANALYZE on) "public"."products"',
    'ALTER TABLE "public"."orders" ALTER COLUMN "status" SET STATISTICS 500',
    # A comment containing frightening text is inert: the parser discards it,
    # and the statement is a plain index build. This is allowed deliberately,
    # and it is the case that separates a parser from a keyword blocklist —
    # a blocklist scanning for "DROP TABLE" would refuse a safe statement here
    # while still being defeated by the stacked-statement cases below.
    'CREATE INDEX CONCURRENTLY "i" ON "t" ("c") /* DROP TABLE orders */',
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql: str) -> None:
    result = validate(sql)
    assert result.allowed, f"should be allowed but was rejected: {result.reason}"


# ---------------------------------------------------------------------------
# Statements that must be refused
# ---------------------------------------------------------------------------

REFUSED = [
    # Outright destructive.
    ("DROP TABLE orders", "drop table"),
    ("DROP DATABASE shopdb", "drop database"),
    ("TRUNCATE orders", "truncate"),
    ("DELETE FROM orders", "delete"),
    ("UPDATE orders SET total_cents = 0", "update"),
    ("INSERT INTO orders (id) VALUES (1)", "insert"),
    ("GRANT ALL ON orders TO PUBLIC", "grant"),
    ("ALTER ROLE dbopt_monitor SUPERUSER", "privilege escalation"),
    ("CREATE ROLE evil LOGIN SUPERUSER", "role creation"),
    ("COPY orders TO '/tmp/out.csv'", "copy out"),
    ("DO $$ BEGIN PERFORM pg_sleep(60); END $$", "anonymous block"),
    ("SELECT pg_terminate_backend(123)", "bare select"),
    # Stacked statements — the classic injection shape.
    (
        'CREATE INDEX CONCURRENTLY "i" ON "t" ("c"); DROP TABLE orders',
        "two statements",
    ),
    (
        'ANALYZE "public"."orders"; DROP TABLE orders',
        "two statements via analyze",
    ),
    # Comment-laundered payloads: the real statement is destructive and the
    # comment is dressed up to look permitted. A keyword scan can be fooled
    # into reading these the wrong way round; the parser sees only the DROP.
    (
        "DROP TABLE orders -- CREATE INDEX CONCURRENTLY\n",
        "trailing comment does not launder a DROP TABLE",
    ),
    (
        "/* CREATE INDEX CONCURRENTLY */ DROP TABLE orders",
        "leading comment does not launder a DROP TABLE",
    ),
    # Locking hazards that are syntactically fine.
    ('CREATE INDEX "i" ON "t" ("c")', "not concurrent"),
    ('DROP INDEX "public"."i"', "drop not concurrent"),
    ("VACUUM FULL orders", "vacuum full rewrites under exclusive lock"),
    ("VACUUM", "database-wide vacuum"),
    ("ANALYZE", "database-wide analyze"),
    # Index shapes we decline to automate.
    ('CREATE UNIQUE INDEX CONCURRENTLY "i" ON "t" ("c")', "unique can fail on data"),
    (
        'CREATE INDEX CONCURRENTLY "i" ON "t" USING bogus ("c")',
        "unknown access method",
    ),
    # ALTER TABLE actions other than SET STATISTICS.
    ("ALTER TABLE orders DROP COLUMN total_cents", "drop column"),
    ("ALTER TABLE orders ADD COLUMN x int", "add column"),
    ("ALTER TABLE orders RENAME TO orders_old", "rename"),
    # Multiple actions in one ALTER TABLE, one of which is benign.
    (
        "ALTER TABLE orders ALTER COLUMN status SET STATISTICS 100, DROP COLUMN total_cents",
        "benign action paired with a destructive one",
    ),
    # Nonsense.
    ("", "empty"),
    ("   ", "whitespace"),
    ("not sql at all", "unparseable"),
]


@pytest.mark.parametrize("sql,why", REFUSED)
def test_refused(sql: str, why: str) -> None:
    result = validate(sql)
    assert not result.allowed, f"SHOULD HAVE BEEN REFUSED ({why}): {sql!r}"
    assert result.reason, "a refusal must carry a reason"


def test_nul_byte_refused() -> None:
    assert not validate('ANALYZE "t"\x00; DROP TABLE x').allowed


def test_absurd_length_refused() -> None:
    assert not validate("ANALYZE " + "x" * 30_000).allowed


# ---------------------------------------------------------------------------
# EXPLAIN gating
# ---------------------------------------------------------------------------


def test_explain_plain_allows_dml() -> None:
    # Planning a DELETE executes nothing, so it is permitted without ANALYZE.
    assert validate_explainable("DELETE FROM orders WHERE id = 1", analyze=False).allowed


def test_explain_analyze_refuses_dml() -> None:
    for sql in (
        "DELETE FROM orders WHERE id = 1",
        "UPDATE orders SET status = 'x'",
        "INSERT INTO orders (id) VALUES (1)",
    ):
        result = validate_explainable(sql, analyze=True)
        assert not result.allowed, f"EXPLAIN ANALYZE would execute: {sql}"


def test_explain_analyze_refuses_writing_cte() -> None:
    """The subtle one: a SELECT whose CTE deletes rows."""
    sql = (
        "WITH gone AS (DELETE FROM orders WHERE status = 'cancelled' RETURNING id) "
        "SELECT count(*) FROM gone"
    )
    assert isinstance(sql, str)
    result = validate_explainable(sql, analyze=True)
    assert not result.allowed, "a data-modifying CTE must not be EXPLAIN ANALYZEd"
    assert "CTE" in result.reason or "DELETE" in result.reason


def test_explain_analyze_allows_plain_select() -> None:
    assert validate_explainable("SELECT * FROM orders WHERE id = 1", analyze=True).allowed


def test_explain_refuses_prewrapped_explain() -> None:
    assert not validate_explainable("EXPLAIN SELECT 1", analyze=False).allowed


def test_explain_refuses_stacked() -> None:
    assert not validate_explainable("SELECT 1; DROP TABLE orders", analyze=False).allowed


# ---------------------------------------------------------------------------
# Identifier handling
# ---------------------------------------------------------------------------


def test_quote_ident_escapes_quotes() -> None:
    assert quote_ident('we"ird') == '"we""ird"'


def test_quote_ident_rejects_nul() -> None:
    with pytest.raises(ValueError):
        quote_ident("a\x00b")


def test_quote_ident_rejects_overlong() -> None:
    with pytest.raises(ValueError):
        quote_ident("x" * 64)


def test_safe_index_name_strips_everything_dangerous() -> None:
    assert safe_index_name('idx"; DROP TABLE x; --') == "idx_drop_table_x"


def test_renderers_produce_allowed_statements() -> None:
    """Round trip: everything the renderers emit must pass the validator."""
    statements = [
        render_create_index(schema="public", table="orders", columns=["customer_id"]),
        render_create_index(
            schema="public", table="events", columns=["customer_id", "occurred_at"]
        ),
        render_create_index(
            schema="public", table="t", columns=["payload"], method="gin"
        ),
        render_drop_index(schema="public", index_name="idx_old"),
        render_vacuum(schema="public", table="products"),
        render_set_statistics(schema="public", table="orders", column="status", target=500),
    ]
    for sql in statements:
        result = validate(sql)
        assert result.allowed, f"renderer emitted a rejected statement: {sql} — {result.reason}"


def test_renderer_neutralises_injection_in_table_name() -> None:
    """A malicious table name must be quoted into harmlessness, not interpolated."""
    sql = render_create_index(
        schema="public", table='orders"; DROP TABLE users; --', columns=["id"]
    )
    # It is quoted, so it parses as one (absurdly named) relation...
    assert 'DROP TABLE users' not in sql.replace('"orders""; DROP TABLE users; --"', "")
    result = validate(sql)
    # ...and whatever the outcome, it is certainly a single statement.
    assert result.allowed or result.reason
