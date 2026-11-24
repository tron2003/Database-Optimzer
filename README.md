# Database Optimizer

Real-time PostgreSQL performance analytics with LLM-assisted, approval-gated
optimization. FastAPI backend, Next.js frontend, a disposable sandbox database
that ships with deliberate performance problems so there is something to find
on first boot.

```
docker compose up -d --build        #  or:  make up
```

| | |
|---|---|
| UI | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Sandbox Postgres | `localhost:5434` — `shopdb` |
| App's own Postgres | `localhost:5433` — `dbopt_meta` |

First boot seeds roughly 650,000 rows into the sandbox (about 10–30 seconds) and
starts a workload generator against it. The Queries page stays empty for the
first minute or so until `pg_stat_statements` has accumulated something.

Nothing is published beyond `127.0.0.1`.

---

## What it does

**Live dashboard** — throughput, buffer cache hit ratio, backends by state,
lock waits, long transactions, temp-file spill. Collector polls every five
seconds and pushes deltas over SSE.

**Slow query analysis** — `pg_stat_statements` ranked four ways, including by
time accumulated *since the previous poll*, which is the only ordering that
answers "what is slow right now". `EXPLAIN` workbench with a plan tree that
shows self-time per node and flags row misestimates, selective sequential
scans, hot nested loops and disk spills.

**Index and schema advice** — unindexed foreign keys, duplicate and
prefix-redundant indexes, never-scanned indexes, bloat and stale statistics.
Each finding carries its evidence and says whether it is a catalog fact or a
statistical guess.

**Natural-language console** — ask questions in English; the agent answers by
calling read-only tools and quoting what they return.

**Approval queue** — suggestions are proposed, never applied. Approving one
writes an audit record, re-validates the statement, and executes it on a
single-use privileged connection.

---

## The safety model

This application runs against databases people care about, so the parts that
could do damage are worth describing precisely.

### Two credentials, two paths

Every connection is registered with a read-only **monitor DSN** and an optional
**apply DSN**. The collector, the analysers, the EXPLAIN workbench and the chat
console only ever see the monitor DSN. The apply DSN is loaded at the moment an
approved statement executes and the connection is closed immediately
afterwards — there is never an idle writable connection against a monitored
database. A connection registered without an apply DSN cannot execute anything,
and absence is a refusal rather than a fallback.

The monitor path is read-only three times over: the role should carry
`default_transaction_read_only = on`, the client sets it again on connect, and
`EXPLAIN` additionally runs inside `SET TRANSACTION READ ONLY`.

### Timeouts and pool caps

Every monitor session sets `statement_timeout`, `lock_timeout` and
`idle_in_transaction_session_timeout` on connect. The per-target pool is capped
at three connections by default: a monitoring tool that exhausts the connection
slots of the database it is watching has caused the incident it was installed
to prevent.

### The LLM cannot write SQL

The model returns **structured JSON** validated against a Pydantic schema —
table, columns, access method — and the backend renders the statement from fixed
templates in `app/safety/allowlist.py`. There is no path where model output
becomes a query string. Each proposal passes three independent gates:

1. **Catalog check.** The table and every column must exist in the catalog just
   read. A hallucinated `orders.customer_uuid` produces a logged rejection, not
   a broken statement.
2. **Template rendering.** Identifiers are quoted; the model's text is never
   concatenated into SQL.
3. **Re-parse.** The rendered statement goes back through the allowlist
   validator. Rendering and validating are deliberately independent, so a bug
   in the renderer is caught by the parser rather than trusted.

### The execution allowlist parses, it does not pattern-match

Statements are parsed with the real PostgreSQL grammar via `pglast` and the
resulting AST is inspected. Five shapes can ever execute — `CREATE INDEX
CONCURRENTLY`, `DROP INDEX CONCURRENTLY`, `ANALYZE`, `VACUUM` (never `FULL`),
and `ALTER TABLE … SET STATISTICS` — each further constrained. Anything
unrecognised, any parse failure, any exception during inspection is a rejection;
there is no branch that returns "allowed" by default.

