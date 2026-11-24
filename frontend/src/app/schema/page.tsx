"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useConnection } from "@/components/Providers";
import { EmptyState, ErrorNote, SectionTitle, Spinner, PageHeader } from "@/components/Shell";
import { Caveat, Pill, Segmented } from "@/components/Stat";
import { api } from "@/lib/api";
import { bytes, compact, num, pct, relativeTime } from "@/lib/format";

type Tab = "indexes" | "tables";

export default function SchemaPage() {
  const { connectionId, current } = useConnection();
  const [tab, setTab] = useState<Tab>("indexes");

  if (!connectionId) {
    return (
      <EmptyState
        title="No database selected"
        body="Pick a database from the selector in the header."
      />
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Schema"
        subtitle="Every index and table in this database, read live. Look here for unused, duplicate and missing indexes."
        right={
          <Segmented
            label="Schema view"
            value={tab}
            onChange={setTab}
            options={[
              { value: "indexes", label: "Indexes" },
              { value: "tables", label: "Tables" },
            ]}
          />
        }
      />

      {tab === "indexes" ? (
        <IndexesTab connectionId={connectionId} />
      ) : (
        <TablesTab
          connectionId={connectionId}
          hasPgstattuple={Boolean(current?.extensions?.pgstattuple)}
        />
      )}
    </div>
  );
}

function IndexesTab({ connectionId }: { connectionId: string }) {
  const indexes = useQuery({
    queryKey: ["indexes", connectionId],
    queryFn: () => api.schema.indexes(connectionId),
  });
  const fks = useQuery({
    queryKey: ["fks", connectionId],
    queryFn: () => api.schema.unindexedForeignKeys(connectionId),
  });

  if (indexes.isLoading) return <Spinner label="Reading pg_stat_user_indexes" />;
  if (indexes.isError) return <ErrorNote message={(indexes.error as Error).message} />;

  const rows = indexes.data ?? [];
  const invalid = rows.filter((r) => !r.indisvalid);
  const unused = rows.filter(
    (r) => r.idx_scan === 0 && !r.indisprimary && !r.indisunique,
  );
  const totalBytes = rows.reduce((sum, r) => sum + r.index_bytes, 0);
  const missingFks = fks.data?.foreign_keys ?? [];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <div className="card-pad">
          <div className="label">Indexes</div>
          <div className="tnum mt-1 text-lg font-semibold text-mist-100">
            {num(rows.length)}
          </div>
        </div>
        <div className="card-pad">
          <div className="label">Total index size</div>
          <div className="tnum mt-1 text-lg font-semibold text-mist-100">
            {bytes(totalBytes)}
          </div>
        </div>
        <div className="card-pad">
          <div className="label">Never scanned</div>
          <div className="tnum mt-1 text-lg font-semibold text-sev-medium">
            {num(unused.length)}
          </div>
        </div>
        <div className="card-pad">
          <div className="label">Unindexed foreign keys</div>
          <div
            className={`tnum mt-1 text-lg font-semibold ${
              missingFks.length ? "text-sev-high" : "text-sev-ok"
            }`}
          >
            {num(missingFks.length)}
          </div>
        </div>
      </div>

      {invalid.length > 0 && (
        <div className="card-pad border-sev-critical/30">
          <SectionTitle
            title="Invalid indexes"
            hint="The worst of both worlds: the planner ignores them, but every write still maintains them."
          />
          <ul className="space-y-1.5">
            {invalid.map((r) => (
              <li key={r.index_name} className="font-mono text-2xs text-sev-critical">
                {r.schemaname}.{r.index_name} on {r.table_name} — {bytes(r.index_bytes)}
              </li>
            ))}
          </ul>
          <Caveat>
            These are almost always left behind by a failed CREATE INDEX
            CONCURRENTLY. Drop each with DROP INDEX CONCURRENTLY, then rebuild if
            the index is still wanted.
          </Caveat>
        </div>
      )}

      {missingFks.length > 0 && (
        <div className="card-pad border-sev-high/30">
          <SectionTitle
            title="Foreign keys with no supporting index"
            hint="Read directly from the catalog — this check is exact, not a heuristic."
          />
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Table</th>
                  <th className="th">Columns</th>
                  <th className="th">References</th>
                  <th className="th text-right">Table size</th>
                </tr>
              </thead>
              <tbody>
                {missingFks.map((fk, i) => (
                  <tr key={i} className="row-hover">
                    <td className="td font-mono">{String(fk.table_name)}</td>
                    <td className="td font-mono text-sev-high">
                      {(fk.columns as string[])?.join(", ")}
                    </td>
                    <td className="td font-mono">{String(fk.references_table)}</td>
                    <td className="td tnum text-right">
                      {bytes(Number(fk.table_bytes ?? 0))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Caveat>
            Without an index leading with these columns, every join across the key
            and every delete or key update on the parent must scan the child table
            in full. Run an analysis on the Suggestions page to generate the index
            statements.
          </Caveat>
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-ink-850">
              <tr>
                <th className="th">Index</th>
                <th className="th">Table</th>
                <th className="th">Columns</th>
                <th className="th">Method</th>
                <th className="th text-right">Scans</th>
                <th className="th text-right">Size</th>
                <th className="th">Flags</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.schemaname}.${r.index_name}`} className="row-hover">
                  <td className="td font-mono text-mist-200">{r.index_name}</td>
                  <td className="td font-mono">{r.table_name}</td>
                  <td className="td font-mono text-mist-400">
                    {r.key_columns.join(", ") || "—"}
                  </td>
                  <td className="td">{r.method}</td>
                  <td className="td tnum text-right">
                    <span className={r.idx_scan === 0 ? "text-sev-medium" : ""}>
                      {compact(r.idx_scan)}
                    </span>
                  </td>
                  <td className="td tnum text-right">{bytes(r.index_bytes)}</td>
                  <td className="td">
                    <span className="flex flex-wrap gap-1">
                      {r.indisprimary && <Pill tone="info">pk</Pill>}
                      {r.indisunique && !r.indisprimary && <Pill tone="info">unique</Pill>}
                      {r.is_partial && <Pill tone="info">partial</Pill>}
                      {!r.indisvalid && <Pill tone="critical">invalid</Pill>}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Caveat>
        A scan count of zero is cumulative since the last statistics reset, not
        since the database was created. A weekly report or a month-end job is
        exactly the query a short observation window misses, so confirm against a
        full business cycle before dropping anything on this evidence alone.
      </Caveat>
    </div>
  );
}

function TablesTab({
  connectionId,
  hasPgstattuple,
}: {
  connectionId: string;
  hasPgstattuple: boolean;
}) {
  const tables = useQuery({
    queryKey: ["tables", connectionId],
    queryFn: () => api.schema.tables(connectionId),
  });
  const [bloatFor, setBloatFor] = useState<string | null>(null);
  const [bloatData, setBloatData] = useState<Record<string, unknown> | null>(null);
  const [bloatError, setBloatError] = useState<string | null>(null);

  const measure = async (schema: string, table: string) => {
    setBloatFor(`${schema}.${table}`);
    setBloatData(null);
    setBloatError(null);
    try {
      setBloatData(await api.schema.bloat(connectionId, schema, table));
    } catch (e) {
      setBloatError((e as Error).message);
    }
  };

  if (tables.isLoading) return <Spinner label="Reading pg_stat_user_tables" />;
  if (tables.isError) return <ErrorNote message={(tables.error as Error).message} />;

  const rows = tables.data ?? [];

  return (
    <div className="space-y-5">
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-ink-850">
              <tr>
                <th className="th">Table</th>
                <th className="th text-right">Seq scans</th>
                <th className="th text-right">Rows/scan</th>
                <th className="th text-right">Idx scans</th>
                <th className="th text-right">Live</th>
                <th className="th text-right">Dead</th>
                <th className="th text-right">Dead %</th>
                <th className="th text-right">Since ANALYZE</th>
                <th className="th text-right">Total</th>
                <th className="th">Autovacuum</th>
                <th className="th" />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const perScan = r.seq_scan ? r.seq_tup_read / r.seq_scan : 0;
                const scanShare =
                  r.seq_scan + r.idx_scan ? r.seq_scan / (r.seq_scan + r.idx_scan) : 0;
                return (
                  <tr key={`${r.schemaname}.${r.table_name}`} className="row-hover">
                    <td className="td font-mono text-mist-200">{r.table_name}</td>
                    <td className="td tnum text-right">
                      <span className={scanShare > 0.7 ? "text-sev-high" : ""}>
                        {compact(r.seq_scan)}
                      </span>
                    </td>
                    <td className="td tnum text-right">{compact(perScan)}</td>
                    <td className="td tnum text-right">{compact(r.idx_scan)}</td>
                    <td className="td tnum text-right">{compact(r.n_live_tup)}</td>
                    <td className="td tnum text-right">{compact(r.n_dead_tup)}</td>
                    <td className="td tnum text-right">
                      <span
                        className={
                          r.dead_ratio > 0.4
                            ? "text-sev-high"
                            : r.dead_ratio > 0.2
                              ? "text-sev-medium"
                              : ""
                        }
                      >
                        {pct(r.dead_ratio, 0)}
                      </span>
                    </td>
                    <td className="td tnum text-right">
                      {compact(r.n_mod_since_analyze)}
                    </td>
                    <td className="td tnum text-right">{bytes(r.total_bytes)}</td>
                    <td className="td text-mist-500">
                      {relativeTime(r.last_autovacuum)}
                    </td>
                    <td className="td text-right">
                      <button
                        type="button"
                        onClick={() => measure(r.schemaname, r.table_name)}
                        disabled={!hasPgstattuple}
                        className="btn-default"
                        title={
                          hasPgstattuple
                            ? "Measure exact bloat with pgstattuple. Reads the whole relation — slow on a large table."
                            : "pgstattuple is not installed on this database."
                        }
                      >
                        Measure
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <Caveat>
        The live and dead tuple counts above are estimates maintained by the
        statistics collector, not measurements. They are directionally reliable
        and numerically approximate.
        {hasPgstattuple
          ? " Use Measure for an exact figure from pgstattuple."
          : " Install pgstattuple for exact figures."}
      </Caveat>

      {bloatFor && (
        <div className="card-pad">
          <SectionTitle
            title={`Exact bloat — ${bloatFor}`}
            hint="measured by pgstattuple, not estimated"
            right={
              <button
                type="button"
                onClick={() => setBloatFor(null)}
                className="btn-default"
              >
                Close
              </button>
            }
          />
          {bloatError ? (
            <ErrorNote message={bloatError} />
          ) : !bloatData ? (
            <Spinner label="Scanning the relation" />
          ) : (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                ["Table size", bytes(Number(bloatData.table_bytes))],
                ["Live tuples", num(Number(bloatData.live_tuples))],
                ["Dead tuples", num(Number(bloatData.dead_tuples))],
                ["Dead bytes", bytes(Number(bloatData.dead_bytes))],
                ["Free space", bytes(Number(bloatData.free_bytes))],
                ["Free %", `${Number(bloatData.free_percent).toFixed(1)}%`],
                ["Reclaimable", bytes(Number(bloatData.reclaimable_bytes))],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className="rounded-md border border-ink-800 bg-ink-850/50 p-2"
                >
                  <div className="label">{label}</div>
                  <div className="tnum mt-0.5 text-xs text-mist-100">{value}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
