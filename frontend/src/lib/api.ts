import type {
  AnalysisRun,
  AppConfig,
  ChatResponse,
  Connection,
  ExecutionResult,
  ExecutionRow,
  ExplainResponse,
  Health,
  IndexRow,
  MetricsSeries,
  ProbeResult,
  SlowQuery,
  Suggestion,
  TableRow,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (cause) {
    // A network-level failure here almost always means the API container is
    // not up, so say that rather than surfacing "Failed to fetch".
    throw new ApiError(
      `Cannot reach the API at ${API_BASE}. Is the backend running?`,
      0,
      cause,
    );
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body = text ? safeJson(text) : undefined;

  if (!response.ok) {
    const detail =
      (body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : undefined) ?? response.statusText;
    throw new ApiError(detail, response.status, body);
  }

  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
};

export const api = {
  config: () => request<AppConfig>("/config"),

  connections: {
    list: () => request<Connection[]>("/connections"),
    get: (id: string) => request<Connection>(`/connections/${id}`),
    probe: (dsn: string) =>
      request<ProbeResult>("/connections/probe", {
        method: "POST",
        body: JSON.stringify({ dsn }),
      }),
    create: (payload: {
      name: string;
      dsn: string;
      apply_dsn?: string | null;
      allow_explain_analyze?: boolean;
    }) =>
      request<Connection>("/connections", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    update: (
      id: string,
      payload: Partial<{
        name: string;
        enabled: boolean;
        allow_explain_analyze: boolean;
        apply_dsn: string;
      }>,
    ) =>
      request<Connection>(`/connections/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    remove: (id: string) =>
      request<void>(`/connections/${id}`, { method: "DELETE" }),
  },

  metrics: {
    series: (id: string, minutes = 30) =>
      request<MetricsSeries>(`/connections/${id}/metrics/series${qs({ minutes })}`),
    health: (id: string) => request<Health>(`/connections/${id}/health`),
    blocking: (id: string) =>
      request<{ blocking: Record<string, unknown>[] }>(
        `/connections/${id}/activity/blocking`,
      ),
    longRunning: (id: string, seconds = 30) =>
      request<{ threshold_seconds: number; sessions: Record<string, unknown>[] }>(
        `/connections/${id}/activity/long-running${qs({ seconds })}`,
      ),
    settings: (id: string) =>
      request<{ settings: Record<string, unknown>[] }>(`/connections/${id}/settings`),
  },

  queries: {
    list: (id: string, order: "total" | "mean" | "calls" | "recent" = "total", limit = 25) =>
      request<SlowQuery[]>(`/connections/${id}/queries${qs({ order, limit })}`),
    explain: (
      id: string,
      payload: { sql: string; analyze: boolean; interpret: boolean },
    ) =>
      request<ExplainResponse>(`/connections/${id}/explain`, {
        method: "POST",
        body: JSON.stringify(payload),
      }),
    review: (id: string, queryid: string) =>
      request<Record<string, unknown>>(`/connections/${id}/queries/${queryid}/review`, {
        method: "POST",
      }),
  },

  schema: {
    indexes: (id: string) => request<IndexRow[]>(`/connections/${id}/indexes`),
    invalidIndexes: (id: string) =>
      request<{ count: number; note: string; indexes: Record<string, unknown>[] }>(
        `/connections/${id}/indexes/invalid`,
      ),
    tables: (id: string) => request<TableRow[]>(`/connections/${id}/tables`),
    unindexedForeignKeys: (id: string) =>
      request<{ count: number; foreign_keys: Record<string, unknown>[] }>(
        `/connections/${id}/foreign-keys/unindexed`,
      ),
    bloat: (id: string, schema: string, table: string) =>
      request<Record<string, unknown>>(
        `/connections/${id}/tables/${schema}/${table}/bloat`,
      ),
  },

  suggestions: {
    analyze: (id: string) =>
      request<AnalysisRun>(`/connections/${id}/analyze`, { method: "POST" }),
    list: (id: string, status?: string) =>
      request<Suggestion[]>(`/connections/${id}/suggestions${qs({ status })}`),
    approve: (
      id: string,
      suggestionId: string,
      payload: { approved_by: string; note?: string; dry_run?: boolean },
    ) =>
      request<ExecutionResult>(
        `/connections/${id}/suggestions/${suggestionId}/approve`,
        { method: "POST", body: JSON.stringify(payload) },
      ),
    reject: (id: string, suggestionId: string, note?: string) =>
      request<void>(`/connections/${id}/suggestions/${suggestionId}/reject`, {
        method: "POST",
        body: JSON.stringify({ note }),
      }),
    reopen: (id: string, suggestionId: string) =>
      request<void>(`/connections/${id}/suggestions/${suggestionId}/reopen`, {
        method: "POST",
      }),
    executions: (id: string) =>
      request<ExecutionRow[]>(`/connections/${id}/executions`),
  },

  chat: {
    tools: (id: string) =>
      request<{ tools: string; note: string }>(`/connections/${id}/chat/tools`),
    ask: (id: string, question: string, threadId?: string | null) =>
      request<ChatResponse>(`/connections/${id}/chat`, {
        method: "POST",
        body: JSON.stringify({ question, thread_id: threadId ?? null }),
      }),
  },

  streamUrl: (connectionId?: string) =>
    `${API_BASE}/api/stream${connectionId ? `?connection_id=${connectionId}` : ""}`,
};
