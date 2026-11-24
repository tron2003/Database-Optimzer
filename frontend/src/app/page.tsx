"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import {
  ChartLegend,
  LiveAreaChart,
  ScoreRing,
  Sparkline,
  StackedAreaChart,
} from "@/components/Charts";
import { Icon } from "@/components/Icons";
import { Info } from "@/components/Info";
import { useConnection } from "@/components/Providers";
import { EmptyState, ErrorNote, LiveDot, SectionTitle } from "@/components/Shell";
import { Meter, Pill, Segmented, Skeleton, StatTile } from "@/components/Stat";
import { useToast } from "@/components/Toast";
import { Welcome } from "@/components/Welcome";
import { api } from "@/lib/api";
import { bytes, compact, ms, num, oneLine, pct, seconds, statusClass } from "@/lib/format";
import { useChartTheme } from "@/lib/theme";
import { useMetricsStream } from "@/lib/useStream";

interface Attention {
  id: string;
  tone: "critical" | "warn" | "info";
  title: string;
  body: string;
  href: string;
  cta: string;
}

/** Where each health check sends you when it needs attention. */
const CHECK_ACTION: Record<string, { href: string; cta: string }> = {
  cache_hit_ratio: { href: "/queries", cta: "See queries" },
  connections: { href: "/chat", cta: "Ask the console" },
  locks: { href: "#lock-waits", cta: "See lock waits" },
  idle_in_tx: { href: "/chat", cta: "Ask the console" },
  long_xact: { href: "/chat", cta: "Ask the console" },
  rollbacks: { href: "/queries", cta: "See queries" },
  deadlocks: { href: "/queries", cta: "See queries" },
  temp_files: { href: "/queries", cta: "See queries" },
};

function describeTps(now: number, avg: number): string {
  if (avg < 1) return "Very little activity";
  const change = (now - avg) / avg;
  if (change > 0.3) return "Busier than usual";
  if (change < -0.3) return "Quieter than usual";
  return "Steady";
}

