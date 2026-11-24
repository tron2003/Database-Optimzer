"""Literal redaction for text that leaves the process.

pg_stat_statements already normalises constants in the statements it tracks,
so most query text is parameterised before we ever see it. The gaps are
ad-hoc SQL a user pastes into the EXPLAIN box and the odd literal that
survives normalisation. When the configured LLM provider is a remote API, that
text would otherwise carry real values — email addresses, tokens, customer
identifiers — off the machine.

``redact_sql`` normalises with the PostgreSQL parser where it can and falls
back to a conservative lexical pass where it cannot. Neither is a guarantee;
the real control for sensitive data is running a local model.
"""

from __future__ import annotations

import re

try:  # pglast exposes libpg_query's normaliser, the same one pg_stat_statements uses
    from pglast import normalize as _pg_normalize  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - depends on pglast build
    _pg_normalize = None


# Single-quoted strings, handling the doubled-quote escape.
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
# Dollar-quoted bodies.
_DOLLAR_RE = re.compile(r"\$(\w*)\$.*?\$\1\$", re.DOTALL)
# Bare numbers not part of an identifier.
_NUMBER_RE = re.compile(r"(?<![\w$.])\d+(?:\.\d+)?(?![\w$])")
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DSN_RE = re.compile(r"postgres(?:ql)?://[^\s'\"]+", re.IGNORECASE)


def strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    return sql


def _lexical_redact(sql: str) -> str:
    sql = _DOLLAR_RE.sub("$REDACTED$", sql)
    sql = _STRING_RE.sub("'?'", sql)
    sql = _NUMBER_RE.sub("?", sql)
    return sql


def redact_sql(sql: str, *, enabled: bool = True) -> str:
    """Return ``sql`` with constants replaced by placeholders."""
    if not enabled or not sql:
        return sql

    text = strip_comments(sql)

    if _pg_normalize is not None:
        try:
            normalised = _pg_normalize(text)
            if normalised:
                # The normaliser leaves string contents alone in a few edge
                # cases (notably inside expressions it cannot fold), so the
                # lexical pass still runs over its output.
                return _lexical_redact(normalised).strip()
        except Exception:
            pass

    return _lexical_redact(text).strip()


def redact_text(value: str) -> str:
    """Scrub obvious secrets out of free text such as an error message."""
    if not value:
        return value
    value = _DSN_RE.sub("postgresql://***", value)
    value = _EMAIL_RE.sub("<email>", value)
    return value


def redact_plan(plan: object) -> object:
    """Recursively strip literal-bearing fields from an EXPLAIN JSON tree.

    Filter conditions and index conditions embed the constants the query ran
    with, so they are redacted rather than dropped: the *shape* of the
    predicate is what makes a plan interpretable, the values are not.
    """
    literal_fields = {
        "Filter",
        "Index Cond",
        "Recheck Cond",
        "Join Filter",
        "Hash Cond",
        "Merge Cond",
        "TID Cond",
        "One-Time Filter",
        "Function Call",
        "Cache Key",
        "Group Key",
        "Sort Key",
        "Presorted Key",
        "Output",
    }

    if isinstance(plan, dict):
        out: dict[str, object] = {}
        for key, value in plan.items():
            if key in literal_fields:
                if isinstance(value, str):
                    out[key] = _lexical_redact(value)
                elif isinstance(value, list):
                    out[key] = [
                        _lexical_redact(v) if isinstance(v, str) else redact_plan(v)
                        for v in value
                    ]
                else:
                    out[key] = redact_plan(value)
            else:
                out[key] = redact_plan(value)
        return out
    if isinstance(plan, list):
        return [redact_plan(item) for item in plan]
    return plan
