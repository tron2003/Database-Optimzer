export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type HealthStatus = "ok" | "warn" | "critical" | "unknown";

export interface Connection {
  id: string;
  name: string;
  kind: "sandbox" | "external";
  dsn_redacted: string;
  can_apply: boolean;
  allow_explain_analyze: boolean;
  enabled: boolean;
  created_at: string;
  last_polled_at: string | null;
  last_error: string | null;
  server_version: string | null;
  extensions: Record<string, string>;
  collector_running: boolean;
}

export interface ProbeResult {
  ok: boolean;
  error?: string | null;
  server_version?: string | null;
  extensions: Record<string, string>;
  has_pg_stat_statements: boolean;
  has_pgstattuple: boolean;
  has_pg_monitor: boolean;
  is_superuser: boolean;
  session_is_writable: boolean;
  warnings: string[];
}

export interface MetricPoint {
  captured_at: string;
  tps: number;
  cache_hit_ratio: number;
  backends_active: number;
  backends_total: number;
  backends_idle_in_tx: number;
  blocked_sessions: number;
  longest_xact_seconds: number;
  database_size_bytes: number;
  temp_bytes_per_sec: number;
  rows_returned_per_sec: number;
}

export interface MetricsSeries {
  connection_id: string;
  points: MetricPoint[];
  interval_seconds: number;
}

export interface HealthCheck {
  key: string;
  label: string;
  status: HealthStatus;
  value: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface Health {
  score: number;
  status: HealthStatus;
  checks: HealthCheck[];
  captured_at?: string | null;
}

export interface SlowQuery {
  queryid: string;
  query: string;
  calls: number;
  total_exec_ms: number;
  mean_exec_ms: number;
  max_exec_ms: number;
  stddev_exec_ms: number;
  rows: number;
  shared_blks_hit: number;
  shared_blks_read: number;
  temp_blks_written: number;
  delta_calls: number;
  delta_total_exec_ms: number;
  cache_hit_ratio: number;
  captured_at?: string | null;
}

export interface PlanFinding {
  code: string;
  severity: Severity;
  node: string;
  message: string;
  detail: Record<string, unknown> & { remedy?: string; node_ms?: number };
}

export interface ExplainResponse {
  plan_id: string | null;
  analyzed: boolean;
  plan: unknown;
  findings: PlanFinding[];
  summary: {
    planning_ms?: number | null;
    execution_ms?: number | null;
    total_cost?: number | null;
    analyzed?: boolean;
    node_count?: number;
    slowest_node?: string | null;
    max_node_ms?: number;
  };
  planning_ms: number | null;
  execution_ms: number | null;
  interpretation: string | null;
  interpretation_error: string | null;
  new_suggestions: number;
  rejected_proposals: string[];
  notes: string[];
}

export interface IndexRow {
  index_name: string;
  table_name: string;
  schemaname: string;
  key_columns: string[];
  method: string | null;
  indisunique: boolean;
  indisprimary: boolean;
  indisvalid: boolean;
  is_partial: boolean;
  idx_scan: number;
  index_bytes: number;
  indexdef: string | null;
}

export interface TableRow {
  schemaname: string;
  table_name: string;
  seq_scan: number;
  seq_tup_read: number;
  idx_scan: number;
  n_live_tup: number;
  n_dead_tup: number;
  n_mod_since_analyze: number;
  total_bytes: number;
  heap_bytes: number;
  index_bytes: number;
  last_autovacuum: string | null;
  last_autoanalyze: string | null;
  dead_ratio: number;
}

export type SuggestionStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "applied"
  | "failed";

export interface Suggestion {
  id: string;
  connection_id: string;
  created_at: string;
  kind: string;
  source: "heuristic" | "llm";
  severity: Severity;
  status: SuggestionStatus;
  title: string;
  rationale: string;
  statement: string | null;
  revert_statement: string | null;
  target_relation: string | null;
  estimated_benefit: string | null;
  estimated_cost: string | null;
  evidence: Record<string, unknown>;
}

export interface ExecutionResult {
  execution_id?: string;
  succeeded?: boolean;
  duration_ms?: number;
  statement: string;
  error?: string | null;
  revert_statement?: string | null;
  dry_run?: boolean;
  allowed?: boolean;
  kind?: string;
  target?: string;
  notes?: string[];
}

export interface ExecutionRow {
  id: string;
  suggestion_id: string;
  approved_by: string;
  approved_at: string;
  note: string | null;
  statement: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  succeeded: boolean | null;
  error: string | null;
}

export interface ChatStep {
  kind: "tool_call" | "answer" | "error";
  name?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
  text?: string;
}

export interface ChatResponse {
  thread_id: string | null;
  answer: string;
  steps: ChatStep[];
  tool_calls: number;
  error?: string | null;
}

export interface AppConfig {
  poll_interval_seconds: number;
  snapshot_retention_hours: number;
  statement_timeout_ms: number;
  lock_timeout_ms: number;
  target_pool_max: number;
  redact_literals: boolean;
  llm_provider: string;
  llm_enabled: boolean;
}

export interface AnalysisRun {
  connection_id: string;
  ran_at: string;
  duration_seconds: number;
  suggestions: { created?: number; updated?: number; unchanged?: number };
  total_drafts: number;
  tables_examined: number;
  indexes_examined: number;
  stats_age_hours: number | null;
}
