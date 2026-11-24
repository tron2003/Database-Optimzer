export function bytes(value: number | null | undefined): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let i = 0;
  while (Math.abs(size) >= 1024 && i < units.length - 1) {
    size /= 1024;
    i += 1;
  }
  return i === 0 ? `${Math.round(size)} B` : `${size.toFixed(1)} ${units[i]}`;
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return `${(value * 1000).toFixed(0)} µs`;
  if (value < 1000) return `${value.toFixed(1)} ms`;
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`;
  return `${(value / 60_000).toFixed(1)} min`;
}

export function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString();
}

export function compact(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) < 1000) return String(Math.round(value));
  return Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function seconds(value: number | null | undefined): string {
  if (!value) return "0s";
  if (value < 60) return `${value.toFixed(0)}s`;
  if (value < 3600) return `${(value / 60).toFixed(0)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

export function clockTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const delta = (Date.now() - then) / 1000;
  if (delta < 5) return "just now";
  if (delta < 60) return `${Math.round(delta)}s ago`;
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`;
  if (delta < 86_400) return `${Math.round(delta / 3600)}h ago`;
  return `${Math.round(delta / 86_400)}d ago`;
}

/** Collapse whitespace so a normalised statement fits one table row. */
export function oneLine(sql: string, max = 140): string {
  const flat = sql.replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max)}…` : flat;
}

export const severityClass: Record<string, string> = {
  critical: "text-sev-critical border-sev-critical/30 bg-sev-critical/10",
  high: "text-sev-high border-sev-high/30 bg-sev-high/10",
  medium: "text-sev-medium border-sev-medium/30 bg-sev-medium/10",
  low: "text-sev-low border-sev-low/30 bg-sev-low/10",
  info: "text-sev-info border-sev-info/30 bg-sev-info/10",
};

export const statusClass: Record<string, string> = {
  ok: "text-sev-ok border-sev-ok/30 bg-sev-ok/10",
  warn: "text-sev-medium border-sev-medium/30 bg-sev-medium/10",
  critical: "text-sev-critical border-sev-critical/30 bg-sev-critical/10",
  unknown: "text-mist-500 border-ink-600 bg-ink-800",
};
