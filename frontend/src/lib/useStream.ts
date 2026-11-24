"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "./api";

export interface StreamMetrics {
  connection_id: string;
  at: string;
  metrics: Record<string, number>;
  rates: Record<string, number>;
  top_statements: {
    queryid: number;
    query: string;
    mean_exec_ms: number;
    delta_calls: number;
    delta_total_exec_ms: number;
  }[];
}

type Status = "connecting" | "live" | "error";

/**
 * Subscribe to the collector's SSE stream.
 *
 * The browser reconnects an EventSource on its own, so there is no retry loop
 * here. What this does add is a staleness check: a connection that is open but
 * silent looks identical to a healthy one from the EventSource API, and on a
 * monitoring dashboard "the numbers stopped moving" must be visible rather
 * than quietly showing stale values forever.
 */
export function useMetricsStream(connectionId: string | null, staleAfterMs = 30_000) {
  const [latest, setLatest] = useState<StreamMetrics | null>(null);
  const [history, setHistory] = useState<StreamMetrics[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [lastEventAt, setLastEventAt] = useState<number>(() => Date.now());
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!connectionId) return;

    setStatus("connecting");
    const source = new EventSource(api.streamUrl(connectionId));
    sourceRef.current = source;

    const onMetrics = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as StreamMetrics;
        setLatest(parsed);
        setHistory((prev) => [...prev.slice(-179), parsed]);
        setStatus("live");
        setLastEventAt(Date.now());
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    };

    const onPing = () => {
      setStatus("live");
      setLastEventAt(Date.now());
    };

    source.addEventListener("metrics", onMetrics as EventListener);
    source.addEventListener("ping", onPing as EventListener);
    source.onopen = () => {
      setStatus("live");
      setLastEventAt(Date.now());
    };
    source.onerror = () => setStatus("error");

    return () => {
      source.removeEventListener("metrics", onMetrics as EventListener);
      source.removeEventListener("ping", onPing as EventListener);
      source.close();
      sourceRef.current = null;
    };
  }, [connectionId]);

  // Reset history when switching databases; mixing two targets' series would
  // produce a chart that is quietly nonsense.
  useEffect(() => {
    setHistory([]);
    setLatest(null);
  }, [connectionId]);

  const [isStale, setIsStale] = useState(false);
  useEffect(() => {
    const timer = setInterval(() => {
      setIsStale(Date.now() - lastEventAt > staleAfterMs);
    }, 2000);
    return () => clearInterval(timer);
  }, [lastEventAt, staleAfterMs]);

  return { latest, history, status, isStale, lastEventAt };
}
