"""Catalog and statistics SQL.

Every statement here is read-only and targets PostgreSQL 13 or newer
(``pg_stat_statements`` split ``total_time`` into ``total_exec_time`` and
``total_plan_time`` in 13). Each one is also written to be cheap: the
collector runs them every few seconds against a database whose performance is
the thing under investigation, so none of them may themselves be a problem.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Instance-wide counters
# ---------------------------------------------------------------------------

DATABASE_STATS = """
SELECT
    s.xact_commit,
    s.xact_rollback,
    s.blks_read,
    s.blks_hit,
    s.tup_returned,
    s.tup_fetched,
    s.tup_inserted,
    s.tup_updated,
    s.tup_deleted,
    s.conflicts,
    s.deadlocks,
    s.temp_files,
    s.temp_bytes,
    s.stats_reset,
    pg_database_size(d.datname) AS database_size_bytes
FROM pg_stat_database s
JOIN pg_database d ON d.oid = s.datid
WHERE d.datname = current_database()
"""

ACTIVITY_STATS = """
SELECT
    count(*)                                                       AS backends_total,
    count(*) FILTER (WHERE state = 'active')                       AS backends_active,
    count(*) FILTER (WHERE state = 'idle')                         AS backends_idle,
    count(*) FILTER (WHERE state = 'idle in transaction')          AS backends_idle_in_tx,
    count(*) FILTER (WHERE state = 'active'
                       AND wait_event_type IS NOT NULL)            AS backends_waiting,
    count(*) FILTER (WHERE cardinality(pg_blocking_pids(pid)) > 0) AS blocked_sessions,
    coalesce(max(extract(epoch FROM (now() - xact_start))), 0)     AS longest_xact_seconds
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
"""

MAX_CONNECTIONS = "SELECT setting::int AS max_connections FROM pg_settings WHERE name = 'max_connections'"

SERVER_VERSION = "SELECT current_setting('server_version') AS version"

INSTALLED_EXTENSIONS = "SELECT extname, extversion FROM pg_extension"


# ---------------------------------------------------------------------------
# Statement statistics
# ---------------------------------------------------------------------------

# Our own monitoring statements are excluded so the tool never reports itself
# as the slowest thing on the system.
TOP_STATEMENTS = """
SELECT
    s.queryid,
    s.query,
    s.calls,
    s.total_exec_time  AS total_exec_ms,
    s.mean_exec_time   AS mean_exec_ms,
    s.max_exec_time    AS max_exec_ms,
    s.stddev_exec_time AS stddev_exec_ms,
    s.rows,
    s.shared_blks_hit,
    s.shared_blks_read,
    s.shared_blks_dirtied,
    s.shared_blks_written,
    s.temp_blks_read,
    s.temp_blks_written,
    s.wal_bytes
FROM pg_stat_statements s
JOIN pg_database d ON d.oid = s.dbid
WHERE d.datname = current_database()
  AND s.queryid IS NOT NULL
  AND s.query NOT ILIKE '%pg_stat_statements%'
  AND s.query NOT ILIKE '%pg_stat_activity%'
  AND s.query NOT ILIKE '%pg_stat_user_indexes%'
ORDER BY s.total_exec_time DESC
LIMIT $1
"""

STATEMENT_BY_ID = """
SELECT s.queryid, s.query, s.calls, s.total_exec_time AS total_exec_ms,
       s.mean_exec_time AS mean_exec_ms, s.rows
FROM pg_stat_statements s
JOIN pg_database d ON d.oid = s.dbid
WHERE d.datname = current_database() AND s.queryid = $1
LIMIT 1
"""


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

BLOCKING_TREE = """
SELECT
    blocked.pid                                              AS blocked_pid,
    blocked.usename                                          AS blocked_user,
    left(blocked.query, 500)                                 AS blocked_query,
    blocked.wait_event_type,
    blocked.wait_event,
    extract(epoch FROM (now() - blocked.query_start))        AS blocked_seconds,
    blocking.pid                                             AS blocking_pid,
    blocking.usename                                         AS blocking_user,
    blocking.state                                           AS blocking_state,
    left(blocking.query, 500)                                AS blocking_query
FROM pg_stat_activity blocked
JOIN LATERAL unnest(pg_blocking_pids(blocked.pid)) AS bp(pid) ON true
JOIN pg_stat_activity blocking ON blocking.pid = bp.pid
WHERE blocked.datname = current_database()
ORDER BY blocked_seconds DESC NULLS LAST
LIMIT 50
"""

LONG_RUNNING = """
SELECT
    pid,
    usename,
    state,
    wait_event_type,
    wait_event,
    extract(epoch FROM (now() - xact_start))   AS xact_seconds,
    extract(epoch FROM (now() - query_start))  AS query_seconds,
    left(query, 1000)                          AS query
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state <> 'idle'
  AND query_start IS NOT NULL
  AND now() - query_start > make_interval(secs => $1)
ORDER BY query_start ASC
LIMIT 50
"""


# ---------------------------------------------------------------------------
# Tables and indexes
# ---------------------------------------------------------------------------

TABLE_STATS = """
SELECT
    s.schemaname,
    s.relname                            AS table_name,
    s.seq_scan,
    s.seq_tup_read,
    s.idx_scan,
    s.idx_tup_fetch,
    s.n_tup_ins,
    s.n_tup_upd,
    s.n_tup_del,
    s.n_tup_hot_upd,
    s.n_live_tup,
    s.n_dead_tup,
    s.n_mod_since_analyze,
    s.last_vacuum,
    s.last_autovacuum,
    s.last_analyze,
    s.last_autoanalyze,
    s.vacuum_count,
    s.autovacuum_count,
    pg_total_relation_size(s.relid)      AS total_bytes,
    pg_relation_size(s.relid)            AS heap_bytes,
    pg_indexes_size(s.relid)             AS index_bytes,
    c.reltuples::bigint                  AS planner_row_estimate