export default function OverviewPage() {
  const { connectionId, current, loading, error } = useConnection();
  const [range, setRange] = useState<"5" | "15">("5");
  const t = useChartTheme();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { latest, history, status, isStale } = useMetricsStream(connectionId);

  const health = useQuery({
    queryKey: ["health", connectionId],
    queryFn: () => api.metrics.health(connectionId!),
    enabled: !!connectionId,
    refetchInterval: 15_000,
  });

  const series = useQuery({
    queryKey: ["series", connectionId],
    queryFn: () => api.metrics.series(connectionId!, 30),
    enabled: !!connectionId,
    refetchInterval: 30_000,
  });

  const blocking = useQuery({
    queryKey: ["blocking", connectionId],
    queryFn: () => api.metrics.blocking(connectionId!),
    enabled: !!connectionId,
    refetchInterval: 10_000,
  });

  const proposed = useQuery({
    queryKey: ["suggestions", connectionId, "proposed"],
    queryFn: () => api.suggestions.list(connectionId!, "proposed"),
    enabled: !!connectionId,
    refetchInterval: 30_000,
  });

  const analyze = useMutation({
    mutationFn: () => api.suggestions.analyze(connectionId!),
    onSuccess: (run) => {
      const made = (run.suggestions.created ?? 0) + (run.suggestions.updated ?? 0);
      toast.push({
        tone: "success",
        title: made ? `Analysis found ${made} suggestion${made === 1 ? "" : "s"}` : "Analysis finished",
        body: made
          ? "Review them before anything runs."
          : `Examined ${run.tables_examined} tables and ${run.indexes_examined} indexes. Nothing to fix right now.`,
      });
      queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    },
    onError: (e: Error) =>
      toast.push({ tone: "error", title: "Analysis failed", body: e.message }),
  });

  if (loading)
    return (
      <div className="space-y-5">
        <Skeleton className="h-64 w-full" />
        <div className="grid grid-cols-1 gap-5 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
        <Skeleton className="h-80 w-full" />
      </div>
    );
  if (error) return <ErrorNote message={error} />;
  if (!connectionId) {
    return (
      <EmptyState
        title="No database registered yet"
        body="Register a PostgreSQL instance to begin. The bundled sandbox registers itself automatically once its container is healthy, so if you just started the stack, give it a minute and refresh."
        action={
          <Link href="/connections" className="btn-primary">
            Add a database
          </Link>
        }
      />
    );
  }

  // Prefer the live stream; fall back to stored snapshots before the first
  // SSE frame lands, so the page is never blank on load.
  const allPoints = history.length
    ? history.map((h) => ({
        captured_at: h.at,
        tps: Number(h.rates?.tps ?? 0),
        cache_hit_ratio: Number(h.metrics?.cache_hit_ratio ?? 0) * 100,
        backends_active: Number(h.metrics?.backends_active ?? 0),
        backends_idle_in_tx: Number(h.metrics?.backends_idle_in_tx ?? 0),
        backends_total: Number(h.metrics?.backends_total ?? 0),
        rows_returned_per_sec: Number(h.rates?.tup_returned_per_sec ?? 0),
      }))
    : (series.data?.points ?? []).map((p) => ({
        captured_at: p.captured_at,
        tps: p.tps,
        cache_hit_ratio: p.cache_hit_ratio * 100,
        backends_active: p.backends_active,
        backends_idle_in_tx: p.backends_idle_in_tx,
        backends_total: p.backends_total,
        rows_returned_per_sec: p.rows_returned_per_sec,
      }));

  // Window to the chosen range, anchored on the newest point so the chart does
  // not empty out if the clock on this machine drifts.
  const newest = allPoints.length
    ? new Date(allPoints[allPoints.length - 1].captured_at).getTime()
    : 0;
  const cutoff = newest - Number(range) * 60_000;
  const windowed = allPoints.filter((p) => new Date(p.captured_at).getTime() >= cutoff);
  const points = windowed.length > 1 ? windowed : allPoints;

  const stackPoints = points.map((p) => ({
    ...p,
    backends_other: Math.max(p.backends_total - p.backends_active - p.backends_idle_in_tx, 0),
  }));

  const tpsValues = points.map((p) => p.tps);
  const tpsNow =
    latest?.rates?.tps !== undefined
      ? Number(latest.rates.tps)
      : points.length
        ? points[points.length - 1].tps
        : 0;
  const tpsAvg = tpsValues.length ? tpsValues.reduce((a, b) => a + b, 0) / tpsValues.length : 0;
  const tpsPeak = tpsValues.length ? Math.max(...tpsValues) : 0;

  const hitValues = points.map((p) => p.cache_hit_ratio);
  const hitFloor = hitValues.length ? Math.max(0, Math.floor(Math.min(...hitValues) - 2)) : 0;

  const m = latest?.metrics ?? {};
  const rates = latest?.rates ?? {};
  const h = health.data;
  const hitNow = Number(m.cache_hit_ratio ?? 0);
  const connRatio = Number(m.backends_total ?? 0) / Math.max(Number(m.max_connections ?? 1), 1);
  const connTone = connRatio > 0.8 ? "critical" : connRatio > 0.6 ? "warn" : "neutral";
  const blockedCount = blocking.data?.blocking?.length ?? 0;
  const proposedCount = proposed.data?.length ?? 0;
  const spark = (key: "cache_hit_ratio" | "tps") => points.slice(-40).map((p) => p[key]);

  // ---- what needs attention -------------------------------------------
  const attention: Attention[] = [];
  if (current?.last_error) {
    attention.push({
      id: "collector",
      tone: "critical",
      title: "The collector cannot read this database",
      body: current.last_error,
      href: "/connections",
      cta: "Check connection",
    });
  }
  for (const c of h?.checks ?? []) {
    if (c.status === "ok" || c.status === "unknown") continue;
    const action = CHECK_ACTION[c.key] ?? { href: "/queries", cta: "Investigate" };
    attention.push({
      id: `check-${c.key}`,
      tone: c.status === "critical" ? "critical" : "warn",
      title: `${c.label}: ${c.value}`,
      body: c.message,
      ...action,
    });
  }
  if (proposedCount > 0) {
    attention.push({
      id: "suggestions",
      tone: "info",
      title: `${proposedCount} suggested change${proposedCount === 1 ? "" : "s"} waiting for review`,
      body: "Nothing runs until you approve it, and each change can be undone.",
      href: "/suggestions",
      cta: "Review",
    });
  }
  if (!current?.extensions?.pg_stat_statements) {
    attention.push({
      id: "pgss",
      tone: "warn",
      title: "Per-query data is switched off",
      body: "pg_stat_statements is not installed, so the Queries page will be empty.",
      href: "/help",
      cta: "How to enable",
    });
  }
  if (isStale || status === "error") {
    attention.push({
      id: "stale",
      tone: "warn",
      title: status === "error" ? "Live stream disconnected" : "No new data for 30 seconds",
      body: "The collector may be stalled. The numbers below could be out of date.",
      href: "/connections",
      cta: "Check connection",
    });
  }

  const worst = attention.some((a) => a.tone === "critical")
    ? "critical"
    : attention.some((a) => a.tone === "warn")
      ? "warn"
      : "ok";
  const problemCount = attention.filter((a) => a.tone !== "info").length;
  const verdict =
    worst === "ok"
      ? attention.length
        ? "Healthy, with changes to review."
        : "Everything looks healthy."
      : problemCount === 1
        ? "One thing needs attention."
        : `${problemCount} things need attention.`;
  // A soft glow from the top-left corner in the status colour, rather than a flat wash:
  // it signals state without turning the whole panel beige/pink in the light theme.
  const glowVar = { ok: "--sev-ok", warn: "--sev-high", critical: "--sev-critical" }[worst];
  const heroBg = `radial-gradient(120% 140% at 0% 0%, rgb(var(${glowVar}) / 0.13) 0%, rgb(var(${glowVar}) / 0) 60%)`;
  const ringColor = { ok: t.status.ok, warn: t.status.warn, critical: t.status.critical }[worst];

  const streamTone = isStale || status === "error" ? "warn" : status === "live" ? "live" : "idle";
  const topMax = Math.max(...(latest?.top_statements ?? []).map((s) => s.delta_total_exec_ms), 1);

  return (
    <div className="space-y-5">
      <Welcome />

      {/* ---- verdict -------------------------------------------------- */}
      <section className="card relative overflow-hidden" aria-label="Health summary">
        <div className="absolute inset-0" style={{ background: heroBg }} aria-hidden />
        <div className="relative grid gap-8 p-6 sm:p-8 grid-cols-[minmax(0,1fr)] lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
          <div className="flex flex-col justify-center gap-7">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-semibold text-mist-300">{current?.name}</span>
                {current?.server_version && <Pill tone="info">PostgreSQL {current.server_version}</Pill>}
                {current?.kind === "sandbox" && <Pill tone="low">Sandbox</Pill>}
                {!current?.can_apply && <Pill tone="info">Observe only</Pill>}
              </div>
              <div className="mt-6 flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:gap-6">
                {h ? (
                  <ScoreRing value={h.score} color={ringColor} label="health" size={116} />
                ) : (
                  <Skeleton className="h-[116px] w-[116px] !rounded-full" />
                )}
                <div>
                  <h1 className="text-2xl font-semibold leading-tight text-mist-100 sm:text-3xl">{verdict}</h1>
                  <p className="mt-2 flex items-center gap-2 text-xs text-mist-400">
                    <LiveDot tone={streamTone} />
                    {status === "live" ? "Live" : status === "error" ? "Reconnecting" : "Connecting"}
                    {h && <span>· {h.checks.length} health checks</span>}
                  </p>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {proposedCount > 0 ? (
                <Link href="/suggestions" className="btn-primary h-10 px-5">
                  Review {proposedCount} suggestion{proposedCount === 1 ? "" : "s"}
                  <Icon name="arrow" className="h-4 w-4" />
                </Link>
              ) : (
                <button
                  type="button"
                  className="btn-primary h-10 px-5"
                  disabled={analyze.isPending || !current?.extensions}
                  onClick={() => analyze.mutate()}
                >
                  {analyze.isPending ? "Analyzing…" : "Find optimizations"}
                </button>
              )}
              <Link href="/help" className="btn-default h-10 px-5">
                How it works
              </Link>
            </div>
          </div>

          <div className="rounded-2xl bg-ink-900 p-2 shadow-panel">
            <h2 className="px-4 pb-1 pt-3 font-display text-sm font-semibold text-mist-100">
              {attention.length ? "What to look at" : "Nothing needs your attention"}
            </h2>
            {attention.length === 0 && (
              <p className="px-4 pb-4 text-xs leading-relaxed text-mist-400">
                Every health check passed and there are no changes waiting. Come back after your
                workload changes, or run an analysis to double-check.
              </p>
            )}
            <ul className="divide-y divide-ink-700/70">
              {attention.slice(0, 5).map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-3 sm:flex-nowrap">
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${
                      a.tone === "critical"
                        ? "bg-sev-critical/12 text-sev-critical"
                        : a.tone === "warn"
                          ? "bg-sev-medium/15 text-sev-medium"
                          : "bg-accent-soft text-accent-glow"
                    }`}
                  >
                    <Icon name={a.tone === "info" ? "suggestions" : "alert"} className="h-[18px] w-[18px]" />
                  </span>
                  <div className="min-w-0 flex-[1_1_calc(100%-3rem)] sm:flex-1">
                    <p className="text-xs font-semibold text-mist-100 sm:truncate">{a.title}</p>
                    <p className="line-clamp-2 text-2xs text-mist-400">{a.body}</p>
                  </div>
                  <Link href={a.href} className="btn-default ml-12 h-8 shrink-0 px-3.5 sm:ml-0">
                    {a.cta}
                  </Link>
                </li>
              ))}
            </ul>
            {attention.length > 5 && (
              <p className="px-4 py-2 text-2xs text-mist-400">
                And {attention.length - 5} more in the health checks below.
              </p>
            )}
          </div>
        </div>
      </section>

      {/* ---- key numbers --------------------------------------------- */}
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Transactions per second"
          info={<Info term="tps" />}
          value={tpsNow.toFixed(1)}
          unit="/s"
          footer={describeTps(tpsNow, tpsAvg)}
        >
          <Sparkline values={spark("tps")} color={t.series.blue} height={40} />
        </StatTile>
        <StatTile
          label="Cache hit ratio"
          info={<Info term="cache_hit" />}
          value={pct(hitNow)}
          tone={hitNow < 0.9 ? "warn" : "ok"}
          footer={
            hitNow >= 0.99 ? "Excellent: reads come from memory" : hitNow >= 0.95 ? "Good" : "Low: more reads are hitting disk"
          }
        >
          <Sparkline values={spark("cache_hit_ratio")} color={t.series.aqua} height={40} />
        </StatTile>
        <StatTile
          label="Connections"
          info={<Info term="connections" />}
          value={num(Number(m.backends_total ?? 0))}
          unit={`of ${num(Number(m.max_connections ?? 0))}`}
          tone={connTone}
          footer={`${num(Number(m.backends_active ?? 0))} active, ${num(Number(m.backends_idle_in_tx ?? 0))} idle in transaction`}
        >
          <div className="pt-3">
            <Meter ratio={connRatio} tone={connTone} />
          </div>
        </StatTile>
        <StatTile
          label="Database size"
          value={bytes(Number(m.database_size_bytes ?? 0))}
          footer={
            <span className="inline-flex items-center gap-1.5">
              Longest open transaction {seconds(Number(m.longest_xact_seconds ?? 0))}
              <Info term="longest_txn" align="right" />
            </span>
          }
        >
          <p className="pt-1 text-2xs text-mist-400">
            {compact(Number(rates.tup_returned_per_sec ?? 0))} rows read per second
          </p>
        </StatTile>
      </div>

      {/* ---- throughput + backends ----------------------------------- */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <section className="card-pad lg:col-span-8">
          <SectionTitle
            title="Throughput"
            hint={`Average ${tpsAvg.toFixed(1)} per second, peak ${tpsPeak.toFixed(1)} over the selected window.`}
            right={
              <Segmented
                label="Time range"
                value={range}
                onChange={setRange}
                options={[
                  { value: "5", label: "5 min" },
                  { value: "15", label: "15 min" },
                ]}
              />
            }
          />
          <LiveAreaChart
            hero
            data={points}
            dataKey="tps"
            name="Transactions"
            height={300}
            formatter={(v) => `${v.toFixed(1)}/s`}
          />
        </section>

        <section className="card-pad lg:col-span-4">
          <SectionTitle
            title={
              <span className="inline-flex items-center gap-1.5">
                Connections by state
                <Info term="idle_in_tx" align="right" />
              </span>
            }
            hint="Layers add up to the total number of open connections."
          />
          <StackedAreaChart
            data={stackPoints}
            height={250}
            formatter={(v) => String(Math.round(v))}
            series={[
              { key: "backends_active", name: "Active", color: t.series.blue },
              { key: "backends_idle_in_tx", name: "Idle in transaction", color: t.series.orange },
              { key: "backends_other", name: "Idle", color: t.series.aqua },
            ]}
          />
          <div className="mt-3">
            <ChartLegend
              items={[
                { name: "Active", color: t.series.blue },
                { name: "Idle in transaction", color: t.series.orange },
                { name: "Idle", color: t.series.aqua },
              ]}
            />
          </div>
        </section>
      </div>

      {/* ---- cache + statements -------------------------------------- */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
        <section className="card-pad lg:col-span-5">
          <SectionTitle
            title="Cache hit ratio"
            hint="The axis starts near the data so small dips stay visible."
          />
          <LiveAreaChart
            data={points}
            dataKey="cache_hit_ratio"
            name="Hit ratio"
            color={t.series.aqua}
            height={300}
            min={hitFloor}
            max={100}
            formatter={(v) => `${v.toFixed(2)}%`}
            axisFormatter={(v) => `${v.toFixed(0)}%`}
          />
        </section>

        <section className="card-pad lg:col-span-7">
          <SectionTitle
            title="Busiest statements right now"
            hint="Ranked by time spent since the previous poll, not the all-time total."
            right={
              <Link href="/queries" className="btn-default">
                All queries
              </Link>
            }
          />
          {!latest?.top_statements?.length ? (
            <p className="py-10 text-center text-xs text-mist-400">Waiting for the next poll…</p>
          ) : (
            <div className="-mx-3 overflow-x-auto">
              <table className="w-full min-w-[520px]">
                <thead>
                  <tr>
                    <th className="th w-[46%]">Statement</th>
                    <th className="th text-right">Calls</th>
                    <th className="th text-right">Average</th>
                    <th className="th w-40 text-right">Time spent</th>
                  </tr>
                </thead>
                <tbody>
                  {latest.top_statements.slice(0, 6).map((s) => (
                    <tr key={String(s.queryid)} className="row-hover">
                      <td className="td max-w-0">
                        <span className="block truncate font-mono text-2xs text-mist-300">
                          {oneLine(s.query, 110)}
                        </span>
                      </td>
                      <td className="td tnum text-right">{num(s.delta_calls)}</td>
                      <td className="td tnum text-right">{ms(s.mean_exec_ms)}</td>
                      <td className="td">
                        <div className="flex items-center justify-end gap-2">
                          <span className="tnum font-semibold text-mist-100">
                            {ms(s.delta_total_exec_ms)}
                          </span>
                          <span className="h-1.5 w-14 overflow-hidden rounded-full bg-ink-800">
                            <span
                              className="block h-full rounded-full bg-series-1"
                              style={{
                                width: `${Math.max((s.delta_total_exec_ms / topMax) * 100, 4)}%`,
                              }}
                            />
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* ---- all health checks --------------------------------------- */}
      <section className="card-pad">
        <SectionTitle
          title="All health checks"
          hint="Open a check to see what it measures and what the value means."
        />
        {health.isLoading && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        )}
        {health.isError && <ErrorNote message={(health.error as Error).message} />}
        <div className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
          {h?.checks.map((check) => (
            <details key={check.key} className="group border-t border-ink-700/70 py-3.5">
              <summary className="flex cursor-pointer list-none items-center gap-3">
                <span
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                    check.status === "ok"
                      ? "bg-sev-ok"
                      : check.status === "warn"
                        ? "bg-sev-medium"
                        : check.status === "critical"
                          ? "bg-sev-critical"
                          : "bg-mist-500"
                  }`}
                />
                <span className="text-xs font-medium text-mist-100">{check.label}</span>
                <span className={`pill ml-auto ${statusClass[check.status] ?? ""}`}>{check.value}</span>
              </summary>
              <p className="mt-2 pl-[22px] text-2xs leading-relaxed text-mist-400">{check.message}</p>
            </details>
          ))}
        </div>
      </section>

      {/* ---- blocking ------------------------------------------------ */}
      {blockedCount > 0 && (
        <section id="lock-waits" className="card-pad ring-1 ring-sev-critical/30">
          <SectionTitle
            title={
              <span className="inline-flex items-center gap-1.5">
                Lock waits
                <Info term="blocked" />
              </span>
            }
            hint="Each row is one session waiting on another. The blocker may itself be idle in a transaction."
          />
          <div className="-mx-3 overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Blocked PID</th>
                  <th className="th">Waiting on</th>
                  <th className="th">Blocked for</th>
                  <th className="th">Blocking PID</th>
                  <th className="th">Blocking state</th>
                </tr>
              </thead>
              <tbody>
                {blocking.data!.blocking.map((row, i) => (
                  <tr key={i} className="row-hover">
                    <td className="td tnum">{String(row.blocked_pid)}</td>
                    <td className="td">
                      {String(row.wait_event_type ?? "")} / {String(row.wait_event ?? "")}
                    </td>
                    <td className="td tnum">{seconds(Number(row.blocked_seconds ?? 0))}</td>
                    <td className="td tnum">{String(row.blocking_pid)}</td>
                    <td className="td">{String(row.blocking_state ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
