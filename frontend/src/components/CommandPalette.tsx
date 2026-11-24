"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon, type IconName } from "./Icons";
import { useConnection } from "./Providers";
import { setMode, useMode } from "@/lib/theme";

interface Command {
  id: string;
  label: string;
  hint: string;
  icon: IconName;
  run: () => void;
}

/**
 * Ctrl/Cmd+K: jump anywhere or switch database without reaching for the mouse.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const mode = useMode();
  const { connections, setConnectionId } = useConnection();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (href: string) => () => router.push(href);
    return [
      { id: "overview", label: "Overview", hint: "Live health and what needs attention", icon: "overview", run: go("/") },
      { id: "queries", label: "Queries", hint: "Slowest statements and the EXPLAIN workbench", icon: "queries", run: go("/queries") },
      { id: "schema", label: "Schema", hint: "Indexes, tables and unused or duplicate indexes", icon: "schema", run: go("/schema") },
      { id: "suggestions", label: "Suggestions", hint: "Review and approve recommended changes", icon: "suggestions", run: go("/suggestions") },
      { id: "chat", label: "Console", hint: "Ask questions about this database", icon: "console", run: go("/chat") },
      { id: "connections", label: "Connections", hint: "Add or manage databases", icon: "connections", run: go("/connections") },
      { id: "help", label: "How it works", hint: "What this tool does and why it is safe", icon: "book", run: go("/help") },
      {
        id: "theme",
        label: mode === "dark" ? "Switch to light theme" : "Switch to dark theme",
        hint: "Appearance",
        icon: mode === "dark" ? "sun" : "moon",
        run: () => setMode(mode === "dark" ? "light" : "dark"),
      },
      ...connections.map<Command>((c) => ({
        id: `db-${c.id}`,
        label: `Switch to ${c.name}`,
        hint: c.kind === "sandbox" ? "Sandbox database" : "Your database",
        icon: "schema",
        run: () => setConnectionId(c.id),
      })),
    ];
  }, [router, mode, connections, setConnectionId]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => `${c.label} ${c.hint}`.toLowerCase().includes(q));
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      // After paint so the input exists.
      window.setTimeout(() => input.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => setIndex(0), [query]);

  if (!open) return null;

  const choose = (c: Command | undefined) => {
    if (!c) return;
    onClose();
    c.run();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[14vh]">
      <div
        className="absolute inset-0 animate-fade-in bg-[rgb(8_12_24/0.5)] backdrop-blur-[3px]"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="relative w-full max-w-xl animate-rise-in overflow-hidden rounded-3xl bg-ink-900 shadow-float"
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          else if (e.key === "ArrowDown") {
            e.preventDefault();
            setIndex((i) => Math.min(i + 1, results.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setIndex((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            choose(results[index]);
          }
        }}
      >
        <div className="flex items-center gap-3 border-b border-ink-700 px-5">
          <Icon name="search" className="h-5 w-5 text-mist-500" />
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search pages and actions"
            aria-label="Search pages and actions"
            className="h-14 w-full bg-transparent text-sm text-mist-100 placeholder:text-mist-500 focus:outline-none"
          />
          <kbd className="rounded-md bg-ink-800 px-1.5 py-0.5 font-mono text-[11px] text-mist-400">Esc</kbd>
        </div>
        <ul className="max-h-[50vh] overflow-y-auto p-2" role="listbox">
          {results.length === 0 && (
            <li className="px-4 py-8 text-center text-xs text-mist-400">
              Nothing matches &ldquo;{query}&rdquo;.
            </li>
          )}
          {results.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === index}>
              <button
                type="button"
                onMouseMove={() => setIndex(i)}
                onClick={() => choose(c)}
                className={`flex w-full items-center gap-3 rounded-2xl px-3 py-2.5 text-left transition-colors ${
                  i === index ? "bg-ink-800" : ""
                }`}
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent-glow">
                  <Icon name={c.icon} className="h-[18px] w-[18px]" />
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-semibold text-mist-100">{c.label}</span>
                  <span className="block truncate text-2xs text-mist-400">{c.hint}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
