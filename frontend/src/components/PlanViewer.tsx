"use client";

import { useState } from "react";
import { compact, ms, num } from "@/lib/format";
import type { PlanFinding } from "@/lib/types";

interface PlanNode {
  "Node Type": string;
  "Relation Name"?: string;
  "Index Name"?: string;
  "Plan Rows"?: number;
  "Actual Rows"?: number;
  "Actual Loops"?: number;
  "Actual Total Time"?: number;
  "Total Cost"?: number;
  Filter?: string;
  "Index Cond"?: string;
  "Rows Removed by Filter"?: number;
  "Sort Method"?: string;
  "Hash Batches"?: number;
  "Heap Fetches"?: number;
  Plans?: PlanNode[];
  [key: string]: unknown;
}

/** Time in this node alone — ``Actual Total Time`` is per-loop and inclusive. */
function selfTime(node: PlanNode): number {
  const loops = node["Actual Loops"] ?? 1;
  const total = (node["Actual Total Time"] ?? 0) * loops;
  const children = (node.Plans ?? []).reduce(
    (sum, c) => sum + (c["Actual Total Time"] ?? 0) * (c["Actual Loops"] ?? 1),
    0,
  );
  return Math.max(total - children, 0);
}

function label(node: PlanNode): string {
  const base = node["Node Type"];
  if (node["Index Name"]) return `${base} using ${node["Index Name"]}`;
  if (node["Relation Name"]) return `${base} on ${node["Relation Name"]}`;
  return base;
}

function misestimateRatio(node: PlanNode): number | null {
  const actualRows = node["Actual Rows"];
  if (actualRows === undefined) return null;
  const loops = node["Actual Loops"] ?? 1;
  const actual = Math.max(actualRows * loops, 1);
  const planned = Math.max((node["Plan Rows"] ?? 0) * loops, 1);
  return Math.max(actual / planned, planned / actual);
}

function NodeRow({
  node,
  depth,
  maxSelf,
  analyzed,
}: {
  node: PlanNode;
  depth: number;
  maxSelf: number;
  analyzed: boolean;
}) {
  const [open, setOpen] = useState(depth < 3);
  const children = node.Plans ?? [];
  const self = selfTime(node);
  const share = maxSelf > 0 ? self / maxSelf : 0;
  const ratio = misestimateRatio(node);
  const removed = node["Rows Removed by Filter"] ?? 0;

  const isSeqScan = node["Node Type"] === "Seq Scan";
  const badEstimate = ratio !== null && ratio >= 10;
  const spilled =
    (node["Sort Method"] ?? "").toLowerCase().includes("external") ||
    (node["Hash Batches"] ?? 0) > 1;

  const flagged = badEstimate || spilled || (isSeqScan && removed > 10_000);

  return (
    <div>
      <div
        className={`group relative flex items-start gap-2 border-l-2 py-1.5 pr-2 text-xs ${
          flagged ? "border-sev-high/50" : "border-ink-700"
        }`}
        style={{ paddingLeft: `${depth * 16 + 10}px` }}
      >
        {/* time bar, drawn behind the row */}
        {analyzed && share > 0.01 && (
          <span
            className="pointer-events-none absolute inset-y-0 left-0 bg-accent/[0.07]"
            style={{ width: `${Math.min(share * 100, 100)}%` }}
            aria-hidden
          />
        )}

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className={`mt-0.5 h-3 w-3 shrink-0 rounded-sm border text-[8px] leading-none ${
            children.length
              ? "border-ink-600 bg-ink-800 text-mist-400 hover:text-mist-100"
              : "invisible"
          }`}
          aria-label={open ? "Collapse" : "Expand"}
        >
          {open ? "–" : "+"}
        </button>

        <div className="relative min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span
              className={`font-medium ${flagged ? "text-sev-high" : "text-mist-100"}`}
            >
              {label(node)}
            </span>

            {analyzed && (
              <span className="tnum text-2xs text-mist-400">{ms(self)}</span>
            )}

            {node["Actual Rows"] !== undefined && (
              <span className="tnum text-2xs text-mist-500">
                {compact((node["Actual Rows"] ?? 0) * (node["Actual Loops"] ?? 1))} rows
                {ratio !== null && ratio >= 10 && (
                  <span className="ml-1 text-sev-high">
                    (est {compact((node["Plan Rows"] ?? 0) * (node["Actual Loops"] ?? 1))},
                    off {ratio.toFixed(0)}×)
                  </span>
                )}
              </span>
            )}

            {(node["Actual Loops"] ?? 1) > 1 && (
              <span className="tnum text-2xs text-mist-500">
                ×{num(node["Actual Loops"])} loops
              </span>
            )}

            {removed > 0 && (
              <span className="tnum text-2xs text-mist-500">
                {compact(removed)} discarded by filter
              </span>
            )}

            {spilled && (
              <span className="text-2xs text-sev-medium">
                {node["Sort Method"] ?? `${node["Hash Batches"]} hash batches`}
              </span>
            )}
          </div>

          {(node.Filter || node["Index Cond"]) && (
            <div className="mt-0.5 truncate font-mono text-[10px] text-mist-500">
              {node["Index Cond"] && <span>cond {node["Index Cond"]} </span>}
              {node.Filter && <span>filter {node.Filter}</span>}
            </div>
          )}
        </div>
      </div>

      {open &&
        children.map((child, i) => (
          <NodeRow
            key={i}
            node={child}
            depth={depth + 1}
            maxSelf={maxSelf}
            analyzed={analyzed}
          />
        ))}
    </div>
  );
}

