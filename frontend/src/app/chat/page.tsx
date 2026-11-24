"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useConnection } from "@/components/Providers";
import { EmptyState, ErrorNote, SectionTitle, PageHeader } from "@/components/Shell";
import { Pill } from "@/components/Stat";
import { api } from "@/lib/api";
import type { ChatStep } from "@/lib/types";

interface Turn {
  question: string;
  answer?: string;
  steps?: ChatStep[];
  error?: string | null;
  pending?: boolean;
}

const EXAMPLES = [
  "What is the slowest thing on this database right now, and why?",
  "Which tables are being sequentially scanned the most?",
  "Is autovacuum keeping up?",
  "Are there any indexes that are never used?",
  "Is anything blocked on a lock?",
];

export default function ConsolePage() {
  const { connectionId, current } = useConnection();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const config = useQuery({ queryKey: ["config"], queryFn: api.config });
  const tools = useQuery({
    queryKey: ["chatTools", connectionId],
    queryFn: () => api.chat.tools(connectionId!),
    enabled: !!connectionId,
  });

  const ask = useMutation({
    mutationFn: (q: string) => api.chat.ask(connectionId!, q, threadId),
    onMutate: (q) => {
      setTurns((prev) => [...prev, { question: q, pending: true }]);
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
    onSuccess: (data) => {
      setThreadId(data.thread_id);
      setTurns((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          question: next[next.length - 1].question,
          answer: data.answer,
          steps: data.steps,
          error: data.error ?? null,
        };
        return next;
      });
      setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    },
    onError: (error: Error) => {
      setTurns((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          question: next[next.length - 1].question,
          error: error.message,
        };
        return next;
      });
    },
  });

  const submit = () => {
    const q = question.trim();
    if (!q || ask.isPending) return;
    setQuestion("");
    ask.mutate(q);
  };

  if (!connectionId) {
    return (
      <EmptyState
        title="No database selected"
        body="Pick a database from the selector in the header."
      />
    );
  }

  if (config.data && !config.data.llm_enabled) {
    return (
      <EmptyState
        title="No model configured"
        body="The console needs an LLM provider. Set DBOPT_LLM_PROVIDER to ollama, anthropic or openai and restart the backend. Everything else in this application — the dashboard, slow-query analysis, index advice and EXPLAIN — works without one."
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-4">
      <div className="space-y-4 lg:col-span-3">
        <PageHeader
          title="Console"
          subtitle={`Ask a question in plain English. Answers come from live, read-only lookups against ${current?.name ?? "this database"}.`}
          right={
            config.data && (
              <Pill tone="info">{config.data.llm_provider}</Pill>
            )
          }
        />

        {turns.length === 0 && (
          <div className="card-pad">
            <p className="text-xs text-mist-400">
              Ask a question about this database. The console answers by calling
              read-only tools and quoting what they return — it cannot run
              arbitrary SQL and cannot change anything.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {EXAMPLES.map((e) => (
                <button
                  key={e}
                  type="button"
                  onClick={() => {
                    setQuestion("");
                    ask.mutate(e);
                  }}
                  className="btn-default h-auto min-h-9 whitespace-normal py-2 text-left"
                >
                  {e}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-4">
          {turns.map((turn, i) => (
            <div key={i} className="space-y-2">
              <div className="flex justify-end">
                <p className="max-w-[80%] rounded-lg rounded-br-sm border border-accent-dim/40 bg-accent/10 px-3 py-2 text-xs text-mist-100">
                  {turn.question}
                </p>
              </div>

              {turn.pending && (
                <div className="flex items-center gap-2 px-1 text-2xs text-mist-500">
                  <span className="h-3 w-3 animate-spin rounded-full border border-ink-600 border-t-accent" />
                  Querying the database…
                </div>
              )}

              {turn.steps && turn.steps.some((s) => s.kind === "tool_call") && (
                <details className="card px-3 py-2">
                  <summary className="label cursor-pointer">
                    {turn.steps.filter((s) => s.kind === "tool_call").length} tool
                    call(s) — show what was read
                  </summary>
                  <div className="mt-2 space-y-2">
                    {turn.steps
                      .filter((s) => s.kind === "tool_call")
                      .map((step, j) => (
                        <div key={j}>
                          <div className="font-mono text-2xs text-accent-glow">
                            {step.name}(
                            {Object.entries(step.arguments ?? {})
                              .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                              .join(", ")}
                            )
                          </div>
                          <pre className="code mt-1 max-h-48 overflow-auto">
                            {JSON.stringify(step.result, null, 2)}
                          </pre>
                        </div>
                      ))}
                  </div>
                </details>
              )}

              {turn.answer && (
                <div className="card-pad">
                  <p className="whitespace-pre-wrap text-xs leading-relaxed text-mist-200">
                    {turn.answer}
                  </p>
                </div>
              )}

              {turn.error && <ErrorNote message={turn.error} />}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="sticky bottom-4">
          <div className="card flex items-end gap-2 p-2">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
              rows={2}
              placeholder="Ask about this database…  (Enter to send, Shift+Enter for a newline)"
              className="min-h-[2.5rem] flex-1 resize-none border-0 bg-transparent px-2 py-1.5 text-xs text-mist-100 placeholder:text-mist-500 focus:outline-none"
            />
            <button
              type="button"
              onClick={submit}
              disabled={!question.trim() || ask.isPending}
              className="btn-primary"
            >
              Ask
            </button>
          </div>
        </div>
      </div>

      <div className="space-y-4">
        <div className="card-pad">
          <SectionTitle title="Available tools" />
          <pre className="whitespace-pre-wrap text-2xs leading-relaxed text-mist-400">
            {tools.data?.tools ?? "…"}
          </pre>
        </div>

        <div className="card-pad">
          <SectionTitle title="What it cannot do" />
          <ul className="space-y-2 text-2xs leading-relaxed text-mist-400">
            <li>
              Run arbitrary SQL. There is no such tool — each tool wraps one
              fixed, read-only query written in advance.
            </li>
            <li>
              Execute EXPLAIN ANALYZE. The console&apos;s explain tool plans
              without running the statement, even on connections where ANALYZE is
              permitted elsewhere.
            </li>
            <li>
              Change anything. Recommendations land in the suggestions queue for
              you to approve.
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
