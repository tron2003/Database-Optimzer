import type { Config } from "tailwindcss";

/**
 * Design tokens.
 *
 * Direction: a bright, airy "lab report" look. Porcelain canvas, white panels
 * lifted by soft shadow, one confident blue, and type with some character:
 * Bricolage Grotesque for headlines and big numbers, Figtree for reading.
 *
 * Every colour is a CSS variable (see globals.css) so light and dark are two
 * value sets for the same names. The `ink` scale is surfaces (950 page → 600
 * strong line) and `mist` is text (100 strongest → 500 quietest) — they keep
 * their names in both themes, which is why pages did not need rewriting.
 */
const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: v("ink-950"),
          900: v("ink-900"),
          850: v("ink-850"),
          800: v("ink-800"),
          700: v("ink-700"),
          600: v("ink-600"),
        },
        mist: {
          500: v("mist-500"),
          400: v("mist-400"),
          300: v("mist-300"),
          200: v("mist-200"),
          100: v("mist-100"),
        },
        accent: {
          DEFAULT: v("accent"),
          dim: v("accent-dim"),
          glow: v("accent-glow"),
          ink: v("accent-ink"),
          soft: v("accent-soft"),
        },
        sev: {
          critical: v("sev-critical"),
          high: v("sev-high"),
          medium: v("sev-medium"),
          low: v("sev-low"),
          info: v("sev-info"),
          ok: v("sev-ok"),
        },
        series: {
          1: v("series-1"),
          2: v("series-2"),
          3: v("series-3"),
          4: v("series-4"),
        },
      },
      fontFamily: {
        sans: [
          '"Figtree Variable"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        display: [
          '"Bricolage Grotesque Variable"',
          '"Figtree Variable"',
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          '"IBM Plex Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        "2xs": ["0.78rem", { lineHeight: "1.15rem" }],
        xs: ["0.84rem", { lineHeight: "1.3rem" }],
        sm: ["0.95rem", { lineHeight: "1.45rem" }],
        base: ["1.0625rem", { lineHeight: "1.65rem" }],
        lg: ["1.1875rem", { lineHeight: "1.65rem" }],
        xl: ["1.5rem", { lineHeight: "1.9rem" }],
        "2xl": ["2rem", { lineHeight: "2.2rem" }],
        "3xl": ["2.6rem", { lineHeight: "2.7rem", letterSpacing: "-0.02em" }],
        hero: ["3.5rem", { lineHeight: "1", letterSpacing: "-0.03em" }],
      },
      borderRadius: {
        card: "1.125rem",
      },
      boxShadow: {
        panel: "var(--shadow-panel)",
        float: "var(--shadow-float)",
      },
      keyframes: {
        pulseRing: {
          "0%": { transform: "scale(1)", opacity: "0.55" },
          "100%": { transform: "scale(2.6)", opacity: "0" },
        },
        riseIn: {
          "0%": { opacity: "0", transform: "translateY(8px) scale(0.985)" },
          "100%": { opacity: "1", transform: "none" },
        },
        fadeIn: { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "pulse-ring": "pulseRing 1.8s cubic-bezier(0.2,0.6,0.3,1) infinite",
        "rise-in": "riseIn 0.28s cubic-bezier(0.2,0.7,0.2,1) both",
        "fade-in": "fadeIn 0.18s ease-out both",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;