A regex allowlist is defeated by comments, string literals, stacked statements
and nested CTEs. A parser is not. `tests/test_allowlist.py` covers 30-odd
hostile inputs including comment-laundered `DROP TABLE`, stacked statements, and
a column literally named `id"); DROP TABLE users; --`.

### EXPLAIN ANALYZE executes the statement

This is the easiest thing to get wrong in a tool like this, because
`EXPLAIN ANALYZE DELETE FROM orders` deletes the orders. So:

- ANALYZE is restricted to a bare `SELECT`, decided from the parse tree.
- A `SELECT` containing a data-modifying CTE
  (`WITH d AS (DELETE … RETURNING *) SELECT …`) parses as a `SelectStmt` and is
  therefore searched for write nodes and refused.
- It additionally requires `allow_explain_analyze` on the connection, off by
  default, with a confirmation dialog before it can be turned on for a
  non-sandbox database.
- It runs in a read-only transaction that is always rolled back.
- The chat console's explain tool is **always** plan-only, even on connections
  where ANALYZE is permitted elsewhere.

### Redaction

When the configured provider is a hosted API, statement text and `EXPLAIN`
predicates are normalised through libpg_query before leaving the process —
literal values replaced, predicate shape kept, since the shape is what makes a
plan interpretable. This is a mitigation, not a guarantee; the real control for
sensitive data is running a local model.

### Audit

An `Execution` row is written **before** the statement runs, so a crash
mid-build still leaves a record that it was attempted. Suggestions store their
own revert statement where one exists.

---

## Configuration

Copy `.env.example` to `.env`. Everything has a working default for the local
stack.

### LLM provider

`DBOPT_LLM_PROVIDER` is one of `ollama`, `anthropic`, `openai`, or `none`.

**`none` is fully supported.** The dashboard, slow-query analysis, index and
maintenance advice, EXPLAIN and the plan analyser all work with no model
configured — every deterministic finding in this tool is computed from catalog
data, not generated. Only the plan narrative, the query review and the console
need a provider. A misconfigured provider degrades to `none` with a logged
warning rather than failing startup.

For local inference:

```
make ollama          # starts Ollama and pulls qwen2.5-coder:14b
```

`DBOPT_OPENAI_BASE_URL` points the OpenAI adapter at any compatible endpoint —
vLLM, LM Studio, llama.cpp's server.

### Registering your own database

Create a dedicated monitor role rather than reusing an application role:

```sql
CREATE ROLE dbopt_monitor LOGIN PASSWORD '…';
GRANT pg_monitor TO dbopt_monitor;
ALTER ROLE dbopt_monitor SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE yourdb TO dbopt_monitor;
GRANT USAGE ON SCHEMA public TO dbopt_monitor;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dbopt_monitor;
```

For statement-level analysis the server needs `pg_stat_statements` in
`shared_preload_libraries` (requires a restart) and
`CREATE EXTENSION pg_stat_statements`. `pgstattuple` is optional and enables
exact bloat measurement instead of the collector's estimate.

The **Test connection** button reports what will and will not work before you
register — a missing extension, a role that is not in `pg_monitor`, a session
that can still write, a superuser DSN where a read-only role was expected.

Leave the apply DSN empty to register the database as observation-only.

---

## What the sandbox plants

`sandbox/init/02-schema.sql` contains five deliberate problems, labelled in the
file so you can check the tool's output against ground truth:

| | |
|---|---|
| Duplicate index | `customers(email)` indexed twice — once by the UNIQUE constraint, once by a redundant btree |
| Redundant prefix | `idx_orders_customer` is a left prefix of `idx_orders_customer_status` |
| Unused indexes | `idx_products_created_at`, `idx_sessions_ip` — nothing in the workload touches them |
| Missing indexes | `order_items.order_id`, `events.occurred_at`, `events.customer_id`, `products.sku`, `orders.placed_at` |
| Statistics target too low | `orders.status` is heavily skewed with `SET STATISTICS 1` |

About two thirds of the generated workload hits an unindexed predicate, so the
problems show up in `pg_stat_statements` within a minute. The headline case:
`order_items.order_id` has no index, so every order-detail lookup sequentially
scans 360,000 rows.

