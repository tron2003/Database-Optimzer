"use client";

import type { ReactNode } from "react";

type Tone = "neutral" | "ok" | "warn" | "critical";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-mist-100",
  ok: "text-sev-ok",
  warn: "text-sev-medium",
  critical: "text-sev-critical",
};

export function StatTile({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
  footer,
  info,
  children,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: string;
  tone?: Tone;
  footer?: ReactNode;
  info?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="card-pad flex flex-col gap-2" title={hint}>
      <span className="label flex items-center gap-1.5">
        {label}
        {info}
      </span>
      <div className="flex items-baseline gap-1.5">
        <span
          className={`tnum font-display text-3xl font-semibold leading-none ${TONE_TEXT[tone]}`}
        >
          {value}
        </span>
        {unit && <span className="text-xs text-mist-500">{unit}</span>}
      </div>
      {footer && <div className="text-2xs text-mist-400">{footer}</div>}
      {children}
    </div>
  );
}

/**
 * One line of the "vitals" list on the overview: a label, a value, and an
 * optional trend or meter on the right. Rows share a panel and are divided by
 * hairlines, so six numbers read as one instrument rather than six boxes.
 */
export function VitalRow({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
  note,
  trend,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: string;
  tone?: Tone;
  note?: ReactNode;
  trend?: ReactNode;
}) {
  return (
    <div
      className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 px-6 py-4"
      title={hint}
    >
      <div className="min-w-0">
        <div className="label">{label}</div>
        <div className="mt-0.5 flex items-baseline gap-1.5">
          <span className={`tnum text-xl font-semibold leading-none ${TONE_TEXT[tone]}`}>
            {value}
          </span>
          {unit && <span className="text-xs text-mist-500">{unit}</span>}
        </div>
      </div>
      {trend && <div className="w-28">{trend}</div>}
      {note && <div className="col-span-2 text-2xs text-mist-400">{note}</div>}
    </div>
  );
}

/** A horizontal fill bar; `ratio` is 0–1. Colour shifts as the limit nears. */
export function Meter({ ratio, tone = "neutral" }: { ratio: number; tone?: Tone }) {
  const fill =
    tone === "critical"
      ? "bg-sev-critical"
      : tone === "warn"
        ? "bg-sev-medium"
        : "bg-accent";
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-ink-800"
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(Math.min(Math.max(ratio, 0), 1) * 100)}
    >
      <div
        className={`h-full rounded-full transition-[width] duration-500 ${fill}`}
        style={{ width: `${Math.min(Math.max(ratio, 0), 1) * 100}%` }}
      />
    </div>
  );
}

/** Segmented control for short, mutually exclusive choices (e.g. a time range). */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className="seg-item"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Pill({
  children,
  tone = "info",
}: {
  children: ReactNode;
  tone?: "critical" | "high" | "medium" | "low" | "info" | "ok";
}) {
  const classes: Record<string, string> = {
    critical: "border-transparent bg-sev-critical/12 text-sev-critical",
    high: "border-transparent bg-sev-high/12 text-sev-high",
    medium: "border-transparent bg-sev-medium/12 text-sev-medium",
    low: "border-transparent bg-sev-low/12 text-sev-low",
    info: "border-transparent bg-ink-800 text-mist-300",
    ok: "border-transparent bg-sev-ok/12 text-sev-ok",
  };
  return <span className={`pill ${classes[tone]}`}>{children}</span>;
}

/**
 * A caveat attached to a figure.
 *
 * Used wherever a number is easy to over-trust — an estimate from the
 * statistics collector, a scan count over a short observation window. The
 * alternative is a dashboard that presents a guess with the same confidence
 * as a measurement.
 */
export function Caveat({ children }: { children: ReactNode }) {
  return (
    <p className="mt-3 border-l-2 border-accent/40 pl-3 text-2xs leading-relaxed text-mist-400">
      {children}
    </p>
  );
}

/** A grey shimmering placeholder shown while data loads. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden />;
}
