"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Dialog } from "@/components/Dialog";
import { Icon } from "@/components/Icons";
import { Info } from "@/components/Info";
import { useConnection } from "@/components/Providers";
import { EmptyState, ErrorNote, PageHeader } from "@/components/Shell";
import { Caveat, Pill, Segmented, Skeleton } from "@/components/Stat";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { ms, relativeTime } from "@/lib/format";
import type { ExecutionResult, Severity, Suggestion } from "@/lib/types";

type Filter = "proposed" | "applied" | "rejected" | "failed" | "all";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "proposed", label: "To review" },
  { value: "applied", label: "Applied" },
  { value: "rejected", label: "Rejected" },
  { value: "failed", label: "Failed" },
  { value: "all", label: "All" },
];

const NAME_KEY = "dbopt.approver";

export default function SuggestionsPage() {
  const { connectionId, current } = useConnection();
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("proposed");

  const suggestions = useQuery({
    queryKey: ["suggestions", connectionId, filter],
    queryFn: () => api.suggestions.list(connectionId!, filter === "all" ? undefined : filter),
    enabled: !!connectionId,
  });

  const analyze = useMutation({
    mutationFn: () => api.suggestions.analyze(connectionId!),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      const made = (run.suggestions.created ?? 0) + (run.suggestions.updated ?? 0);
      toast.push({
        tone: "success",
        title: made ? `Found ${made} suggestion${made === 1 ? "" : "s"}` : "Analysis finished",
        body: made ? "Review each one before anything runs." : "Nothing to fix right now.",
      });
    },
    onError: (e: Error) => toast.push({ tone: "error", title: "Analysis failed", body: e.message }),
  });

  const executions = useQuery({
    queryKey: ["executions", connectionId],
    queryFn: () => api.suggestions.executions(connectionId!),
    enabled: !!connectionId,
  });

  if (!connectionId) {
    return (
      <EmptyState
        title="No database selected"
        body="Pick a database from the selector at the top right."
      />
    );
  }

  const rows = suggestions.data ?? [];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Suggestions"
        subtitle="Recommended fixes for this database. Nothing runs by itself: every change is checked against a safety allowlist and only runs when you approve it."
        right={
          <button
            type="button"
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending}
            className="btn-primary h-10 px-5"
          >
            {analyze.isPending ? "Analyzing…" : "Run analysis"}
          </button>
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segmented label="Filter by status" value={filter} onChange={setFilter} options={FILTERS} />
        <span className="flex items-center gap-1.5 text-2xs text-mist-400">
          How changes stay safe <Info term="allowlist" align="right" />
        </span>
      </div>

      {analyze.data && (
        <div className="card-pad">
          <p className="text-xs text-mist-300">
            Examined {analyze.data.tables_examined} tables and {analyze.data.indexes_examined} indexes
            in {analyze.data.duration_seconds.toFixed(2)}s: {analyze.data.suggestions.created ?? 0} new,{" "}
            {analyze.data.suggestions.updated ?? 0} refreshed, {analyze.data.suggestions.unchanged ?? 0}{" "}
            already decided.
          </p>
          {analyze.data.stats_age_hours !== null && (
            <Caveat>
              The server&apos;s statistics have been accumulating for{" "}
              {analyze.data.stats_age_hours.toFixed(0)} hours.
              {analyze.data.stats_age_hours < 168
                ? " That is less than a week, so any conclusion resting on a zero scan count is provisional. A weekly job may not have run yet."
                : " That covers more than a week, so periodic jobs have most likely run at least once."}
            </Caveat>
          )}
        </div>
      )}

      {!current?.can_apply && (
        <div className="flex items-start gap-3 rounded-2xl bg-ink-850 px-4 py-3 ring-1 ring-inset ring-ink-700">
          <Icon name="info" className="mt-0.5 h-4 w-4 shrink-0 text-mist-400" />
          <p className="text-xs leading-relaxed text-mist-400">
            This connection has no apply credential, so suggestions can be reviewed but not run.{" "}
            <Link href="/connections" className="font-semibold text-accent-glow underline">
              Add one on the Connections page
            </Link>{" "}
            to enable approval, or copy a statement and run it yourself.
          </p>
        </div>
      )}

      {suggestions.isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-52" />
          <Skeleton className="h-52" />
        </div>
      ) : suggestions.isError ? (
        <ErrorNote message={(suggestions.error as Error).message} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={filter === "proposed" ? "Nothing to review" : filter === "all" ? "No suggestions yet" : `Nothing ${filter}`}
          body="Run an analysis to check for unindexed foreign keys, duplicate and unused indexes, bloat, and stale statistics."
          action={
            <button
              type="button"
              className="btn-primary h-10 px-5"
              onClick={() => analyze.mutate()}
              disabled={analyze.isPending}
            >
              {analyze.isPending ? "Analyzing…" : "Run analysis"}
            </button>
          }
        />
      ) : (
        <div className="space-y-4">
          {rows.map((s) => (
            <SuggestionCard
              key={s.id}
              suggestion={s}
              connectionId={connectionId}
              databaseName={current?.name ?? "this database"}
              canApply={current?.can_apply ?? false}
            />
          ))}
        </div>
      )}

      {(executions.data?.length ?? 0) > 0 && (
        <section className="card-pad">
          <h2 className="text-lg font-semibold text-mist-100">Audit log</h2>
          <p className="mb-4 mt-1 text-xs text-mist-400">
            One row per execution attempt, written before the statement runs.
          </p>
          <div className="-mx-3 overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">When</th>
                  <th className="th">Approved by</th>
                  <th className="th">Statement</th>
                  <th className="th text-right">Duration</th>
                  <th className="th">Result</th>
                </tr>
              </thead>
              <tbody>
                {executions.data!.map((e) => (
                  <tr key={e.id} className="row-hover">
                    <td className="td whitespace-nowrap">{relativeTime(e.approved_at)}</td>
                    <td className="td">{e.approved_by}</td>
                    <td className="td max-w-0">
                      <span className="block truncate font-mono text-2xs">{e.statement}</span>
                    </td>
                    <td className="td tnum text-right">{ms(e.duration_ms)}</td>
                    <td className="td">
                      {e.succeeded === null ? (
                        <Pill tone="info">In flight</Pill>
                      ) : e.succeeded ? (
                        <Pill tone="ok">Applied</Pill>
                      ) : (
                        <Pill tone="critical">Failed</Pill>
                      )}
                    </td>
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

const SEV: Record<Severity, { bar: string; chip: string; label: string }> = {
  critical: { bar: "bg-sev-critical", chip: "bg-sev-critical/12 text-sev-critical", label: "Critical" },
  high: { bar: "bg-sev-high", chip: "bg-sev-high/12 text-sev-high", label: "High impact" },
  medium: { bar: "bg-sev-medium", chip: "bg-sev-medium/15 text-sev-medium", label: "Medium impact" },
  low: { bar: "bg-sev-low", chip: "bg-sev-low/12 text-sev-low", label: "Low impact" },
  info: { bar: "bg-sev-info", chip: "bg-ink-800 text-mist-300", label: "For your information" },
};

function SuggestionCard({
  suggestion,
  connectionId,
  databaseName,
  canApply,
}: {
  suggestion: Suggestion;
  connectionId: string;
  databaseName: string;
  canApply: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [expanded, setExpanded] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [approvedBy, setApprovedBy] = useState("");
  const [result, setResult] = useState<ExecutionResult | null>(null);

  // Remember who is approving, so the audit-log name is typed once.
  useEffect(() => {
    try {
      setApprovedBy(window.localStorage.getItem(NAME_KEY) ?? "");
    } catch {
      /* non-fatal */
    }
  }, []);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["suggestions"] });
    qc.invalidateQueries({ queryKey: ["executions"] });
  };

  const approve = useMutation({
    mutationFn: (dryRun: boolean) =>
      api.suggestions.approve(connectionId, suggestion.id, {
        approved_by: approvedBy.trim() || "unknown",
        dry_run: dryRun,
      }),
    onSuccess: (data) => {
      setResult(data);
      if (data.dry_run) return;
      try {
        window.localStorage.setItem(NAME_KEY, approvedBy.trim());
      } catch {
        /* non-fatal */
      }
      invalidate();
      if (data.succeeded) {
        setConfirming(false);
        toast.push({
          tone: "success",
          title: "Change applied",
          body: `Finished in ${ms(data.duration_ms)}. Run an analysis again to confirm it is resolved.`,
        });
      } else {
        toast.push({ tone: "error", title: "The change failed", body: data.error ?? "See the audit log." });
      }
    },
    onError: (e: Error) => toast.push({ tone: "error", title: "Could not run the change", body: e.message }),
  });

  const rejectMut = useMutation({
    mutationFn: () => api.suggestions.reject(connectionId, suggestion.id),
    onSuccess: () => {
      invalidate();
      toast.push({ tone: "info", title: "Suggestion rejected", body: "You can reopen it from the Rejected filter." });
    },
  });

  const reopenMut = useMutation({
    mutationFn: () => api.suggestions.reopen(connectionId, suggestion.id),
    onSuccess: invalidate,
  });

  const sev = SEV[suggestion.severity];
  const decided = ["applied", "rejected"].includes(suggestion.status);
  const close = () => {
    setConfirming(false);
    setResult(null);
  };

  return (
    <article className="card relative overflow-hidden p-6 pl-7">
      <span className={`absolute inset-y-0 left-0 w-1.5 ${sev.bar}`} aria-hidden />

      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`pill border-transparent ${sev.chip}`}>{sev.label}</span>
            <Pill tone="info">{suggestion.kind.replace(/_/g, " ")}</Pill>
            <Pill tone={suggestion.source === "llm" ? "medium" : "low"}>
              {suggestion.source === "llm" ? "Written by the model" : "Found in the catalog"}
            </Pill>
            {suggestion.status !== "proposed" && (
              <Pill
                tone={suggestion.status === "applied" ? "ok" : suggestion.status === "failed" ? "critical" : "info"}
              >
                {suggestion.status}
              </Pill>
            )}
            {!suggestion.statement && <Pill tone="info">Advice only</Pill>}
          </div>
          <h3 className="mt-3 text-lg font-semibold text-mist-100">{suggestion.title}</h3>
          {suggestion.target_relation && (
            <p className="mt-1 font-mono text-2xs text-mist-500">{suggestion.target_relation}</p>
          )}
        </div>
        <button type="button" onClick={() => setExpanded((e) => !e)} className="btn-default">
          {expanded ? "Less detail" : "More detail"}
        </button>
      </div>

      <dl className="mt-4 grid gap-4 md:grid-cols-3">
        <div className="md:col-span-3">
          <dt className="label">Why this is suggested</dt>
          <dd className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-mist-300">
            {expanded || suggestion.rationale.length < 320
              ? suggestion.rationale
              : `${suggestion.rationale.slice(0, 320)}…`}
          </dd>
        </div>
        {suggestion.estimated_benefit && (
          <div className="rounded-2xl bg-sev-ok/[0.07] p-3.5">
            <dt className="label text-sev-ok">What you gain</dt>
            <dd className="mt-1 text-xs leading-relaxed text-mist-200">{suggestion.estimated_benefit}</dd>
          </div>
        )}
        {suggestion.estimated_cost && (
          <div className="rounded-2xl bg-sev-medium/[0.09] p-3.5 md:col-span-2">
            <dt className="label text-sev-medium">What it costs</dt>
            <dd className="mt-1 text-xs leading-relaxed text-mist-200">{suggestion.estimated_cost}</dd>
          </div>
        )}
      </dl>

      {expanded && Object.keys(suggestion.evidence).length > 0 && (
        <details className="mt-4" open>
          <summary className="label cursor-pointer">Evidence</summary>
          <pre className="code mt-2 max-h-48 overflow-auto">{JSON.stringify(suggestion.evidence, null, 2)}</pre>
        </details>
      )}

      {suggestion.statement && (
        <div className="mt-4">
          <div className="label mb-1.5">The SQL that would run</div>
          <pre className="code whitespace-pre-wrap">{suggestion.statement}</pre>
        </div>
      )}

      {/* ---- actions ------------------------------------------------- */}
      <div className="mt-5 flex flex-wrap items-center gap-2">
        {!decided && suggestion.statement && (
          <>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={!canApply}
              className="btn-primary h-10 px-5"
              title={canApply ? undefined : "This connection has no apply credential, so nothing can be run."}
            >
              Review and approve
            </button>
            <button
              type="button"
              onClick={() => rejectMut.mutate()}
              disabled={rejectMut.isPending}
              className="btn-default h-10 px-5"
            >
              Reject
            </button>
          </>
        )}
        {(suggestion.status === "rejected" || suggestion.status === "failed") && (
          <button type="button" onClick={() => reopenMut.mutate()} className="btn-default h-10 px-5">
            {suggestion.status === "failed" ? "Move back to review" : "Reopen"}
          </button>
        )}
      </div>

      {/* ---- confirm ------------------------------------------------- */}
      <Dialog
        open={confirming}
        onClose={close}
        wide
        title="Run this change?"
        description={`It will run on ${databaseName}. You can test it first without changing anything.`}
        footer={
          <>
            <button type="button" onClick={close} className="btn-default h-10 px-5">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => approve.mutate(true)}
              disabled={approve.isPending}
              className="btn-default h-10 px-5"
            >
              Test first (dry run)
            </button>
            <button
              type="button"
              onClick={() => approve.mutate(false)}
              disabled={approve.isPending || !approvedBy.trim()}
              className="btn-primary h-10 px-5"
            >
              {approve.isPending ? "Running…" : "Run now"}
            </button>
          </>
        }
      >
        <div className="space-y-4 pb-4">
          <div>
            <div className="label mb-1.5">What will run</div>
            <pre className="code whitespace-pre-wrap">{suggestion.statement}</pre>
          </div>
          {suggestion.revert_statement && (
            <div>
              <div className="label mb-1.5">How to undo it</div>
              <pre className="code whitespace-pre-wrap">{suggestion.revert_statement}</pre>
            </div>
          )}
          {suggestion.estimated_cost && (
            <p className="rounded-2xl bg-sev-medium/[0.09] p-3.5 text-xs leading-relaxed text-mist-200">
              <span className="font-semibold text-sev-medium">What it costs: </span>
              {suggestion.estimated_cost}
            </p>
          )}
          <label className="block">
            <span className="label">Your name, recorded in the audit log</span>
            <input
              value={approvedBy}
              onChange={(e) => setApprovedBy(e.target.value)}
              placeholder="For example: Kanish"
              className="input mt-1.5"
            />
          </label>

          {result && (
            <div
              role="status"
              className={`rounded-2xl p-3.5 text-xs leading-relaxed ${
                result.dry_run
                  ? "bg-accent-soft text-mist-200"
                  : result.succeeded
                    ? "bg-sev-ok/10 text-sev-ok"
                    : "bg-sev-critical/10 text-sev-critical"
              }`}
            >
              {result.dry_run ? (
                <>
                  <p className="font-semibold text-mist-100">Dry run passed. Nothing was changed.</p>
                  <p className="mt-1">
                    It would run as a <span className="font-mono">{result.kind}</span> on{" "}
                    <span className="font-mono">{result.target}</span>.
                  </p>
                  {result.notes?.map((n, i) => (
                    <p key={i} className="mt-1 text-mist-400">
                      {n}
                    </p>
                  ))}
                </>
              ) : result.succeeded ? (
                <p>Applied in {ms(result.duration_ms)}.</p>
              ) : (
                <p className="whitespace-pre-wrap">{result.error}</p>
              )}
            </div>
          )}
        </div>
      </Dialog>
    </article>
  );
}