`make reset-stats` resets the server's statistics to re-test the heuristics from
a clean observation window.

---

## Layout

```
backend/app/
  collector/     polling loop, the pg_stat_* SQL, SSE broadcast
  analysis/      deterministic heuristics — no LLM in this package
    plans.py       EXPLAIN tree walker
    indexes.py     FK / duplicate / unused / seq-scan checks
    maintenance.py bloat and statistics freshness
    health.py      instance checks with interpretations
  safety/
    allowlist.py   pglast-based validation + statement renderers
    redact.py      literal redaction for remote providers
  llm/           provider abstraction, prompts, structured schemas
    advisor.py     the three gates between model output and executable SQL
  agent/         fixed read-only tool set for the console
  services/      explain capture, analysis runs, the write path
  api/routes/    FastAPI endpoints
frontend/src/
  app/           dashboard, queries, schema, suggestions, console, connections
  components/    plan viewer, charts, shell
sandbox/
  init/          schema with planted problems, seed data, roles and grants
  workload/      continuous traffic generator
```

---

## Development

```
make test          # backend unit tests (70, no database required)
make lint          # ruff + tsc --noEmit
make dev-backend   # uvicorn with reload
make dev-frontend  # next dev
make psql-sandbox  # psql into the sandbox
make logs          # follow backend logs
```

### Integration checks against a live database

`backend/tests/integration/` holds two scripts that run against a real
PostgreSQL instance rather than fixtures. They are not part of `make test`
because they need a live server.

```bash
# every catalog query, executed as the read-only monitor role
python backend/tests/integration/check_catalog_queries.py \
  "host=localhost port=5434 user=dbopt_monitor password=monitor dbname=shopdb"

# the full loop: detect -> propose -> validate -> execute -> verify -> revert
python backend/tests/integration/check_live.py \
  "postgresql://dbopt_monitor:monitor@localhost:5434/shopdb" \
  "postgresql://dbopt_apply:apply@localhost:5434/shopdb"
```

`check_live.py` asserts that the analysers find the problems the sandbox
plants, that every generated statement and every revert statement passes the
allowlist, that the monitor role is genuinely unable to write, and that building
the recommended index actually changes the plan from `Seq Scan` to `Index Scan`.
On the bundled sandbox that step measures a 150–290× improvement on the
order-detail query, then reverts it.

### Known issues

`npm audit` reports a moderate PostCSS advisory reachable only through
Next.js's own dependency tree. It is a build-time source-map issue, not a
runtime path, and clearing it currently requires Next 16 — a breaking upgrade
not worth taking for this. The backend has no known advisories.

---

## Design notes

A few choices that are deliberate and would otherwise look like oversights.

**The cache hit ratio check does not alarm on a "low" value.** It counts shared
buffers only; the OS page cache sits underneath it and is invisible, so a 90%
ratio on a machine with plenty of RAM can be perfectly healthy. The check says
so rather than sending people to raise `shared_buffers` as a reflex.

**Unused-index findings are reported at low severity when the observation
window is short.** `idx_scan` is cumulative since the last statistics reset. A
weekly report or a month-end job is exactly the query a two-hour window misses,
so the severity reflects the window and the rationale states it.

**The sequential-scan check emits no statement.** Knowing a table is scanned a
lot does not tell you which column wants an index — that needs the query text.
Emitting a confident `CREATE INDEX` there would be guessing, so it points at
the slow-query list instead.

**Dead-tuple counts are labelled as estimates.** They come from the statistics
collector and are directionally right, numerically approximate. Exact figures
are claimed only where `pgstattuple` has measured the relation.

**Plan self-time excludes children.** `Actual Total Time` is per-loop and
inclusive of children, which is the single most common way plans get misread.
The viewer multiplies by loops and subtracts the children, so the bar widths
show where time is actually spent.

**Suggestions that resolve to the same statement are merged, not overwritten.**
A duplicate index is usually also an unused one. Both checks emit the same
`DROP`, so both share a fingerprint; the stronger catalog-derived finding
survives and the weaker statistical one is appended, because someone deciding
whether to drop an index wants both reasons.
