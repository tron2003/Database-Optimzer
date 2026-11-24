/**
 * Plain-English explanations for the jargon the dashboard uses.
 *
 * Written for someone who runs the application on top of a database but has
 * never read a query plan. Each entry says what the thing is, then what a
 * good or bad value means for them.
 */
export interface Term {
  title: string;
  body: string;
}

export const GLOSSARY: Record<string, Term> = {
  tps: {
    title: "Transactions per second",
    body: "How much work the database is doing right now. A sudden drop usually means the application stopped sending work; a sudden spike can mean a runaway job.",
  },
  cache_hit: {
    title: "Cache hit ratio",
    body: "How often a read is served from memory instead of disk. Memory is far faster. Above 99% is healthy for most workloads; a falling value means the database is reading from disk more.",
  },
  connections: {
    title: "Connections",
    body: "Open sessions between your applications and the database. Every database has a limit. Running close to it makes new connections fail.",
  },
  idle_in_tx: {
    title: "Idle in transaction",
    body: "A session that started a transaction and then stopped doing anything. It can hold locks and stop cleanup from reclaiming space, so a few for a moment is fine but long-lived ones are a problem.",
  },
  blocked: {
    title: "Blocked sessions",
    body: "Sessions waiting for a lock another session holds. A few for a split second is normal. If the number stays above zero, something is stuck.",
  },
  longest_txn: {
    title: "Longest transaction",
    body: "How long the oldest open transaction has been running. Very long transactions stop the database from cleaning up old row versions.",
  },
  seq_scan: {
    title: "Sequential scan",
    body: "The database read every row in a table to answer one query. Fine for small tables, slow for big ones. An index usually fixes it.",
  },
  index: {
    title: "Index",
    body: "A lookup structure that lets the database find rows without reading the whole table, like the index of a book. Indexes speed up reads and slow down writes a little.",
  },
  fk_index: {
    title: "Unindexed foreign key",
    body: "A column that points at another table but has no index. Joins and deletes on it have to scan the whole table, so these are the most common cause of avoidable slowness.",
  },
  explain: {
    title: "EXPLAIN",
    body: "Shows how the database plans to run a query, without running it. EXPLAIN ANALYZE actually runs the query and measures it, so it is only allowed on plain SELECTs.",
  },
  bloat: {
    title: "Bloat and dead rows",
    body: "Updates and deletes leave old row versions behind until a cleanup (vacuum) removes them. Too many waste disk space and slow scans.",
  },
  analyze: {
    title: "Planner statistics",
    body: "Numbers the database keeps about your data so it can pick good query plans. When they are stale, it can choose a slow plan. Refreshing them is quick and safe.",
  },
  pg_stat_statements: {
    title: "pg_stat_statements",
    body: "A PostgreSQL extension that records how often each query runs and how long it takes. Without it there is no per-query data.",
  },
  mean_time: {
    title: "Mean time",
    body: "The average time one run of this statement takes. Compare it with the maximum to see whether it is consistently slow or only sometimes.",
  },
  allowlist: {
    title: "Safety allowlist",
    body: "Every change is checked against a short list of allowed statement types, such as creating or dropping an index. Anything else is refused, even if you approve it.",
  },
};
