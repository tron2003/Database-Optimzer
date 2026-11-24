"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useConnection } from "@/components/Providers";
import { FindingList, PlanViewer } from "@/components/PlanViewer";
import { EmptyState, ErrorNote, SectionTitle, Spinner, PageHeader } from "@/components/Shell";
import { Caveat, Pill, Segmented } from "@/components/Stat";
import { api, ApiError } from "@/lib/api";
import { compact, ms, num, oneLine, pct } from "@/lib/format";
import type { ExplainResponse, SlowQuery } from "@/lib/types";

type Order = "total" | "mean" | "calls" | "recent";

const ORDERS: { key: Order; label: string; hint: string }[] = [
  {
    key: "recent",
    label: "Recent",
    hint: "Execution time accumulated since the previous poll — what is slow right now.",
  },
  {
    key: "total",
    label: "Total time",
    hint: "Cumulative since the last statistics reset. This is what actually consumes the server.",
  },
  {
    key: "mean",
    label: "Mean time",
    hint: "Average per call. A high mean on a rarely-called query may matter less than a low mean called constantly.",
  },
  { key: "calls", label: "Calls", hint: "Most frequently executed." },
];

export default function QueriesPage() {
  const { connectionId, current } = useConnection();
  const [order, setOrder] = useState<Order>("total");
  const [selected, setSelected] = useState<SlowQuery | null>(null);

  const queries = useQuery({
    queryKey: ["queries", connectionId, order],
    queryFn: () => api.queries.list(connectionId!, order, 40),
    enabled: !!connectionId,
    refetchInterval: order === "recent" ? 10_000 : 30_000,
  });

  if (!connectionId) {
    return (
      <EmptyState
        title="No database selected"
        body="Pick a database from the selector in the header."
      />
    );
  }

  if (queries.isError) {
    const err = queries.error as ApiError;
    return (
      <ErrorNote
        message={
          err.status === 409
            ? err.message
            : `Could not load statements: ${err.message}`
        }
      />
    );
  }

  const rows = queries.data ?? [];
  const activeOrder = ORDERS.find((o) => o.key === order)!;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Queries"
        subtitle={`The statements your database spends the most time on. ${activeOrder.hint}`}
        right={
          <Segmented
            label="Sort statements by"
            value={order}
            onChange={setOrder}
            options={ORDERS.map((o) => ({ value: o.key, label: o.label }))}
          />
        }
      />

      {queries.isLoading ? (
        <Spinner label="Reading pg_stat_statements" />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No statements recorded yet"
          body="pg_stat_statements has nothing to report. If the database was just started, give it a moment of traffic."
        />
      ) : (
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-ink-850">
                <tr>
                  <th className="th w-[40%]">Statement</th>
                  <th className="th text-right">Calls</th>
                  <th className="th text-right">Total</th>
                  <th className="th text-right">Mean</th>
                  <th className="th text-right">Max</th>
                  <th className="th text-right">Stddev</th>
                  <th className="th text-right">Rows</th>
                  <th className="th text-right">Cache</th>
                  <th className="th" />
                </tr>
              </thead>
              <tbody>
                {rows.map((q) => {
                  // A modest mean with a large stddev is the signature of a
                  // query that is fine for most parameters and terrible for a
                  // few — worth flagging, because the average hides it.
                  const volatile =
                    q.stddev_exec_ms > q.mean_exec_ms * 2 && q.calls > 20;
                  return (
                    <tr
                      key={q.queryid}
                      className={`row-hover cursor-pointer ${
                        selected?.queryid === q.queryid ? "bg-ink-800" : ""
                      }`}
                      onClick={() => setSelected(q)}
                    >
                      <td className="td max-w-0">
                        <span className="block truncate font-mono text-2xs text-mist-300">
                          {oneLine(q.query, 120)}
                        </span>
                      </td>
                      <td className="td tnum text-right">{compact(q.calls)}</td>
                      <td className="td tnum text-right text-mist-100">
                        {ms(q.total_exec_ms)}
                      </td>
                      <td className="td tnum text-right">{ms(q.mean_exec_ms)}</td>
                      <td className="td tnum text-right">{ms(q.max_exec_ms)}</td>
                      <td className="td tnum text-right">
                        <span className={volatile ? "text-sev-medium" : ""}>
                          {ms(q.stddev_exec_ms)}
                        </span>
                      </td>
                      <td className="td tnum text-right">{compact(q.rows)}</td>
                      <td className="td tnum text-right">
                        <span
                          className={
                            q.cache_hit_ratio < 0.9 ? "text-sev-medium" : ""
                          }
                        >
                          {pct(q.cache_hit_ratio, 0)}
                        </span>
                      </td>
                      <td className="td text-right">
                        {volatile && <Pill tone="medium">volatile</Pill>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected && (
        <StatementDetail
          key={selected.queryid}
          query={selected}
          connectionId={connectionId}
          allowAnalyze={current?.allow_explain_analyze ?? false}
          onClose={() => setSelected(null)}
        />
      )}

      <ExplainWorkbench
        connectionId={connectionId}
        allowAnalyze={current?.allow_explain_analyze ?? false}
      />
    </div>
  );
}

function StatementDetail({
  query,
  connectionId,
  allowAnalyze,
  onClose,
}: {
  query: SlowQuery;
  connectionId: string;
  allowAnalyze: boolean;
  onClose: () => void;
}) {
  const review = useMutation({
    mutationFn: () => api.queries.review(connectionId, query.queryid),
  });

  const volatile = query.stddev_exec_ms > query.mean_exec_ms * 2 && query.calls > 20;

  return (
    <div className="card-pad space-y-4">
      <SectionTitle
        title="Selected statement"
        hint={`queryid ${query.queryid}`}
        right={
          <button type="button" onClick={onClose} className="btn-default">
            Close
          </button>
        }
      />

      <pre className="code max-h-56 overflow-auto whitespace-pre-wrap">{query.query}</pre>

      <div className="grid grid-cols-2 gap-3 text-2xs md:grid-cols-4 xl:grid-cols-7">
        {[
          ["Calls", num(query.calls)],
          ["Total", ms(query.total_exec_ms)],
          ["Mean", ms(query.mean_exec_ms)],
          ["Max", ms(query.max_exec_ms)],
          ["Stddev", ms(query.stddev_exec_ms)],
          ["Rows returned", num(query.rows)],
          ["Blocks read", num(query.shared_blks_read)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md border border-ink-800 bg-ink-850/50 p-2">
            <div className="label">{label}</div>
            <div className="tnum mt-0.5 text-xs text-mist-100">{value}</div>
          </div>
        ))}
      </div>

      {volatile && (
        <Caveat>
          The standard deviation is more than twice the mean across{" "}
          {num(query.calls)} calls. This statement is probably fast for most
          parameter values and slow for a few; the average conceals that, so
          judge it by the max rather than the mean.
        </Caveat>
      )}

      {query.temp_blks_written > 0 && (
        <Caveat>
          This statement wrote {num(query.temp_blks_written)} temporary blocks,
          meaning a sort or hash exceeded work_mem and spilled to disk.
        </Caveat>
      )}

      <Caveat>
        The text above is normalised by pg_stat_statements: <code>$1</code>,{" "}
        <code>$2</code> are placeholders and the real values varied between calls.
        To plan it, substitute representative values in the workbench below —
        {allowAnalyze
          ? " EXPLAIN ANALYZE is enabled for this connection."
          : " EXPLAIN ANALYZE is disabled for this connection, so you will get a plan without actual timings."}
      </Caveat>

      <div className="flex items-center gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={() => review.mutate()}
          disabled={review.isPending}
        >
          {review.isPending ? "Reviewing…" : "Ask the model to review this"}
        </button>
        {review.isError && (
          <span className="text-2xs text-sev-critical">
            {(review.error as Error).message}
          </span>
        )}
      </div>

      {review.data && (
        <div className="space-y-3 rounded-md border border-ink-700 bg-ink-850/50 p-3">
          <div className="flex items-center gap-2">
            <Pill tone="info">model review</Pill>
            {Boolean(review.data.insufficient_evidence) && (
              <Pill tone="medium">insufficient evidence</Pill>
            )}
            {Number(review.data.new_suggestions ?? 0) > 0 && (
              <Pill tone="low">
                {String(review.data.new_suggestions)} new suggestion(s)
              </Pill>
            )}
          </div>
          <p className="text-xs leading-relaxed text-mist-200">
            {String(review.data.summary ?? "")}
          </p>
          {Boolean(review.data.likely_cause) && (
            <p className="text-2xs leading-relaxed text-mist-400">
              {String(review.data.likely_cause)}
            </p>
          )}
          {Boolean(review.data.rewrite_sql) && (
            <div>
              <div className="label mb-1">Suggested rewrite — not executed</div>
              <pre className="code whitespace-pre-wrap">
                {String(review.data.rewrite_sql)}
              </pre>
            </div>
          )}
          {Array.isArray(review.data.rejected_proposals) &&
            review.data.rejected_proposals.length > 0 && (
              <div>
                <div className="label mb-1">
                  Rejected proposals — failed the catalog check
                </div>
                <ul className="space-y-1">
                  {(review.data.rejected_proposals as string[]).map((r, i) => (
                    <li key={i} className="text-2xs text-mist-500">
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            )}
        </div>
      )}
    </div>
  );
}

function ExplainWorkbench({
  connectionId,
  allowAnalyze,
}: {
  connectionId: string;
  allowAnalyze: boolean;
}) {
  const [sql, setSql] = useState("");
  const [analyze, setAnalyze] = useState(false);
  const [interpret, setInterpret] = useState(false);
  const [result, setResult] = useState<ExplainResponse | null>(null);

  const explain = useMutation({
    mutationFn: () =>
      api.queries.explain(connectionId, { sql, analyze, interpret }),
    onSuccess: setResult,
  });

  return (
    <div className="card-pad space-y-4">
      <SectionTitle
        title="EXPLAIN workbench"
        hint="Paste a statement to see its plan. Plan-only is always safe; ANALYZE executes the statement."
      />

      <textarea
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        rows={6}
        spellCheck={false}
        placeholder={"SELECT oi.* FROM order_items oi WHERE oi.order_id = 42;"}
        className="input resize-y font-mono text-xs"
      />

      <div className="flex flex-wrap items-center gap-4">
        <label
          className={`flex items-center gap-2 text-xs ${
            allowAnalyze ? "text-mist-300" : "cursor-not-allowed text-mist-500"
          }`}
          title={
            allowAnalyze
              ? "Runs the statement for real and reports actual timings."
              : "Disabled for this connection. Enable it on the Connections page only if executing the statement is acceptable."
          }
        >
          <input
            type="checkbox"
            checked={analyze}
            disabled={!allowAnalyze}
            onChange={(e) => setAnalyze(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-ink-600 bg-ink-850 accent-accent"
          />
          ANALYZE
          <span className="text-2xs text-mist-500">(executes the statement)</span>
        </label>

        <label className="flex items-center gap-2 text-xs text-mist-300">
          <input
            type="checkbox"
            checked={interpret}
            onChange={(e) => setInterpret(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-ink-600 bg-ink-850 accent-accent"
          />
          Also ask the model to explain it
        </label>

        <button
          type="button"
          onClick={() => explain.mutate()}
          disabled={!sql.trim() || explain.isPending}
          className="btn-primary ml-auto"
        >
          {explain.isPending ? "Planning…" : "Explain"}
        </button>
      </div>

      {analyze && (
        <Caveat>
          EXPLAIN ANALYZE runs the statement. It is restricted to SELECT, and a
          SELECT containing a data-modifying CTE is refused — but it will still
          perform every read the query performs, and it runs inside a transaction
          that is rolled back.
        </Caveat>
      )}

      {explain.isError && <ErrorNote message={(explain.error as Error).message} />}

      {result && (
        <div className="space-y-4 border-t border-ink-800 pt-4">
          <div className="flex flex-wrap items-center gap-3 text-2xs text-mist-400">
            <Pill tone={result.analyzed ? "low" : "info"}>
              {result.analyzed ? "executed" : "plan only"}
            </Pill>
            {result.planning_ms !== null && (
              <span className="tnum">planning {ms(result.planning_ms)}</span>
            )}
            {result.execution_ms !== null && (
              <span className="tnum">execution {ms(result.execution_ms)}</span>
            )}
            {result.summary?.node_count !== undefined && (
              <span className="tnum">{result.summary.node_count} nodes</span>
            )}
            {result.summary?.slowest_node && (
              <span>slowest: {result.summary.slowest_node}</span>
            )}
          </div>

          <div>
            <SectionTitle title="Findings" hint="computed from the plan, not from a model" />
            <FindingList findings={result.findings} />
          </div>

          <div>
            <SectionTitle
              title="Plan"
              hint={
                result.analyzed
                  ? "bar width shows time in that node alone, excluding children"
                  : "estimates only — run with ANALYZE for actual rows and timings"
              }
            />
            <PlanViewer plan={result.plan} analyzed={result.analyzed} />
          </div>

          {result.interpretation && (
            <div>
              <SectionTitle title="Model interpretation" />
              <div className="rounded-md border border-ink-700 bg-ink-850/50 p-3">
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-mist-200">
                  {result.interpretation}
                </p>
                {result.new_suggestions > 0 && (
                  <p className="mt-2 text-2xs text-sev-low">
                    {result.new_suggestions} suggestion(s) added to the queue for
                    approval.
                  </p>
                )}
              </div>
            </div>
          )}

          {result.interpretation_error && (
            <Caveat>
              The plan and its findings above are complete; only the model
              narrative failed: {result.interpretation_error}
            </Caveat>
          )}

          {result.rejected_proposals.length > 0 && (
            <div>
              <div className="label mb-1">
                Model proposals rejected by the catalog check
              </div>
              <ul className="space-y-1">
                {result.rejected_proposals.map((r, i) => (
                  <li key={i} className="text-2xs text-mist-500">
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
