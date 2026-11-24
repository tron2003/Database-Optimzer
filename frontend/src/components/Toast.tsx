"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Icon } from "./Icons";

type Tone = "success" | "error" | "info";

interface ToastItem {
  id: number;
  tone: Tone;
  title: string;
  body?: string;
}

interface ToastApi {
  push: (toast: { tone?: Tone; title: string; body?: string }) => void;
}

const Ctx = createContext<ToastApi>({ push: () => {} });
export const useToast = () => useContext(Ctx);

const STYLES: Record<Tone, { ring: string; icon: string; name: "check" | "alert" | "info" }> = {
  success: { ring: "text-sev-ok bg-sev-ok/12", icon: "text-sev-ok", name: "check" },
  error: { ring: "text-sev-critical bg-sev-critical/12", icon: "text-sev-critical", name: "alert" },
  info: { ring: "text-accent bg-accent/12", icon: "text-accent", name: "info" },
};

/** Confirmations that appear after an action and leave on their own. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback(
    (id: number) => setItems((all) => all.filter((t) => t.id !== id)),
    [],
  );

  const push = useCallback<ToastApi["push"]>(
    ({ tone = "info", title, body }) => {
      const id = ++seq.current;
      setItems((all) => [...all.slice(-3), { id, tone, title, body }]);
      // Errors stay longer: they usually need reading twice.
      window.setTimeout(() => dismiss(id), tone === "error" ? 9000 : 4500);
    },
    [dismiss],
  );

  const api = useMemo(() => ({ push }), [push]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(24rem,calc(100vw-2.5rem))] flex-col gap-2"
        role="region"
        aria-live="polite"
        aria-label="Notifications"
      >
        {items.map((t) => {
          const s = STYLES[t.tone];
          return (
            <div
              key={t.id}
              role={t.tone === "error" ? "alert" : "status"}
              className="pointer-events-auto flex animate-rise-in gap-3 rounded-2xl bg-ink-900 p-3.5 shadow-float"
            >
              <span
                className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${s.ring}`}
              >
                <Icon name={s.name} className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-mist-100">{t.title}</p>
                {t.body && (
                  <p className="mt-0.5 break-words text-2xs leading-relaxed text-mist-400">
                    {t.body}
                  </p>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => dismiss(t.id)}
                className="self-start rounded-full p-1 text-mist-500 hover:bg-ink-800 hover:text-mist-100"
              >
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}
