"use client";

import Link from "next/link";
import { Icon, type IconName } from "@/components/Icons";
import { PageHeader } from "@/components/Shell";
import { GLOSSARY } from "@/lib/glossary";

const PIPELINE: { icon: IconName; title: string; body: string }[] = [
  {
    icon: "overview",
    title: "Collect",
    body: "Every few seconds the backend reads PostgreSQL's own statistics views: connections, locks, cache use and per-query timings. It stores snapshots so you can see trends.",
  },
  {
    icon: "queries",
    title: "Analyze",
    body: "Plain code, not a language model, looks for problems: missing and duplicate indexes, slow plans, stale statistics and bloat.",
  },
  {
    icon: "suggestions",
    title: "Propose",
    body: "Each finding becomes a suggestion with the exact SQL, what it should improve, what it costs, and how to undo it. A model can explain and rank them, but it never writes SQL that runs.",
  },
  {
    icon: "check",
    title: "Approve and run",
    body: "You review and approve. The statement is checked again, recorded in the audit log, run once, and the result is saved.",
  },
];

const SAFETY: { title: string; body: string }[] = [
  {
    title: "Two separate logins",
    body: "Monitoring uses a read-only login with short timeouts. A second login that can change things is used only when you approve a change, for one statement, then closed.",
  },
  {
    title: "A short allowlist",
    body: "Only creating or dropping a regular index, analyzing or vacuuming a named table, and adjusting statistics are allowed. Anything else is refused even if you approve it. A statement that cannot be parsed is also refused.",
  },
  {
    title: "You always decide",
    body: "There is no automatic mode. Every change needs your explicit approval, and every attempt is written to the audit log before it runs.",
  },
  {
    title: "The console cannot run SQL",
    body: "The chat console answers questions through nine fixed read-only lookups. It has no tool for running arbitrary SQL.",
  },
  {
    title: "Private by default",
    body: "When a cloud model is used, literal values in queries are stripped before anything leaves your machine. With a local model, nothing leaves at all.",
  },
];

export default function HelpPage() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader
        title="How it works"
        subtitle="What this tool does, why it is safe to point at a database, and what the technical terms mean."
      />

      <section className="card-pad">
        <h2 className="text-lg font-semibold text-mist-100">The short version</h2>
        <p className="mt-2 text-sm leading-relaxed text-mist-300">
          Database Optimizer watches a PostgreSQL database, works out why it might be slow, and
          suggests specific fixes. You stay in control: it proposes, you approve, and only then does
          anything change.
        </p>
        <ol className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {PIPELINE.map((step, i) => (
            <li key={step.title} className="rounded-2xl bg-ink-850 p-4">
              <div className="flex items-center gap-3">
                <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent-glow">
                  <Icon name={step.icon} className="h-[18px] w-[18px]" />
                </span>
                <h3 className="text-sm font-semibold text-mist-100">
                  {i + 1}. {step.title}
                </h3>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-mist-400">{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="card-pad">
        <h2 className="text-lg font-semibold text-mist-100">Why there is a sandbox</h2>
        <p className="mt-2 text-sm leading-relaxed text-mist-300">
          A monitoring tool needs a database with real problems and real traffic to be worth
          trying. The bundled sandbox is a small shop database with problems planted on purpose, such as
          a missing foreign-key index and a duplicate index, plus a traffic generator that keeps it
          busy. You can approve changes and watch queries get faster without risking anything that matters.
        </p>
        <p className="mt-3 text-sm leading-relaxed text-mist-300">
          Once you trust how it behaves, add your own database on the{" "}
          <Link href="/connections" className="font-semibold text-accent-glow underline">
            Connections page
          </Link>
          . The same safety rules apply there.
        </p>
      </section>

      <section className="card-pad">
        <h2 className="text-lg font-semibold text-mist-100">How changes stay safe</h2>
        <ul className="mt-4 divide-y divide-ink-700/70">
          {SAFETY.map((item) => (
            <li key={item.title} className="flex gap-3 py-3.5 first:pt-0 last:pb-0">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-sev-ok/12 text-sev-ok">
                <Icon name="check" className="h-3.5 w-3.5" />
              </span>
              <div>
                <h3 className="text-xs font-semibold text-mist-100">{item.title}</h3>
                <p className="mt-0.5 text-xs leading-relaxed text-mist-400">{item.body}</p>
              </div>
            </li>
          ))}
        </ul>
        <p className="mt-5 rounded-2xl bg-sev-medium/[0.09] p-3.5 text-xs leading-relaxed text-mist-200">
          <span className="font-semibold text-sev-medium">Good to know: </span>
          this is built for use on your own machine. It has no sign-in, so do not expose port 8000
          or 3000 to a network.
        </p>
      </section>

      <section className="card-pad">
        <h2 className="text-lg font-semibold text-mist-100">Turning on per-query data</h2>
        <p className="mt-2 text-sm leading-relaxed text-mist-300">
          The Queries page needs the <span className="font-mono text-xs">pg_stat_statements</span>{" "}
          extension. The bundled sandbox has it already. On your own server, add it to{" "}
          <span className="font-mono text-xs">shared_preload_libraries</span>, restart PostgreSQL,
          then run:
        </p>
        <pre className="code mt-3">CREATE EXTENSION IF NOT EXISTS pg_stat_statements;</pre>
      </section>

      <section className="card-pad">
        <h2 className="text-lg font-semibold text-mist-100">Plain-English glossary</h2>
        <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
          {Object.values(GLOSSARY).map((term) => (
            <div key={term.title}>
              <dt className="text-xs font-semibold text-mist-100">{term.title}</dt>
              <dd className="mt-1 text-xs leading-relaxed text-mist-400">{term.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      <div className="flex flex-wrap items-center gap-3 pb-4">
        <button
          type="button"
          className="btn-default"
          onClick={() => {
            try {
              window.localStorage.removeItem("dbopt.welcome.dismissed");
            } catch {
              /* non-fatal */
            }
            window.location.assign("/");
          }}
        >
          Show the welcome walkthrough again
        </button>
        <Link href="/" className="btn-primary">
          Back to overview
        </Link>
      </div>
    </div>
  );
}
