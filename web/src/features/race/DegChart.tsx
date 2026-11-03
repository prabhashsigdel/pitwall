// Hand-made SVG chart: clean lap times vs driver baseline against tyre age, with the
// fitted line per compound and its 95% CI band (lines using the CI's low / high slope).

import type { CompoundDeg, DegPoint, Line } from "../../api/types";
import { compoundInfo, signed } from "../../lib/format";
import { linear, quantile, ticks } from "../../lib/scale";

const W = 560;
const H = 300;
const M = { l: 58, r: 14, t: 14, b: 40 };
const DRAW_ORDER = ["HARD", "MEDIUM", "SOFT"] as const; // soft last = on top

function band(lo: Line, hi: Line, x: (v: number) => number, y: (v: number) => number): string {
  return [
    [lo.x_start, lo.y_start],
    [lo.x_end, lo.y_end],
    [hi.x_end, hi.y_end],
    [hi.x_start, hi.y_start],
  ]
    .map(([a, b]) => `${x(a).toFixed(1)},${y(b).toFixed(1)}`)
    .join(" ");
}

export function DegChart({ compounds, points }: { compounds: CompoundDeg[]; points: DegPoint[] }) {
  const lines = compounds.filter((c) => c.line);
  const values = points.map((p) => p.value);
  const lineYs = lines.flatMap((c) => [c.line!.y_start, c.line!.y_end]);
  // y range: the middle 96% of the points (a few traffic-ish laps would squash everything)
  // and every fitted line, padded a little.
  const yLo = Math.min(quantile(values, 0.02), ...lineYs);
  const yHi = Math.max(quantile(values, 0.98), ...lineYs);
  const pad = (yHi - yLo) * 0.08 || 0.5;
  const yTicks = ticks(yLo - pad, yHi + pad, 5);
  const y0 = Math.min(yTicks[0], yLo - pad);
  const y1 = Math.max(yTicks[yTicks.length - 1], yHi + pad);
  const xMax = Math.max(5, ...points.map((p) => p.tyre_age), ...lines.map((c) => c.line!.x_end));
  const xTicks = ticks(0, xMax, 6);
  const x1 = Math.max(xMax, xTicks[xTicks.length - 1]);

  const x = linear(0, x1, M.l, W - M.r);
  const y = linear(y0, y1, H - M.b, M.t);
  const summary = lines
    .map((c) => `${compoundInfo(c.compound).label} ${signed(c.deg_per_lap ?? 0, 3)} seconds per lap`)
    .join(", ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={`Tyre degradation: ${summary || "no fitted lines"}`}>
      <defs>
        <clipPath id="deg-plot">
          <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} />
        </clipPath>
      </defs>

      {/* grid + y axis */}
      {yTicks.map((t) => (
        <g key={t}>
          <line x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeWidth={t === 0 ? 1.2 : 0.6}
            strokeDasharray={t === 0 ? "4 4" : undefined} />
          <text x={M.l - 8} y={y(t)} dy="0.32em" textAnchor="end" fontSize="12" style={{ fill: "var(--color-muted)" }}>
            {signed(t, 1)}
          </text>
        </g>
      ))}
      {xTicks.map((t) => (
        <text key={t} x={x(t)} y={H - M.b + 18} textAnchor="middle" fontSize="12" style={{ fill: "var(--color-muted)" }}>
          {t}
        </text>
      ))}
      <text x={(M.l + W - M.r) / 2} y={H - 4} textAnchor="middle" fontSize="12" style={{ fill: "var(--color-muted)" }}>
        Tyre age (laps)
      </text>
      <text transform={`translate(13 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle" fontSize="12"
        style={{ fill: "var(--color-muted)" }}>
        Lap time vs baseline (s)
      </text>

      <g clipPath="url(#deg-plot)">
        {/* faint clean-lap points */}
        {DRAW_ORDER.map((c) => (
          <g key={c} style={{ fill: compoundInfo(c).colour }} opacity={0.22}>
            {points.filter((p) => p.compound === c).map((p, i) => (
              <circle key={i} cx={x(p.tyre_age)} cy={y(p.value)} r={2.2} />
            ))}
          </g>
        ))}
        {/* CI band + fitted line */}
        {DRAW_ORDER.map((c) => {
          const d = lines.find((l) => l.compound === c);
          if (!d?.line) return null;
          const colour = compoundInfo(c).colour;
          return (
            <g key={c}>
              {d.band_low && d.band_high && (
                <polygon points={band(d.band_low, d.band_high, x, y)} style={{ fill: colour }} opacity={0.16} />
              )}
              <line x1={x(d.line.x_start)} y1={y(d.line.y_start)} x2={x(d.line.x_end)} y2={y(d.line.y_end)}
                style={{ stroke: colour }} strokeWidth={2.5} strokeLinecap="round" />
            </g>
          );
        })}
      </g>
    </svg>
  );
}
