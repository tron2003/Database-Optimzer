import type { Metadata } from "next";
import "@fontsource-variable/figtree";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./globals.css";
import { Providers } from "@/components/Providers";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "Database Optimizer",
  description:
    "Real-time PostgreSQL performance analytics with reviewed, approval-gated optimization.",
  icons: { icon: "/favicon.svg" },
};

/**
 * Runs before first paint so the page never flashes the wrong theme. Uses the
 * saved choice if there is one, otherwise follows the operating system.
 * localStorage can throw in locked-down browsers, hence the try/catch.
 */
const THEME_INIT = `(function(){try{var s=localStorage.getItem('dbopt.theme');var d=s?s==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.dataset.theme=d?'dark':'light';}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
