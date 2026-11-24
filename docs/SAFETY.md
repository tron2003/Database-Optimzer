# Safety model

A reviewer's reference. The README summarises this; here each control is named
with the file that implements it and the failure it is meant to prevent.

The threat this design takes seriously is not a malicious user. It is the
ordinary case: someone connects this tool to a production database, a model
proposes something plausible and wrong, and a tired operator clicks approve.
Every control below assumes the operator will eventually trust the tool more
than they should.

---

## 1. Credential separation

**`app/db/target.py`**

| | Monitor | Apply |
|---|---|---|
| Used by | collector, analysers, EXPLAIN, chat | approved executions only |
| Pooled | yes, capped at `target_pool_max` (default 3) | never — single-use, closed after one statement |
| Read-only | role + client + transaction | no |
| Required | yes | no; absent means the connection cannot execute |

A connection registered without an apply DSN raises `ApplyNotConfigured`. There
is no code path that falls back to the monitor credential for a write — the
monitor pool and the apply connection are constructed from different DSN fields
and the apply path checks `self._apply_dsn is None` before doing anything else.

**Why not one credential.** A single credential with write access means every
read in the system runs with the privilege to drop a table. Separating them
means a bug in the collector, the analysers, the prompt layer or the chat agent
cannot write, regardless of what it tries to do.

---

## 2. Read-only enforcement, three layers

1. **Role.** The documented setup sets `default_transaction_read_only = on` on
   the monitor role. This is the only layer that holds if the application is
   compromised.
2. **Session.** `_configure_monitor_session` re-issues
   `SET default_transaction_read_only = on` on every pool connect, covering
   targets where the operator could not alter the role.
3. **Transaction.** `capture_plan` wraps `EXPLAIN` in
   `SET TRANSACTION READ ONLY` and always rolls back.

`tests/integration/check_live.py` asserts layer 1 empirically by attempting a
`CREATE TABLE` as the monitor role and requiring it to fail.

---

## 3. Resource guardrails

**`app/config.py` → `session_setup_sql()`**

Applied on every monitor connect:

- `statement_timeout` (5s default) — a pathological plan fails one statement
- `lock_timeout` (2s default) — never queue behind a long transaction
- `idle_in_transaction_session_timeout` (10s default) — never hold back the
  vacuum horizon
- `application_name = 'dbopt-monitor'` — so the DBA can see what this is

The pool cap is the control most often missing from tools in this category. A
monitoring agent that opens connections proportional to load exhausts
`max_connections` during exactly the incident it was installed to observe, and
then the DBA cannot connect either.

The apply path gets its own budget: `lock_timeout` still applies, but
`statement_timeout` is lifted for index builds, which legitimately run for a
long time on a large table.

---

## 4. The model cannot emit SQL

**`app/llm/schemas.py`, `app/llm/advisor.py`, `app/safety/allowlist.py`**

The model returns `IndexProposal` — `{schema_name, table, columns, method,
include, reason, confidence}` — validated by Pydantic. It never returns a
statement.

```
model JSON
   → Pydantic validation            (schemas.py)
   → catalog existence check        (advisor.py, gate 1)
   → render from fixed template     (allowlist.py, gate 2)
   → re-parse the rendered text     (allowlist.py, gate 3)
   → persisted as a proposal
   → human approval
   → re-validate at execution time  (execution.py)
   → execute
```

Gate 3 exists because gates 2 and 3 must be able to disagree. If the renderer
has a quoting bug, the parser catches it. Collapsing them into one step would
mean trusting the renderer, which is the component most likely to be wrong.

`rewrite_sql` on `QueryReview` is the one field carrying model-authored SQL. It
is displayed to the user and never executed; there is no endpoint that takes it.

**Known limitation.** A partial-index `WHERE` predicate is an expression, not an
identifier, so it cannot be quoted safely. `render_create_index` appends it and
relies entirely on gate 3 to reject anything that does not parse as a single
`IndexStmt`. That is sound for the stacked-statement case but it is a narrower
margin than the rest of the pipeline, which is why the renderer is never called
with a model-supplied predicate today.

---

## 5. The allowlist

**`app/safety/allowlist.py` → `validate()`**

