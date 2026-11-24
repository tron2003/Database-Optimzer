"use client";

import { useSyncExternalStore } from "react";

/**
 * Theme state and the Chart.js palette.
 *
 * The page theme lives on <html data-theme>. Chart.js draws to a canvas and
 * cannot read CSS variables, so the chart colours are mirrored here and picked
 * by the current mode. Keep the series values in step with globals.css; both
 * sets were run through the palette validator against their own surface.
 */
export type Mode = "light" | "dark";

export interface ChartTheme {
  mode: Mode;
  surface: string;
  grid: string;
  axis: string;
  text: string;
  textStrong: string;
  crosshair: string;
  tooltipBg: string;
  tooltipBorder: string;
  ringTrack: string;
  series: { blue: string; orange: string; aqua: string; violet: string };
  status: { ok: string; warn: string; critical: string };
  fontFamily: string;
}

const FONT = '"Figtree Variable", ui-sans-serif, system-ui, sans-serif';

const LIGHT: ChartTheme = {
  mode: "light",
  surface: "#ffffff",
  grid: "rgba(16, 24, 40, 0.07)",
  axis: "#667085",
  text: "#334155",
  textStrong: "#0f1729",
  crosshair: "rgba(15, 23, 41, 0.30)",
  tooltipBg: "rgba(255, 255, 255, 0.98)",
  tooltipBorder: "rgba(16, 24, 40, 0.10)",
  ringTrack: "rgba(16, 24, 40, 0.07)",
  series: { blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a", violet: "#6250d6" },
  status: { ok: "#1fa463", warn: "#eda100", critical: "#e34948" },
  fontFamily: FONT,
};

const DARK: ChartTheme = {
  mode: "dark",
  surface: "#11151f",
  grid: "rgba(154, 163, 184, 0.11)",
  axis: "#7d869b",
  text: "#d9deea",
  textStrong: "#f0f2f7",
  crosshair: "rgba(217, 222, 234, 0.30)",
  tooltipBg: "rgba(23, 28, 41, 0.97)",
  tooltipBorder: "rgba(255, 255, 255, 0.10)",
  ringTrack: "rgba(154, 163, 184, 0.14)",
  series: { blue: "#3987e5", orange: "#d95926", aqua: "#199e70", violet: "#9085e9" },
  status: { ok: "#43c28a", warn: "#e8c04a", critical: "#f0556d" },
  fontFamily: FONT,
};

const KEY = "dbopt.theme";

export function getMode(): Mode {
  if (typeof document === "undefined") return "light";
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

export function useMode(): Mode {
  return useSyncExternalStore(subscribe, getMode, () => "light");
}

export function useChartTheme(): ChartTheme {
  return useMode() === "dark" ? DARK : LIGHT;
}

export function setMode(mode: Mode) {
  document.documentElement.dataset.theme = mode;
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    /* non-fatal: the choice just won't persist */
  }
}

/** `#rrggbb` + alpha → `rgba(...)`. */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