function collectMaxSelf(node: PlanNode): number {
  const children = node.Plans ?? [];
  return Math.max(selfTime(node), ...children.map(collectMaxSelf), 0);
}

export function PlanViewer({
  plan,
  analyzed,
}: {
  plan: unknown;
  analyzed: boolean;
}) {
  const root = Array.isArray(plan) ? plan[0] : plan;
  const node = (root as { Plan?: PlanNode } | undefined)?.Plan;
  if (!node) {
    return <p className="text-xs text-mist-500">No plan to display.</p>;
  }
  const maxSelf = collectMaxSelf(node);

  return (
    <div className="overflow-x-auto rounded-md border border-ink-700 bg-ink-950 py-1">
      <div className="min-w-[640px]">
        <NodeRow node={node} depth={0} maxSelf={maxSelf} analyzed={analyzed} />
      </div>
    </div>
  );
}

export function FindingList({ findings }: { findings: PlanFinding[] }) {
  if (!findings.length) {
    return (
      <p className="text-xs text-mist-500">
        The analyser found no structural problems in this plan. That is not the same
        as the query being fast — it means nothing in the plan shape explains the
        cost.
      </p>
    );
  }

  const tone: Record<string, string> = {
    critical: "border-sev-critical/40 bg-sev-critical/5",
    high: "border-sev-high/40 bg-sev-high/5",
    medium: "border-sev-medium/40 bg-sev-medium/5",
    low: "border-sev-low/40 bg-sev-low/5",
    info: "border-ink-700 bg-ink-850/50",
  };

  const dot: Record<string, string> = {
    critical: "bg-sev-critical",
    high: "bg-sev-high",
    medium: "bg-sev-medium",
    low: "bg-sev-low",
    info: "bg-sev-info",
  };

  return (
    <div className="space-y-2">
      {findings.map((finding, i) => (
        <div
          key={i}
          className={`rounded-md border px-3 py-2.5 ${tone[finding.severity] ?? tone.info}`}
        >
          <div className="flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${dot[finding.severity] ?? dot.info}`}
            />
            <span className="text-xs font-medium text-mist-100">{finding.node}</span>
            <span className="ml-auto font-mono text-[10px] text-mist-500">
              {finding.code}
            </span>
          </div>
          <p className="mt-1.5 text-2xs leading-relaxed text-mist-300">
            {finding.message}
          </p>
          {finding.detail?.remedy && (
            <p className="mt-1.5 border-l-2 border-ink-600 pl-2.5 text-2xs leading-relaxed text-mist-400">
              {String(finding.detail.remedy)}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