Parses with `pglast` (libpg_query — PostgreSQL's own grammar) and inspects the
AST. Permitted, each with further constraints:

| Statement | Constraints |
|---|---|
| `CREATE INDEX` | must be `CONCURRENTLY`; not `UNIQUE`; not constraint-backing; access method on a known list; no `TABLESPACE`; must be named |
| `DROP INDEX` | must be `CONCURRENTLY`; no `CASCADE`; exactly one index |
| `ANALYZE` | must name a table — no database-wide run |
| `VACUUM` | must name a table; never `FULL`; never `FREEZE` |
| `ALTER TABLE` | exactly one action, and that action `SET STATISTICS` |

Everything else is refused, including every parse failure and every exception
raised during inspection. `_validate_node` ends in an unconditional rejection
rather than a permissive default.

**Why `UNIQUE` is refused.** A unique index build fails on existing duplicate
data. That failure is a data-integrity question for a human, not a performance
action to automate.

**Why `CONCURRENTLY` is mandatory.** A plain `CREATE INDEX` takes an
`ACCESS EXCLUSIVE` lock for the whole build, blocking writes. On a large
production table that is an outage.

**Why `VACUUM FULL` is refused outright.** It rewrites the table under
`ACCESS EXCLUSIVE`. It is the correct answer occasionally and a catastrophe
usually, and it is not a decision this tool should be able to make.

### What the parser buys over a keyword blocklist

`tests/test_allowlist.py` makes the distinction concrete in both directions:

- Refused: `ANALYZE "public"."orders"; DROP TABLE orders` — stacked statements
  parse to two nodes.
- Refused: `/* CREATE INDEX CONCURRENTLY */ DROP TABLE orders` — the comment is
  discarded and only the `DROP` remains.
- **Allowed:** `CREATE INDEX CONCURRENTLY "i" ON "t" ("c") /* DROP TABLE orders */`
  — a comment is not a statement. A keyword blocklist would refuse this safe
  statement while still being defeated by the first case.
- Allowed, and provably one statement: a column literally named
  `id"); DROP TABLE users; --`. The quote is doubled, the payload stays inside
  one identifier, and the test asserts on the parse tree rather than counting
  semicolons in the string.

---

## 6. EXPLAIN gating

**`app/safety/allowlist.py` → `validate_explainable()`**

`EXPLAIN ANALYZE` runs the statement. This is the control most easily missed,
because the API shape invites treating "explain" as read-only.

- Without ANALYZE: any statement type. Planning executes nothing.
- With ANALYZE: bare `SELECT` only, decided from the node type.
- With ANALYZE: the whole tree is walked for `InsertStmt`, `UpdateStmt`,
  `DeleteStmt`, `MergeStmt` and `CreateTableAsStmt`, because
  `WITH d AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM d` is a
  `SelectStmt` at the top level and would otherwise pass.
- Plus `allow_explain_analyze` on the connection — off by default, confirmation
  dialog before enabling on a non-sandbox database.
- Plus a read-only transaction that is always rolled back.
- The chat tool `explain_query` passes `analyze=False, allow_analyze=False`
  unconditionally. A model should not be able to cause execution by asking for
  timings.

---

## 7. Audit

**`app/services/execution.py`**

The `Execution` row is inserted and committed **before** the statement runs. A
process killed during a 40-minute index build still leaves evidence of what was
attempted, by whom, and when. The row is then updated with duration, outcome and
error.

Re-validation happens at execution time, not only at proposal time. The gap
between proposing and approving may be days, during which the row could have
been modified. Validation is microseconds; skipping it buys nothing.

Suggestions carry `revert_statement` where one exists. For a `DROP INDEX` it is
captured from `pg_get_indexdef` **at analysis time**, while the index still
exists — after the drop, the definition is gone and cannot be reconstructed.

Failure messages are translated rather than passed through raw: a
`LockNotAvailableError` explains lock contention, and a failed
`CREATE INDEX CONCURRENTLY` explains that an INVALID index is now present and
must be dropped. That last one matters because an invalid index is invisible in
normal use — the planner ignores it while every write still maintains it — so
the Schema page surfaces them explicitly.

---

## 8. Redaction

**`app/safety/redact.py`**

Active only when `provider_is_remote()` — a local Ollama does not need it.

`redact_sql` normalises through libpg_query's own normaliser (the same one
`pg_stat_statements` uses), then applies a lexical pass over the result,
because the normaliser leaves string contents alone in some expressions.
Comments are stripped first; they routinely carry ticket numbers and names.

`redact_plan` walks the `EXPLAIN` tree and scrubs the literal-bearing fields —
`Filter`, `Index Cond`, `Hash Cond`, `Output` and friends — while keeping the
predicate *shape*. `email = '?'` is interpretable and carries nothing;
dropping the field entirely would make the plan unreadable.

**This is a mitigation, not a guarantee.** Table and column names still leave
the machine, and they are frequently sensitive on their own. For data that must
not leave, run a local model.

---

## 9. The chat agent

**`app/agent/tools.py`**

Nine tools, each wrapping one fixed, pre-written, read-only query. Arguments are
coerced and bounded — a request for 1,000,000 rows returns 200.

There is deliberately **no `run_sql` tool**. A general SQL tool would mean the
model composes a statement that reaches the database, which is the single
property this architecture exists to deny. The `/chat/tools` endpoint exposes
the tool list so a user can verify that for themselves rather than taking it on
trust.

The loop is bounded at six tool calls (`app/agent/chat.py`); on the last step
the model is told to answer with what it has. The bound matters because the loop
runs against a live database, and an agent that decides to enumerate every table
one call at a time should hit a wall rather than a rate limit.

---

## What is not protected

Stated plainly, because a safety document that implies completeness is worse
than none.

- **No authentication or authorization.** The API is unauthenticated and binds
  to localhost. Anyone who can reach port 8000 can approve an execution. Putting
  this on a shared host requires an auth layer in front; the `approved_by` field
  is a label typed by the user, not an identity.
- **DSNs are stored in plaintext** in the meta database, passwords included.
  They are redacted in API responses and logs, but anyone with read access to
  `dbopt_meta` has the credentials. Encrypting them at rest needs a key
  management story this version does not have.
- **No rate limiting** on any endpoint, including the LLM-backed ones.
- **No CSRF protection.** CORS is restricted to the configured origin, which is
  not a substitute.
- **The `pg_stat_statements` text is trusted as text.** It is normalised by the
  server and only ever displayed or sent to a model, never executed — but it
  originates from arbitrary application queries.
- **An approved statement can still be wrong.** Every control here establishes
  that the statement is one of five safe shapes and targets a relation that
  exists. None of them establish that dropping that index is a good idea. That
  judgement is the human's, which is the reason the approval step exists at all.
