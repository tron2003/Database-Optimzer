"use client";

import {
  ArcElement,
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions,
  type Plugin,
  type ScriptableContext,
} from "chart.js";
import { useMemo } from "react";
import { Doughnut, Line } from "react-chartjs-2";
import { clockTime } from "@/lib/format";
import { useChartTheme, withAlpha, type ChartTheme } from "@/lib/theme";

ChartJS.register(
  ArcElement,
  CategoryScale,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
);


export interface Point {
  captured_at: string;
  [key: string]: number | string;
}

// ---------------------------------------------------------------------------
// Plugins
// ---------------------------------------------------------------------------

/** A hairline that follows the pointer, so values can be read against the x axis. */
const crosshairFor = (t: ChartTheme): Plugin<"line"> => ({
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements?.();
    if (!active?.length) return;
    const { ctx, chartArea } = chart;
    const x = active[0].element.x;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = t.crosshair;
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.restore();
  },
});

/** A soft halo on the line itself — used on the one hero chart only. */
const glow = (color: string): Plugin<"line"> => ({
  id: "glow",
  beforeDatasetDraw(chart) {
    chart.ctx.save();
    chart.ctx.shadowColor = withAlpha(color, 0.65);
    chart.ctx.shadowBlur = 14;
  },
  afterDatasetDraw(chart) {
    chart.ctx.restore();
  },
});

// ---------------------------------------------------------------------------
// Shared options
// ---------------------------------------------------------------------------

function fillGradient(color: string, top = 0.3) {
  return (context: ScriptableContext<"line">) => {
    const { chart } = context;
    const area = chart.chartArea;
    if (!area) return withAlpha(color, 0.12);
    const gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    gradient.addColorStop(0, withAlpha(color, top));
    gradient.addColorStop(1, withAlpha(color, 0));
    return gradient;
  };
}

function axisNumber(value: number | string, formatter?: (v: number) => string) {
  const n = Number(value);
  if (formatter) return formatter(n);
  if (Math.abs(n) >= 1000)
    return Intl.NumberFormat(undefined, { notation: "compact" }).format(n);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

function baseOptions(t: ChartTheme, opts: {
  formatter?: (v: number) => string;
  axisFormatter?: (v: number) => string;
  min?: number;
  max?: number;
  stacked?: boolean;
  showTitle?: boolean;
}): ChartOptions<"line"> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false, // live data: redraws every few seconds must not wobble
    interaction: { mode: "index", intersect: false },
    layout: { padding: { top: 8, right: 4 } },
    elements: {
      point: { radius: 0, hoverRadius: 4.5, hitRadius: 24, hoverBorderWidth: 2 },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: {
          color: t.axis,
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 6,
          padding: 8,
          font: { size: 11, family: t.fontFamily },
        },
      },
      y: {
        stacked: opts.stacked,
        min: opts.min,
        max: opts.max,
        beginAtZero: opts.min === undefined,
        grid: { color: t.grid, drawTicks: false },
        border: { display: false },
        ticks: {
          color: t.axis,
          maxTicksLimit: 5,
          padding: 10,
          font: { size: 11, family: t.fontFamily },
          callback: (value) => axisNumber(value, opts.axisFormatter ?? opts.formatter),
        },
      },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        borderWidth: 1,
        padding: 12,
        cornerRadius: 10,
        displayColors: true,
        usePointStyle: true,
        boxWidth: 8,
        boxHeight: 8,
        boxPadding: 6,
        titleColor: t.axis,
        titleFont: { size: 11, weight: 400, family: t.fontFamily },
        bodyColor: t.textStrong,
        bodyFont: { size: 12, weight: 500, family: t.fontFamily },
        callbacks: {
          label: (item) => {
            const v = item.parsed.y ?? 0;
            const text = opts.formatter ? opts.formatter(v) : String(v);
            return ` ${item.dataset.label ?? ""}   ${text}`;
          },
        },
      },
    },
  };
}

const labelsOf = (data: Point[]) => data.map((p) => clockTime(p.captured_at));

// ---------------------------------------------------------------------------
// Charts
// ---------------------------------------------------------------------------

