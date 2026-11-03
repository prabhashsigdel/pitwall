// Number, date and compound formatting shared by every component.

import type { Compound } from "../api/types";

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** "2026-11-01" -> "1 NOV 2026" (no time zone shifts: the date is parsed by hand). */
export function raceDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** Signed number with a real minus sign: +0.048 / −1.20 */
export function signed(v: number, digits = 2): string {
  const s = Math.abs(v).toFixed(digits);
  if (Number(s) === 0) return s;
  return `${v > 0 ? "+" : "−"}${s}`;
}

export function fixed(v: number | null | undefined, digits = 2, empty = "–"): string {
  return v === null || v === undefined ? empty : v.toFixed(digits);
}

export const COMPOUNDS: Record<Compound, { letter: string; label: string; colour: string }> = {
  SOFT: { letter: "S", label: "Soft", colour: "var(--color-soft)" },
  MEDIUM: { letter: "M", label: "Medium", colour: "var(--color-medium)" },
  HARD: { letter: "H", label: "Hard", colour: "var(--color-hard)" },
  INTERMEDIATE: { letter: "I", label: "Inter", colour: "var(--color-inter)" },
  WET: { letter: "W", label: "Wet", colour: "var(--color-wet)" },
  UNKNOWN: { letter: "?", label: "Unknown", colour: "var(--color-muted)" },
};

export function compoundInfo(c: string) {
  return COMPOUNDS[(c in COMPOUNDS ? c : "UNKNOWN") as Compound];
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
