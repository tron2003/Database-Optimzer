"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { CommandPalette } from "./CommandPalette";
import { Icon, LogoMark, type IconName } from "./Icons";
import { useConnection } from "./Providers";
import { relativeTime } from "@/lib/format";
import { setMode, useMode } from "@/lib/theme";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Overview", icon: "overview" },
  { href: "/queries", label: "Queries", icon: "queries" },
  { href: "/schema", label: "Schema", icon: "schema" },
  { href: "/suggestions", label: "Suggestions", icon: "suggestions" },
  { href: "/chat", label: "Console", icon: "console" },
  { href: "/connections", label: "Connections", icon: "connections" },
];

/** A dot that pulses outward while data is arriving. */
export function LiveDot({ tone }: { tone: "live" | "warn" | "error" | "idle" }) {
  const color = {
    live: "bg-sev-ok",
    warn: "bg-sev-high",
    error: "bg-sev-critical",
    idle: "bg-mist-500",
  }[tone];
  return (
    <span className="relative flex h-2 w-2" aria-hidden>
      {tone === "live" && (
        <span className={`absolute inset-0 animate-pulse-ring rounded-full ${color}`} />
      )}
      <span className={`relative h-2 w-2 rounded-full ${color}`} />
    </span>
  );
}

function ThemeToggle() {
  const mode = useMode();
  const next = mode === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setMode(next)}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="flex h-9 w-9 items-center justify-center rounded-full text-mist-400 transition-colors hover:bg-ink-800 hover:text-mist-100"
    >
      <Icon name={mode === "dark" ? "sun" : "moon"} className="h-[18px] w-[18px]" />
    </button>
  );
}

/** The current database, as a chip with a live status dot. */
function ConnectionChip() {
  const { connections, connectionId, setConnectionId, current } = useConnection();

  if (connections.length === 0) {
    return (
      <Link href="/connections" className="btn-primary h-9">
        Add a database
      </Link>
    );
  }

  const tone = current?.last_error ? "error" : current?.collector_running ? "live" : "idle";

  return (
    <label
      className="relative flex h-9 items-center gap-2 rounded-full bg-ink-800 pl-3 pr-2 text-xs font-semibold text-mist-100"
      title={
        current?.last_error
          ? `Collector error: ${current.last_error}`
          : current
            ? `Polled ${relativeTime(current.last_polled_at)}`
            : undefined
      }
    >
      <LiveDot tone={tone} />
      <span className="sr-only">Database</span>
      <select
        value={connectionId ?? ""}
        onChange={(e) => setConnectionId(e.target.value)}
        className="max-w-[9.5rem] cursor-pointer appearance-none truncate bg-transparent pr-5 text-xs font-semibold text-mist-100 focus:outline-none"
      >
        {connections.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
            {c.kind === "sandbox" ? " (sandbox)" : ""}
          </option>
        ))}
      </select>
      <svg viewBox="0 0 12 12" className="pointer-events-none absolute right-3 h-2.5 w-2.5 text-mist-400" aria-hidden>
        <path d="M2.5 4.5L6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </label>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [palette, setPalette] = useState(false);
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[70] focus:rounded-full focus:bg-accent focus:px-4 focus:py-2 focus:text-xs focus:font-semibold focus:text-accent-ink"
      >
        Skip to content
      </a>

      <header className="sticky top-0 z-30 px-3 pt-3 sm:px-6 sm:pt-4">
        <div className="mx-auto flex max-w-[1380px] items-center gap-2 rounded-full bg-ink-900/90 p-1.5 pl-3 shadow-float backdrop-blur-xl sm:gap-3">
          <Link href="/" className="flex shrink-0 items-center gap-2.5 pr-1" aria-label="Database Optimizer home">
            <LogoMark className="h-8 w-8" />
            <span className="hidden font-display text-sm font-semibold leading-none text-mist-100 xl:block">
              Database Optimizer
            </span>
          </Link>

          <nav className="hidden flex-1 items-center justify-center gap-0.5 lg:flex" aria-label="Main">
            {NAV.map((item) => {
              const active = isActive(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-2 rounded-full px-3.5 py-2 text-xs font-semibold transition-colors ${
                    active
                      ? "bg-mist-100 text-ink-900"
                      : "text-mist-400 hover:bg-ink-800 hover:text-mist-100"
                  }`}
                >
                  <Icon name={item.icon} className="h-4 w-4" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-1 lg:ml-0">
            <button
              type="button"
              onClick={() => setPalette(true)}
              className="hidden h-9 items-center gap-2 rounded-full bg-ink-800 pl-3 pr-2 text-xs text-mist-400 transition-colors hover:text-mist-100 md:flex"
              aria-label="Open command palette"
            >
              <Icon name="search" className="h-4 w-4" />
              Search
              <kbd className="rounded-md bg-ink-900 px-1.5 py-0.5 font-mono text-[11px]">Ctrl K</kbd>
            </button>
            <Link
              href="/help"
              aria-label="How it works"
              title="How it works"
              className={`flex h-9 w-9 items-center justify-center rounded-full transition-colors hover:bg-ink-800 hover:text-mist-100 ${
                pathname === "/help" ? "bg-ink-800 text-mist-100" : "text-mist-400"
              }`}
            >
              <Icon name="help" className="h-[18px] w-[18px]" />
            </Link>
            <ThemeToggle />
            <ConnectionChip />
          </div>
        </div>

        {/* Navigation below the large breakpoint: a scrolling row of pills. */}
        <nav
          className="mx-auto mt-2 flex max-w-[1380px] gap-1 overflow-x-auto pb-1 lg:hidden"
          aria-label="Main"
        >
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={`flex shrink-0 items-center gap-2 rounded-full px-3.5 py-2 text-xs font-semibold ${
                isActive(item.href)
                  ? "bg-mist-100 text-ink-900"
                  : "bg-ink-900/80 text-mist-400 shadow-panel"
              }`}
            >
              <Icon name={item.icon} className="h-4 w-4" />
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <main
        id="main"
        className="mx-auto max-w-[1380px] px-3 pb-16 pt-6 sm:px-6 lg:pt-8"
      >
        {children}
      </main>

      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-20 text-center">
      <LogoMark className="mb-2 h-14 w-14" />
      <h2 className="text-xl font-semibold text-mist-100">{title}</h2>
      <p className="max-w-md text-sm leading-relaxed text-mist-400">{body}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-2xl bg-sev-critical/[0.08] px-4 py-3 ring-1 ring-inset ring-sev-critical/25"
    >
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-sev-critical" />
      <p className="text-xs leading-relaxed text-sev-critical">{message}</p>
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 px-1 py-6 text-xs text-mist-400" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-accent" />
      {label}…
    </div>
  );
}

export function SectionTitle({
  title,
  hint,
  right,
}: {
  title: ReactNode;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-lg font-semibold text-mist-100">{title}</h2>
        {hint && <p className="mt-1 max-w-prose text-xs text-mist-400">{hint}</p>}
      </div>
      {right}
    </div>
  );
}

/** Page title block used at the top of every page. */
export function PageHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-3xl font-semibold text-mist-100">{title}</h1>
        {subtitle && <p className="mt-2 max-w-2xl text-sm text-mist-400">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}