export function LiveAreaChart({
  data,
  dataKey,
  color: colorProp,
  name,
  height = 160,
  formatter,
  axisFormatter,
  min,
  max,
  hero = false,
}: {
  data: Point[];
  dataKey: string;
  color?: string;
  name: string;
  height?: number;
  formatter?: (value: number) => string;
  axisFormatter?: (value: number) => string;
  min?: number;
  max?: number;
  /** Adds the glow; reserve for the single most important chart on a page. */
  hero?: boolean;
}) {
  const t = useChartTheme();
  const color = colorProp ?? t.series.blue;
  const chartData = useMemo<ChartData<"line">>(
    () => ({
      labels: labelsOf(data),
      datasets: [
        {
          label: name,
          data: data.map((p) => Number(p[dataKey] ?? 0)),
          borderColor: color,
          borderWidth: hero ? 2.25 : 2,
          borderJoinStyle: "round",
          backgroundColor: fillGradient(color, hero ? 0.38 : 0.26),
          fill: true,
          tension: 0.35,
          pointHoverBackgroundColor: color,
          pointHoverBorderColor: t.surface,
        },
      ],
    }),
    [data, dataKey, name, color, hero, t.surface],
  );

  const options = useMemo(
    () => baseOptions(t, { formatter, axisFormatter, min, max }),
    [t, formatter, axisFormatter, min, max],
  );
  const plugins = useMemo(
    () => (hero ? [crosshairFor(t), glow(color)] : [crosshairFor(t)]),
    [hero, color, t],
  );

  return (
    <div style={{ height }} role="img" aria-label={`${name} over time`}>
      <Line data={chartData} options={options} plugins={plugins} />
    </div>
  );
}

/**
 * Stacked areas: the layers sum to the total, so composition and size read in
 * one shape. Series are drawn bottom-up in the order given.
 */
export function StackedAreaChart({
  data,
  series,
  height = 160,
  formatter,
}: {
  data: Point[];
  series: { key: string; name: string; color: string }[];
  height?: number;
  formatter?: (value: number) => string;
}) {
  const t = useChartTheme();
  const chartData = useMemo<ChartData<"line">>(
    () => ({
      labels: labelsOf(data),
      datasets: series.map((s) => ({
        label: s.name,
        data: data.map((p) => Number(p[s.key] ?? 0)),
        borderColor: s.color,
        borderWidth: 1.5,
        backgroundColor: withAlpha(s.color, t.mode === "dark" ? 0.62 : 0.5),
        fill: true,
        tension: 0.3,
        pointHoverBackgroundColor: s.color,
        pointHoverBorderColor: t.surface,
      })),
    }),
    [data, series, t.surface, t.mode],
  );

  const options = useMemo(() => baseOptions(t, { formatter, stacked: true }), [t, formatter]);

  return (
    <div style={{ height }} role="img" aria-label="Stacked chart over time">
      <Line data={chartData} options={options} plugins={[crosshairFor(t)]} />
    </div>
  );
}

/** A tiny trend line with no axes, for use beside a number. */
export function Sparkline({
  values,
  color: colorProp,
  height = 34,
}: {
  values: number[];
  color?: string;
  height?: number;
}) {
  const t = useChartTheme();
  const color = colorProp ?? t.series.blue;
  const chartData = useMemo<ChartData<"line">>(
    () => ({
      labels: values.map((_, i) => String(i)),
      datasets: [
        {
          data: values,
          borderColor: color,
          borderWidth: 1.75,
          backgroundColor: fillGradient(color, 0.22),
          fill: true,
          tension: 0.35,
        },
      ],
    }),
    [values, color],
  );

  const options = useMemo<ChartOptions<"line">>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      events: [],
      layout: { padding: 1 },
      elements: { point: { radius: 0 } },
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    }),
    [],
  );

  return (
    <div style={{ height }} aria-hidden>
      <Line data={chartData} options={options} />
    </div>
  );
}

/** A three-quarter ring gauge with the value overlaid in the centre. */
export function ScoreRing({
  value,
  color,
  label,
  size = 108,
}: {
  value: number;
  color: string;
  label?: string;
  size?: number;
}) {
  const t = useChartTheme();
  const clamped = Math.max(0, Math.min(100, value));
  const data = useMemo<ChartData<"doughnut">>(
    () => ({
      labels: ["Score", "Remaining"],
      datasets: [
        {
          data: [clamped, 100 - clamped],
          backgroundColor: [color, t.ringTrack],
          borderWidth: 0,
          borderRadius: 6,
        },
      ],
    }),
    [clamped, color, t.ringTrack],
  );

  const options = useMemo<ChartOptions<"doughnut">>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      cutout: "80%",
      rotation: 225,
      circumference: 270,
      events: [],
      animation: { duration: 700, easing: "easeOutQuart" },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
    }),
    [],
  );

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Health score ${clamped} out of 100`}
    >
      <Doughnut data={data} options={options} />
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tnum text-xl font-semibold leading-none text-mist-100">
          {Math.round(clamped)}
        </span>
        {label && <span className="mt-1 text-2xs text-mist-400">{label}</span>}
      </div>
    </div>
  );
}

export function ChartLegend({
  items,
}: {
  items: { name: string; color: string }[];
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <span key={item.name} className="flex items-center gap-1.5 text-2xs text-mist-400">
          <span className="h-2 w-2 rounded-full" style={{ background: item.color }} />
          {item.name}
        </span>
      ))}
    </div>
  );
}
