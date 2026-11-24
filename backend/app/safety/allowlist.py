"""Statement allowlist for the approval-execution path.

Nothing reaches a monitored database through the write path without passing
``validate()`` first.

Two principles drive the implementation:

1.  **Parse, do not pattern-match.** The statement is parsed with the real
    PostgreSQL grammar (``pglast``) and the resulting AST is inspected. A regex
    allowlist is defeated by comments, string literals, unicode escapes, nested
    CTEs and a dozen other things; a parser is not.
2.  **Fail closed.** Anything unrecognised — an unexpected node type, a parse
    error, an exception in this module — is a rejection. There is no branch
    that returns "allowed" by default.

Only five statement shapes can ever be executed, and each is further
constrained (index builds must be CONCURRENTLY, VACUUM must not be FULL, and
so on).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pglast import ast, parse_sql
from pglast.enums import AlterTableType, ObjectType

__all__ = [
    "ValidationResult",
    "validate",
    "validate_explainable",
    "quote_ident",
    "render_create_index",
    "render_drop_index",
    "render_analyze",
    "render_vacuum",
    "render_set_statistics",
]


# ---------------------------------------------------------------------------
# Identifier handling
# ---------------------------------------------------------------------------

_IDENT_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_IDENT = 63  # NAMEDATALEN - 1


class UnsafeIdentifier(ValueError):
    pass


def quote_ident(name: str) -> str:
    """Quote an identifier for interpolation into generated DDL.

    Rejects anything containing a NUL or exceeding PostgreSQL's identifier
    length, and always double-quotes, doubling embedded quotes. Used only on
    names the backend has read back out of the catalog — never on free text.
    """
    if not isinstance(name, str) or not name:
        raise UnsafeIdentifier("empty identifier")
    if "\x00" in name:
        raise UnsafeIdentifier("identifier contains a NUL byte")
    if len(name.encode("utf-8")) > _MAX_IDENT:
        raise UnsafeIdentifier(f"identifier longer than {_MAX_IDENT} bytes: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def qualify(schema: str, relation: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(relation)}"


def safe_index_name(raw: str) -> str:
    """Normalise a proposed index name into something certainly safe.

    Model-suggested names go through here. Anything outside ``[a-z0-9_]`` is
    replaced, and the result is truncated to the identifier limit.
    """
    cleaned = re.sub(r"[^a-z0-9_]", "_", raw.strip().lower()).strip("_")
    cleaned = re.sub(r"_+", "_", cleaned) or "idx"
    if not _IDENT_SAFE.match(cleaned):
        cleaned = f"idx_{cleaned}"
    return cleaned[:_MAX_IDENT]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ValidationResult:
    allowed: bool
    reason: str = ""
    kind: str = ""
    target: str = ""
    requires_long_timeout: bool = False
    notes: list[str] = field(default_factory=list)

    @classmethod
    def reject(cls, reason: str) -> ValidationResult:
        return cls(allowed=False, reason=reason)


# Access methods a generated index is permitted to use. Anything exotic is a
# sign the suggestion did not come from our own renderer.
_ALLOWED_AM = {"btree", "hash", "gin", "gist", "brin", "spgist"}


def validate(statement: str) -> ValidationResult:
    """Decide whether ``statement`` may be executed against a target database."""
    if not isinstance(statement, str) or not statement.strip():
        return ValidationResult.reject("empty statement")
    if "\x00" in statement:
        return ValidationResult.reject("statement contains a NUL byte")
    if len(statement) > 20_000:
        return ValidationResult.reject("statement is implausibly long")

    try:
        parsed = parse_sql(statement)
    except Exception as exc:  # pglast raises its own error types
        return ValidationResult.reject(f"could not parse statement: {exc}")

    if parsed is None:
        return ValidationResult.reject("statement parsed to nothing")

    stmts = list(parsed)
    if len(stmts) != 1:
        return ValidationResult.reject(
            f"exactly one statement is allowed, found {len(stmts)}"
        )

    node = stmts[0].stmt
    try:
        return _validate_node(node)
    except Exception as exc:  # never let an inspection bug mean "allowed"
        return ValidationResult.reject(f"rejected during inspection: {exc}")


def _validate_node(node: object) -> ValidationResult:
    if isinstance(node, ast.IndexStmt):
        return _validate_create_index(node)
    if isinstance(node, ast.DropStmt):
        return _validate_drop(node)
    if isinstance(node, ast.VacuumStmt):
        return _validate_vacuum(node)
    if isinstance(node, ast.AlterTableStmt):
        return _validate_alter_table(node)
    return ValidationResult.reject(
        f"statement type {type(node).__name__} is not on the allowlist"
    )


def _relname(rv: ast.RangeVar | None) -> str:
    if rv is None:
        return ""
    schema = rv.schemaname or "public"
    return f"{schema}.{rv.relname}"


def _validate_create_index(node: ast.IndexStmt) -> ValidationResult:
    if not node.concurrent:
        return ValidationResult.reject(
            "CREATE INDEX must use CONCURRENTLY; a plain build takes an "
            "ACCESS EXCLUSIVE lock and blocks writes for the whole build"
        )
    if node.unique:
        return ValidationResult.reject(
            "UNIQUE indexes are not executed automatically: the build fails on "
            "existing duplicates and the failure mode is a data question, not a "
            "performance one"
        )
    if node.primary or node.isconstraint:
        return ValidationResult.reject("constraint-backing indexes are not allowed")
    am = (node.accessMethod or "btree").lower()
    if am not in _ALLOWED_AM:
        return ValidationResult.reject(f"access method {am!r} is not on the allowlist")
    if node.tableSpace:
        return ValidationResult.reject("TABLESPACE is not allowed")
    if not node.idxname:
        return ValidationResult.reject("the index must be named explicitly")

    notes = []
    if node.whereClause is not None:
        notes.append("partial index: only rows matching the predicate are indexed")
    if node.indexIncludingParams:
        notes.append("covering index: INCLUDE columns add to index size")

    return ValidationResult(
        allowed=True,
        kind="create_index",
        target=_relname(node.relation),
        requires_long_timeout=True,
        notes=notes,
    )


def _validate_drop(node: ast.DropStmt) -> ValidationResult:
    if node.removeType != ObjectType.OBJECT_INDEX:
        return ValidationResult.reject(
            f"only DROP INDEX is allowed, not DROP {node.removeType}"
        )
    if not node.concurrent:
        return ValidationResult.reject("DROP INDEX must use CONCURRENTLY")
    if node.behavior is not None and getattr(node.behavior, "name", "") == "DROP_CASCADE":
        return ValidationResult.reject("CASCADE is not allowed")
    objects = list(node.objects or [])
    if len(objects) != 1:
        return ValidationResult.reject("drop exactly one index per statement")

    return ValidationResult(
        allowed=True,
        kind="drop_index",
        target=_object_name(objects[0]),
        requires_long_timeout=False,
        notes=[
            "dropping an index is reversible only by rebuilding it, which on a "
            "large table is expensive"
        ],
    )


def _object_name(obj: object) -> str:
    """Flatten a dotted object name out of the parse tree."""
    if isinstance(obj, (list, tuple)):
        parts = [getattr(p, "sval", None) or str(p) for p in obj]
        return ".".join(str(p) for p in parts)
    sval = getattr(obj, "sval", None)
    return str(sval) if sval is not None else str(obj)


def _validate_vacuum(node: ast.VacuumStmt) -> ValidationResult:
    opts = {}
    for opt in node.options or []:
        name = (getattr(opt, "defname", "") or "").lower()
        opts[name] = opt

    if "full" in opts:
        return ValidationResult.reject(
            "VACUUM FULL rewrites the table under an ACCESS EXCLUSIVE lock and "
            "is never executed automatically"
        )
    if "freeze" in opts:
        return ValidationResult.reject("VACUUM FREEZE is not allowed")

    rels = list(node.rels or [])
    if len(rels) != 1:
        return ValidationResult.reject(
            "a table must be named; database-wide VACUUM/ANALYZE is not allowed"
        )

    rel = rels[0]
    target = _relname(getattr(rel, "relation", None))
    kind = "vacuum_table" if node.is_vacuumcmd else "analyze_table"
    return ValidationResult(
        allowed=True,
        kind=kind,
        target=target,
        requires_long_timeout=True,
        notes=["runs with normal locking; concurrent reads and writes continue"],
    )


def _validate_alter_table(node: ast.AlterTableStmt) -> ValidationResult:
    if node.objtype not in (ObjectType.OBJECT_TABLE, None):
        return ValidationResult.reject("only ALTER TABLE is allowed")
    cmds = list(node.cmds or [])
    if len(cmds) != 1:
        return ValidationResult.reject("exactly one ALTER TABLE action is allowed")
    cmd = cmds[0]
    if cmd.subtype != AlterTableType.AT_SetStatistics:
        return ValidationResult.reject(
            "the only permitted ALTER TABLE action is SET STATISTICS; it changes "
            "planner sampling only and touches no data"
        )
    return ValidationResult(
        allowed=True,
        kind="set_statistics",
        target=_relname(node.relation),
        requires_long_timeout=False,
        notes=["takes a brief lock; run ANALYZE afterwards for it to take effect"],
    )


# ---------------------------------------------------------------------------
# EXPLAIN gating
# ---------------------------------------------------------------------------


def validate_explainable(statement: str, *, analyze: bool) -> ValidationResult:
    """Decide whether a statement may be EXPLAINed, and with ANALYZE or not.

    This exists because of one easily-missed fact: ``EXPLAIN ANALYZE`` *runs*
    the statement. ``EXPLAIN ANALYZE DELETE FROM orders`` deletes the orders.
    Plain ``EXPLAIN`` only plans, so it is safe for any statement, but ANALYZE
    is restricted to a bare SELECT.

    A CTE can also hide a write: ``WITH d AS (DELETE ... RETURNING *) SELECT``
    parses as a SelectStmt. The whole tree is therefore searched for write
    nodes rather than only the top-level statement being typed.
    """
    if not isinstance(statement, str) or not statement.strip():
        return ValidationResult.reject("empty statement")
    if "\x00" in statement:
        return ValidationResult.reject("statement contains a NUL byte")

    try:
        parsed = parse_sql(statement)
    except Exception as exc:
        return ValidationResult.reject(f"could not parse statement: {exc}")

    stmts = list(parsed or [])
    if len(stmts) != 1:
        return ValidationResult.reject(
            f"EXPLAIN takes exactly one statement, found {len(stmts)}"
        )

    node = stmts[0].stmt

    if isinstance(node, ast.ExplainStmt):
        return ValidationResult.reject(
            "do not pass EXPLAIN yourself; submit the bare statement and choose "
            "ANALYZE with the toggle"
        )

    if not analyze:
        # Planning alone executes nothing, so any statement type is fine.
        return ValidationResult(allowed=True, kind="explain", target="")

    if not isinstance(node, ast.SelectStmt):
        return ValidationResult.reject(
            f"EXPLAIN ANALYZE executes the statement, so it is restricted to SELECT. "
            f"This is a {type(node).__name__.replace('Stmt', '').upper()}. "
            f"Run it without ANALYZE to see the plan without executing it."
        )

    hidden = _find_write_nodes(node)
    if hidden:
        return ValidationResult.reject(
            f"this SELECT contains a data-modifying CTE ({', '.join(sorted(hidden))}), "
            f"which EXPLAIN ANALYZE would execute. Run it without ANALYZE."
        )

    return ValidationResult(
        allowed=True,
        kind="explain_analyze",
        target="",
        notes=["the statement is executed in a read-only transaction and rolled back"],
    )


def _find_write_nodes(node: object, seen: set[int] | None = None) -> set[str]:
    """Walk the parse tree looking for anything that writes."""
    found: set[str] = set()
    seen = seen if seen is not None else set()
    if id(node) in seen:
        return found
    seen.add(id(node))

    if isinstance(node, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt)):
        found.add(type(node).__name__.replace("Stmt", "").upper())
    if hasattr(ast, "MergeStmt") and isinstance(node, ast.MergeStmt):
        found.add("MERGE")
    if isinstance(node, ast.CreateTableAsStmt):
        found.add("CREATE TABLE AS")

    if isinstance(node, (list, tuple)):
        for item in node:
            found |= _find_write_nodes(item, seen)
        return found

    attrs = getattr(node, "__slots__", None) or getattr(node, "attribute_names", None)
    if attrs:
        for name in attrs:
            try:
                child = getattr(node, name)
            except AttributeError:
                continue
            if child is None or isinstance(child, (str, int, float, bool, bytes)):
                continue
            found |= _find_write_nodes(child, seen)
    return found


# ---------------------------------------------------------------------------
# Renderers
#
# Structured input in, SQL text out. Model output is parsed into the structured
# form first and never concatenated into a statement directly.
# ---------------------------------------------------------------------------


def render_create_index(
    *,
    schema: str,
    table: str,
    columns: list[str],
    index_name: str | None = None,
    method: str = "btree",
    include: list[str] | None = None,
    where: str | None = None,
) -> str:
    method = method.lower()
    if method not in _ALLOWED_AM:
        raise UnsafeIdentifier(f"access method {method!r} is not allowed")
    if not columns:
        raise UnsafeIdentifier("an index needs at least one column")

    cols = ", ".join(quote_ident(c) for c in columns)
    name = index_name or f"idx_{table}_{'_'.join(columns)}"
    name = safe_index_name(name)

    sql = (
        f"CREATE INDEX CONCURRENTLY {quote_ident(name)} "
        f"ON {qualify(schema, table)} USING {method} ({cols})"
    )
    if include:
        sql += " INCLUDE (" + ", ".join(quote_ident(c) for c in include) + ")"
    if where:
        # A predicate is an expression, not an identifier, so it cannot be
        # quoted safely. It is appended and the whole statement is then re-parsed
        # by validate(), which is what actually makes this safe.
        sql += f" WHERE {where}"
    return sql


def render_drop_index(*, schema: str, index_name: str) -> str:
    return f"DROP INDEX CONCURRENTLY {qualify(schema, index_name)}"


def render_analyze(*, schema: str, table: str, columns: list[str] | None = None) -> str:
    target = qualify(schema, table)
    if columns:
        target += " (" + ", ".join(quote_ident(c) for c in columns) + ")"
    return f"ANALYZE {target}"


def render_vacuum(*, schema: str, table: str, analyze: bool = True) -> str:
    opts = "(VERBOSE off, ANALYZE on)" if analyze else "(VERBOSE off)"
    return f"VACUUM {opts} {qualify(schema, table)}"


def render_set_statistics(*, schema: str, table: str, column: str, target: int) -> str:
    if not 0 <= target <= 10_000:
        raise UnsafeIdentifier("statistics target must be between 0 and 10000")
    return (
        f"ALTER TABLE {qualify(schema, table)} "
        f"ALTER COLUMN {quote_ident(column)} SET STATISTICS {int(target)}"
    )
