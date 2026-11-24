"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api } from "@/lib/api";
import { ToastProvider } from "./Toast";
import type { Connection } from "@/lib/types";

interface ConnectionCtx {
  connectionId: string | null;
  setConnectionId: (id: string) => void;
  connections: Connection[];
  current: Connection | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

const Ctx = createContext<ConnectionCtx>({
  connectionId: null,
  setConnectionId: () => {},
  connections: [],
  current: null,
  loading: true,
  error: null,
  refresh: () => {},
});

export const useConnection = () => useContext(Ctx);

const STORAGE_KEY = "dbopt.connection";

function ConnectionProvider({ children }: { children: ReactNode }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionId, setConnectionIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.connections
      .list()
      .then((rows) => {
        if (cancelled) return;
        setConnections(rows);
        setError(null);
        setConnectionIdState((previous) => {
          if (previous && rows.some((r) => r.id === previous)) return previous;
          // localStorage can throw in a locked-down browser, and the app must
          // still work without it.
          let stored: string | null = null;
          try {
            stored = window.localStorage.getItem(STORAGE_KEY);
          } catch {
            stored = null;
          }
          if (stored && rows.some((r) => r.id === stored)) return stored;
          return rows[0]?.id ?? null;
        });
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const setConnectionId = (id: string) => {
    setConnectionIdState(id);
    try {
      window.localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* non-fatal */
    }
  };

  const value = useMemo<ConnectionCtx>(
    () => ({
      connectionId,
      setConnectionId,
      connections,
      current: connections.find((c) => c.id === connectionId) ?? null,
      loading,
      error,
      refresh: () => setTick((t) => t + 1),
    }),
    [connectionId, connections, loading, error],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ConnectionProvider>{children}</ConnectionProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
