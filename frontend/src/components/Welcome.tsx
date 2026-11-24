"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "./Icons";

const KEY = "dbopt.welcome.dismissed";

const STEPS = [
  {
    title: "Watch",
    body: "Live health, throughput and connections update every few seconds.",
  },
  {
    title: "Find",
    body: "Run an analysis to spot missing, duplicate and unused indexes and slow queries.",
  },
  {
    title: "Review",
    body: "Every suggestion shows the exact SQL, what it costs, and how to undo it.",
  },
  {
    title: "Approve",
    body: "Nothing runs until you say so, and every change is recorded in an audit log.",
  },
];

/**
 * A first-run orientation. Shown once, dismissible, and always reachable again
 * from the Help page — a new user should know the four steps before touching
 * anything, and a returning one should never have to see it twice.
 */
export function Welcome() {
  // Start hidden so a returning user never sees it flash on load.
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      setVisible(window.localStorage.getItem(KEY) !== "1");
    } catch {
      setVisible(true);
    }
  }, []);

  if (!visible) return null;

  const dismiss = () => {
    setVisible(false);
    try {
      window.localStorage.setItem(KEY, "1");
    } catch {
      /* non-fatal */
    }
  };

  return (
    <section className="card animate-rise-in p-6 sm:p-7" aria-labelledby="welcome-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="welcome-title" className="text-xl font-semibold text-mist-100">
            Welcome. Here is how this works.
          </h2>
          <p className="mt-1 max-w-xl text-sm text-mist-400">
            This tool watches your PostgreSQL database and suggests fixes. It never changes
            anything on its own.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss welcome"
          className="rounded-full p-2 text-mist-400 hover:bg-ink-800 hover:text-mist-100"
        >
          <Icon name="x" className="h-4 w-4" />
        </button>
      </div>

      <ol className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {STEPS.map((step, i) => (
          <li key={step.title} className="rounded-2xl bg-ink-850 p-4">
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs font-bold text-accent-ink">
              {i + 1}
            </span>
            <h3 className="mt-3 text-sm font-semibold text-mist-100">{step.title}</h3>
            <p className="mt-1 text-2xs leading-relaxed text-mist-400">{step.body}</p>
          </li>
        ))}
      </ol>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <Link href="/suggestions" className="btn-primary" onClick={dismiss}>
          Find optimizations
        </Link>
        <Link href="/help" className="btn-default">
          How it works
        </Link>
        <button type="button" onClick={dismiss} className="btn border-transparent text-mist-400 hover:text-mist-100">
          Got it
        </button>
      </div>
    </section>
  );
}
