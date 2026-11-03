// Where the attacker's gain came from: pit loss + tyre gain + car pace + unexplained = total.
// Positive = in the attacker's favour (green), negative = against (red).

import type { Battle, Breakdown } from "../../api/types";
import { signed } from "../../lib/format";
import { linear, ticks } from "../../lib/scale";

const PARTS: { key: keyof Omit<Breakdown, "total">; label: string }[] = [
  { key: "pit_loss", label: "Pit loss" },
  { key: "tyre_gain", label: "Tyre gain" },
  { key: "car_pace", label: "Car pace" },
  { key: "unexplained", label: "Unexplained" },
];

const W = 520;
const H = 240;
const M = { l: 48, r: 8, t: 22, b: 34 };

function Chart({ values, total }: { values: number[]; total: number }) {
  // running total before / after each part
  const starts = values.map((_, i) => values.slice(0, i).reduce((a, b) => a + b, 0));
  const ends = starts.map((s, i) => s + values[i]);
  const all = [0, ...starts, ...ends, total];
  const yTicks = ticks(Math.min(...all), Math.max(...all), 4);
  const y = linear(Math.min(yTicks[0], ...all), Math.max(yTicks[yTicks.length - 1], ...all), H - M.b, M.t);
  const col = (W - M.l - M.r) / 5;
  const barW = col * 0.56;
  const cx = (i: number) => M.l + col * (i + 0.5);

  const bars = [
    ...values.map((v, i) => ({ label: PARTS[i].label, from: starts[i], to: ends[i], v, isTotal: false })),
    { label: "Total", from: 0, to: total, v: total, isTotal: true },
  ];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
      aria-label={bars.map((b) => `${b.label} ${signed(b.v)} seconds`).join(", ")}>
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} stroke="var(--color-line)"
            strokeWidth={t === 0 ? 1.2 : 0.6} />
          <text x={M.l - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="12" style={{ fill: "var(--color-muted)" }}>
            {signed(t, 1)}
          </text>
        </g>
      ))}
      {bars.map((b, i) => {
        const top = y(Math.max(b.from, b.to));
        const h = Math.max(1.5, Math.abs(y(b.from) - y(b.to)));
        const colour = b.v >= 0 ? "var(--color-good)" : "var(--color-bad)";
        const labelY = b.v >= 0 ? top - 6 : top + h + 14;
        return (
          <g key={b.label}>
            {/* dashed connector to the next bar's start */}
            {i < bars.length - 1 && (
              <line x1={cx(i) + barW / 2} x2={cx(i + 1) - barW / 2} y1={y(b.to)} y2={y(b.to)}
                stroke="var(--color-muted)" strokeDasharray="3 3" strokeWidth={0.8} />
            )}
            <rect x={cx(i) - barW / 2} y={top} width={barW} height={h} rx={2}
              style={{ fill: colour }} opacity={b.isTotal ? 1 : 0.85} />
            <text x={cx(i)} y={labelY} textAnchor="middle" fontSize="12.5" fontWeight={700} style={{ fill: "var(--color-text)" }}>
              {signed(b.v)}s
            </text>
            <text x={cx(i)} y={H - 10} textAnchor="middle" fontSize="12"
              fontWeight={b.isTotal ? 700 : 400} style={{ fill: b.isTotal ? "var(--color-text)" : "var(--color-muted)" }}>
              {b.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function Waterfall({ battle }: { battle: Battle }) {
  const b = battle.breakdown;
  const missing = PARTS.filter((p) => b?.[p.key] === null || b?.[p.key] === undefined).map((p) => p.label.toLowerCase());

  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-xs font-bold uppercase tracking-[0.1em] text-muted">Where the time came from</p>
      <p className="mt-1 font-bold">
        {battle.attacker} vs {battle.defender} · <span className={toneClass(battle.tone)}>{battle.result}</span>
      </p>

      {b && missing.length === 0 && b.total !== null ? (
        <div className="mt-3">
          <Chart values={PARTS.map((p) => b[p.key] as number)} total={b.total} />
          <p className="mt-1 text-xs text-muted">Seconds in {battle.attacker}'s favour. Green helped, red hurt.</p>
        </div>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-line px-3 py-3 text-sm text-muted">
          {missing.length === PARTS.length
            ? "No breakdown for this battle. Only undercut-window battles get one."
            : `No full breakdown: ${missing.join(", ")} ${missing.length === 1 ? "is" : "are"} missing for this battle, so the parts can't be added up.`}
        </p>
      )}

      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">Before</dt>
        <dd>{battle.gap_before_text}</dd>
        <dt className="text-muted">After</dt>
        <dd>{battle.gap_after_text}</dd>
        {battle.stuck !== null && (
          <>
            <dt className="text-muted">3 laps on</dt>
            <dd>{battle.stuck ? "Result held" : "Result didn't hold"}</dd>
          </>
        )}
        {battle.outlier_reason && (
          <>
            <dt className="text-muted">Outlier</dt>
            <dd className="text-warn">{battle.outlier_reason}</dd>
          </>
        )}
      </dl>
      {battle.notes.map((n) => (
        <p key={n} className="mt-2 text-xs text-muted">Note: {n}</p>
      ))}
    </div>
  );
}

export function toneClass(tone: Battle["tone"]): string {
  return tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-muted";
}
