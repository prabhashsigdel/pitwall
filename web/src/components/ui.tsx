// Small presentational building blocks used across pages.

import type { ReactNode } from "react";
import { darkenForLight } from "../lib/color";
import { useTheme } from "../lib/theme";

/** Small uppercase red label above a bold title. */
export function SectionHeader({ label, title, id, aside }: {
  label: string;
  title: string;
  id?: string;
  aside?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <p className="text-xs font-bold uppercase tracking-[0.14em] text-accent-text">{label}</p>
        <h2 id={id} className="mt-0.5 text-2xl font-bold leading-tight tracking-tight">{title}</h2>
      </div>
      {aside}
    </div>
  );
}

type BadgeKind = "ok" | "warn" | "bad" | "neutral" | "accent";

const BADGE: Record<BadgeKind, string> = {
  ok: "border-good/40 bg-good/10 text-good",
  warn: "border-warn/40 bg-warn/10 text-warn",
  bad: "border-bad/45 bg-bad/10 text-bad",
  neutral: "border-line text-muted",
  accent: "border-accent bg-accent/15 text-accent-text",
};

export function Badge({ kind = "neutral", children, title }: { kind?: BadgeKind; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-bold uppercase tracking-[0.08em] ${BADGE[kind]}`}
    >
      {children}
    </span>
  );
}

/** Card surface with a small uppercase label. */
export function Card({ label, children, className = "" }: { label?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-line bg-surface p-4 ${className}`}>
      {label && <p className="text-xs font-bold uppercase tracking-[0.1em] text-muted">{label}</p>}
      {children}
    </div>
  );
}

/** Grey placeholder block shaped like the content it stands in for. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse-soft rounded bg-line/70 ${className}`} />;
}

/** Thin vertical team colour strip (darkened in light mode where it would be too faint). */
export function TeamStrip({ colour, className = "h-5" }: { colour: string; className?: string }) {
  const theme = useTheme();
  const shown = theme === "light" ? darkenForLight(colour) : colour;
  return <span aria-hidden className={`inline-block w-1 shrink-0 rounded-sm ${className}`} style={{ background: shown }} />;
}

/** Visually hidden, still read by screen readers. */
export function SrOnly({ children }: { children: ReactNode }) {
  return <span className="sr-only">{children}</span>;
}
