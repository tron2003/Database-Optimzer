"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useConnection } from "@/components/Providers";
import { ErrorNote, SectionTitle, Spinner, PageHeader } from "@/components/Shell";
import { Caveat, Pill } from "@/components/Stat";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import type { ProbeResult } from "@/lib/types";

export default function ConnectionsPage() {
  const { connections, loading, error, refresh, connectionId, setConnectionId } =
    useConnection();
  const [showForm, setShowForm] = useState(false);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Connections"
        subtitle="Each database is registered with a read-only monitor login and, optionally, a separate login used only to run changes you approve."
        right={
          <button
            type="button"
            onClick={() => setShowForm((s) => !s)}
            className="btn-primary"
          >
            {showForm ? "Cancel" : "Register a database"}
          </button>
        }
      />

      {error && <ErrorNote message={error} />}
      {showForm && (
        <RegisterForm
          onDone={() => {
            setShowForm(false);
            refresh();
          }}
        />
      )}

      {loading ? (
        <Spinner />
      ) : (
        <div className="space-y-3">
          {connections.map((c) => (
            <div
              key={c.id}
              className={`card p-4 ${c.id === connectionId ? "border-accent-dim/50" : ""}`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium text-mist-100">{c.name}</h3>
                    {c.kind === "sandbox" && <Pill tone="low">sandbox</Pill>}
                    {c.can_apply ? (
                      <Pill tone="medium">can apply changes</Pill>
                    ) : (
                      <Pill tone="info">observe only</Pill>
                    )}
                    {c.allow_explain_analyze && (
                      <Pill tone="high">EXPLAIN ANALYZE enabled</Pill>
                    )}
                    {!c.enabled && <Pill tone="info">paused</Pill>}
                  </div>
                  <p className="mt-1 break-all font-mono text-2xs text-mist-500">
                    {c.dsn_redacted}
                  </p>
                  <p className="mt-1 text-2xs text-mist-500">
                    PostgreSQL {c.server_version ?? "?"} · polled{" "}
                    {relativeTime(c.last_polled_at)} ·{" "}
                    {c.collector_running ? "collector running" : "collector stopped"}
                  </p>
                  {Object.keys(c.extensions ?? {}).length > 0 && (
                    <p className="mt-1 text-2xs text-mist-500">
                      extensions: {Object.keys(c.extensions).join(", ")}
                    </p>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {c.id !== connectionId && (
                    <button
                      type="button"
                      onClick={() => setConnectionId(c.id)}
                      className="btn-default"
                    >
                      Select
                    </button>
                  )}
                  <ConnectionActions connectionId={c.id} onDone={refresh} row={c} />
                </div>
              </div>

              {c.last_error && (
                <p className="mt-3 rounded-md border border-sev-critical/30 bg-sev-critical/5 p-2 text-2xs text-sev-critical">
                  {c.last_error}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ConnectionActions({
  connectionId,
  row,
  onDone,
}: {
  connectionId: string;
  row: { enabled: boolean; allow_explain_analyze: boolean; kind: string };
  onDone: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  const update = useMutation({
    mutationFn: (payload: Parameters<typeof api.connections.update>[1]) =>
      api.connections.update(connectionId, payload),
    onSuccess: onDone,
  });

  const remove = useMutation({
    mutationFn: () => api.connections.remove(connectionId),
    onSuccess: onDone,
  });

  return (
    <>
      <button
        type="button"
        onClick={() => update.mutate({ enabled: !row.enabled })}
        className="btn-default"
      >
        {row.enabled ? "Pause polling" : "Resume polling"}
      </button>

      <button
        type="button"
        onClick={() => {
          if (
            !row.allow_explain_analyze &&
            row.kind === "external" &&
            !window.confirm(
              "EXPLAIN ANALYZE executes the statement being explained.\n\n" +
                "It is restricted to SELECT and runs inside a transaction that is " +
                "rolled back, but it will still perform every read the query performs " +
                "— which on a production database can be expensive.\n\n" +
                "Enable it for this connection?",
            )
          ) {
            return;
          }
          update.mutate({ allow_explain_analyze: !row.allow_explain_analyze });
        }}
        className="btn-default"
      >
        {row.allow_explain_analyze ? "Disable ANALYZE" : "Enable ANALYZE"}
      </button>

      {confirmDelete ? (
        <>
          <button
            type="button"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
            className="btn-danger"
          >
            Confirm delete
          </button>
          <button
            type="button"
            onClick={() => setConfirmDelete(false)}
            className="btn-default"
          >
            Keep
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={() => setConfirmDelete(true)}
          className="btn-default"
        >
          Remove
        </button>
      )}
    </>
  );
}

function RegisterForm({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [dsn, setDsn] = useState("");
  const [applyDsn, setApplyDsn] = useState("");
  const [probeResult, setProbeResult] = useState<ProbeResult | null>(null);

  const probe = useMutation({
    mutationFn: () => api.connections.probe(dsn),
    onSuccess: setProbeResult,
  });

  const create = useMutation({
    mutationFn: () =>
      api.connections.create({
        name,
        dsn,
        apply_dsn: applyDsn.trim() || null,
        allow_explain_analyze: false,
      }),
    onSuccess: onDone,
  });

  return (
    <div className="card-pad space-y-4">
      <SectionTitle title="Register a database" />

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label className="space-y-1">
          <span className="label">Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Production replica"
            className="input"
          />
        </label>

        <label className="space-y-1">
          <span className="label">Monitor DSN — read only</span>
          <input
            value={dsn}
            onChange={(e) => setDsn(e.target.value)}
            placeholder="postgresql://monitor:***@host:5432/dbname"
            className="input font-mono text-xs"
          />
        </label>

        <label className="space-y-1 md:col-span-2">
          <span className="label">
            Apply DSN — optional, used only to execute approved changes
          </span>
          <input
            value={applyDsn}
            onChange={(e) => setApplyDsn(e.target.value)}
            placeholder="leave empty to make this connection observation-only"
            className="input font-mono text-xs"
          />
        </label>
      </div>

      <Caveat>
        Point the monitor DSN at a dedicated role: a member of{" "}
        <code>pg_monitor</code> with <code>default_transaction_read_only = on</code>{" "}
        and no write privileges. The application sets statement, lock and idle
        timeouts on every session it opens, but a role that cannot write is the
        guarantee that matters.
      </Caveat>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => probe.mutate()}
          disabled={!dsn.trim() || probe.isPending}
          className="btn-default"
        >
          {probe.isPending ? "Testing…" : "Test connection"}
        </button>
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={!name.trim() || !dsn.trim() || create.isPending}
          className="btn-primary"
        >
          {create.isPending ? "Registering…" : "Register"}
        </button>
      </div>

      {probe.isError && <ErrorNote message={(probe.error as Error).message} />}
      {create.isError && <ErrorNote message={(create.error as Error).message} />}

      {probeResult && (
        <div
          className={`rounded-md border p-3 ${
            probeResult.ok
              ? "border-sev-ok/40 bg-sev-ok/5"
              : "border-sev-critical/40 bg-sev-critical/5"
          }`}
        >
          {probeResult.ok ? (
            <>
              <p className="text-xs text-sev-ok">
                Connected — PostgreSQL {probeResult.server_version}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Pill tone={probeResult.has_pg_stat_statements ? "ok" : "medium"}>
                  pg_stat_statements {probeResult.has_pg_stat_statements ? "✓" : "✗"}
                </Pill>
                <Pill tone={probeResult.has_pgstattuple ? "ok" : "info"}>
                  pgstattuple {probeResult.has_pgstattuple ? "✓" : "✗"}
                </Pill>
                <Pill tone={probeResult.has_pg_monitor ? "ok" : "medium"}>
                  pg_monitor {probeResult.has_pg_monitor ? "✓" : "✗"}
                </Pill>
                <Pill tone={probeResult.session_is_writable ? "high" : "ok"}>
                  {probeResult.session_is_writable ? "writable" : "read only"}
                </Pill>
                {probeResult.is_superuser && <Pill tone="critical">superuser</Pill>}
              </div>
              {probeResult.warnings.length > 0 && (
                <ul className="mt-3 space-y-1.5">
                  {probeResult.warnings.map((w, i) => (
                    <li key={i} className="text-2xs leading-relaxed text-sev-medium">
                      {w}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-xs text-sev-critical">{probeResult.error}</p>
          )}
        </div>
      )}
    </div>
  );
}