FROM pg_stat_user_tables s
JOIN pg_class c ON c.oid = s.relid
ORDER BY pg_total_relation_size(s.relid) DESC
LIMIT $1
"""

INDEX_STATS = """
SELECT
    s.schemaname,
    s.relname                                AS table_name,
    s.indexrelname                           AS index_name,
    s.idx_scan,
    s.idx_tup_read,
    s.idx_tup_fetch,
    pg_relation_size(s.indexrelid)           AS index_bytes,
    i.indisunique,
    i.indisprimary,
    i.indisvalid,
    i.indnatts,
    i.indnkeyatts,
    pg_get_indexdef(s.indexrelid)            AS indexdef,
    am.amname                                AS method,
    (i.indpred IS NOT NULL)                  AS is_partial,
    (
        SELECT array_agg(att.attname ORDER BY k.ord)
        FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att
          ON att.attrelid = i.indrelid AND att.attnum = k.attnum
        WHERE k.attnum <> 0 AND k.ord <= i.indnkeyatts
    )                                        AS key_columns,
    pg_stat_get_live_tuples(i.indrelid)      AS table_live_tuples
FROM pg_stat_user_indexes s
JOIN pg_index i  ON i.indexrelid = s.indexrelid
JOIN pg_class c  ON c.oid = s.indexrelid
JOIN pg_am    am ON am.oid = c.relam
ORDER BY pg_relation_size(s.indexrelid) DESC
"""

# Columns that appear in a foreign key but have no index leading with them.
# A missing FK index is the single most common cause of a surprise sequential
# scan, and unlike the pg_stat_statements heuristics this one is exact.
UNINDEXED_FOREIGN_KEYS = """
SELECT
    con.conname                         AS constraint_name,
    src_ns.nspname                      AS schemaname,
    src.relname                         AS table_name,
    (
        SELECT array_agg(att.attname ORDER BY k.ord)
        FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = k.attnum
    )                                   AS columns,
    tgt.relname                         AS references_table,
    pg_total_relation_size(con.conrelid) AS table_bytes
FROM pg_constraint con
JOIN pg_class src        ON src.oid = con.conrelid
JOIN pg_namespace src_ns ON src_ns.oid = src.relnamespace
JOIN pg_class tgt        ON tgt.oid = con.confrelid
WHERE con.contype = 'f'
  AND src_ns.nspname NOT IN ('pg_catalog', 'information_schema')
  AND NOT EXISTS (
      -- An index serves the FK only if the constraint's columns are a
      -- leading prefix of the index key, in order. int2vector subscripts are
      -- zero-based, so the comparison is done through WITH ORDINALITY (which
      -- always counts from 1) rather than by slicing the vector.
      SELECT 1
      FROM pg_index i
      WHERE i.indrelid = con.conrelid
        AND i.indisvalid
        AND (
            SELECT array_agg(k.attnum ORDER BY k.ord)
            FROM unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, ord)
            WHERE k.ord <= cardinality(con.conkey)
        ) = con.conkey
  )
ORDER BY pg_total_relation_size(con.conrelid) DESC
"""

# Exact bloat, only where pgstattuple is installed. Expensive: it reads the
# whole relation, so it is never part of the polling loop.
EXACT_BLOAT = """
SELECT
    $1::text                AS schemaname,
    $2::text                AS table_name,
    (s).table_len           AS table_bytes,
    (s).tuple_count         AS live_tuples,
    (s).tuple_len           AS live_bytes,
    (s).dead_tuple_count    AS dead_tuples,
    (s).dead_tuple_len      AS dead_bytes,
    (s).free_space          AS free_bytes,
    (s).free_percent        AS free_percent
FROM (
    SELECT pgstattuple(format('%I.%I', $1::text, $2::text)) AS s
) q
"""

SCHEMA_OVERVIEW = """
SELECT
    c.relname                                      AS table_name,
    n.nspname                                      AS schemaname,
    c.reltuples::bigint                            AS estimated_rows,
    pg_total_relation_size(c.oid)                  AS total_bytes,
    (
        SELECT jsonb_agg(jsonb_build_object(
                   'name', a.attname,
                   'type', format_type(a.atttypid, a.atttypmod),
                   'not_null', a.attnotnull
               ) ORDER BY a.attnum)
        FROM pg_attribute a
        WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    )                                              AS columns,
    (
        SELECT jsonb_agg(pg_get_indexdef(i.indexrelid))
        FROM pg_index i WHERE i.indrelid = c.oid
    )                                              AS indexes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT $1
"""

# Settings worth surfacing next to the metrics they explain.
NOTABLE_SETTINGS = """
SELECT name, setting, unit, source, boot_val, short_desc
FROM pg_settings
WHERE name = ANY($1::text[])
ORDER BY name
"""

NOTABLE_SETTING_NAMES = [
    "autovacuum",
    "autovacuum_vacuum_scale_factor",
    "autovacuum_analyze_scale_factor",
    "checkpoint_completion_target",
    "default_statistics_target",
    "effective_cache_size",
    "effective_io_concurrency",
    "maintenance_work_mem",
    "max_connections",
    "max_parallel_workers_per_gather",
    "max_wal_size",
    "random_page_cost",
    "seq_page_cost",
    "shared_buffers",
    "track_io_timing",
    "wal_buffers",
    "work_mem",
]
