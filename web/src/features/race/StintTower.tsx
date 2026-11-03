// "Who ran what": a timing tower with one stint bar per driver across the race distance.
//
// Stint segments are positioned by lap (left = start lap, width = laps) so every segment,
// the lap axis and the red flag lines share one scale. Each segment except the last ends
// 3px short, which leaves a visible gap at every stop.

import { useState } from "react";
import { useStrategies } from "../../api/hooks";
import type { DriverStrategy, Stint, Strategies } from "../../api/types";
import { ErrorState, EmptyState } from "../../components/states";
import { SectionHeader, TeamStrip } from "../../components/ui";
import { compoundInfo, plural } from "../../lib/format";
import { ticks } from "../../lib/scale";
import { useElementWidth } from "../../lib/useElementWidth";
import { TowerSkeleton } from "./RacePageSkeleton";

const TOP_N = 10;
const GAP_PX = 3;
const MIN_LABEL_PX = 30; // narrower segments get no "S18" label
const GRID = "grid grid-cols-[3rem_8.5rem_minmax(0,1fr)_3.5rem] items-center gap-x-3";

interface Hover {
  text: string;
  x: number;
  y: number;
}

const pct = (lap: number, total: number) => `${(lap / total) * 100}%`;

function Legend({ data }: { data: Strategies }) {
  const used = [...new Set(data.drivers.flatMap((d) => d.stints.map((s) => s.compound)))];
  const order = ["SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN"];
  const hasRedChange = data.drivers.some((d) => d.stints.some((s) => s.red_flag_change));
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted" aria-label="Legend">
      {used.sort((a, b) => order.indexOf(a) - order.indexOf(b)).map((c) => (
        <li key={c} className="flex items-center gap-1.5">
          <span aria-hidden className="h-3 w-5 rounded-sm" style={{ background: compoundInfo(c).colour }} />
          {compoundInfo(c).label}
        </li>
      ))}
      {hasRedChange && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="striped h-3 w-5 rounded-sm" style={{ backgroundColor: "var(--color-hard)" }} />
          Red flag tyre change
        </li>
      )}
      {data.red_flag_laps.length > 0 && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-3.5 w-0.5 bg-accent" />
          Red flag
        </li>
      )}
    </ul>
  );
}

function LapAxis({ data, barRef }: { data: Strategies; barRef: React.RefObject<HTMLDivElement | null> }) {
  const total = data.total_laps;
  // Lap numbers, minus the ones that would collide with an "RF 45" label.
  const laps = [1, ...ticks(0, total, 7).filter((t) => t >= 5)].filter(
    (t) => !data.red_flag_laps.some((rf) => Math.abs(rf - t) < total * 0.06),
  );
  return (
    <div className={`${GRID} h-9 border-b border-line px-4 text-xs font-bold uppercase tracking-[0.08em] text-muted`}>
      <span className="text-right">Pos</span>
      <span>Driver</span>
      <div ref={barRef} className="relative h-full" aria-hidden>
        {laps.map((t) => (
          <span key={t} className="absolute bottom-1.5 -translate-x-1/2" style={{ left: pct(t - 0.5, total) }}>
            {t}
          </span>
        ))}
        {data.red_flag_laps.map((rf) => (
          <span key={rf} className="absolute bottom-1.5 -translate-x-1/2 whitespace-nowrap text-accent-text" style={{ left: pct(rf, total) }}>
            RF {rf}
          </span>
        ))}
      </div>
      <span className="text-right">Stops</span>
    </div>
  );
}

function Segment({ stint, total, isLast, pxPerLap, onHover }: {
  stint: Stint;
  total: number;
  isLast: boolean;
  pxPerLap: number;
  onHover: (h: Hover | null) => void;
}) {
  const info = compoundInfo(stint.compound);
  const laps = stint.end_lap - stint.start_lap + 1;
  const label = `${info.letter}${laps}`;
  const text = `${info.label} · laps ${stint.start_lap}–${stint.end_lap} (${plural(laps, "lap")})${
    stint.red_flag_change ? " · fitted during the red flag" : ""
  }`;
  const show = (e: React.PointerEvent) => onHover({ text, x: e.clientX, y: e.clientY });
  return (
    <div
      role="img"
      aria-label={text}
      onPointerEnter={show}
      onPointerMove={show}
      onPointerLeave={() => onHover(null)}
      className={`absolute top-1/2 flex h-5 -translate-y-1/2 items-center overflow-hidden rounded-[3px] ${
        stint.red_flag_change ? "striped" : ""
      }`}
      style={{
        left: pct(stint.start_lap - 1, total),
        width: `calc(${pct(laps, total)} - ${isLast ? 0 : GAP_PX}px)`,
        backgroundColor: info.colour,
      }}
    >
      {laps * pxPerLap >= MIN_LABEL_PX && (
        <span aria-hidden className="px-1.5 text-xs font-bold text-ink">
          {label}
        </span>
      )}
    </div>
  );
}

function Row({ d, data, pxPerLap, onHover }: {
  d: DriverStrategy;
  data: Strategies;
  pxPerLap: number;
  onHover: (h: Hover | null) => void;
}) {
  const dnf = d.status === "DNF";
  const pos = dnf ? "DNF" : d.position === null ? "–" : `P${d.position}`;
  return (
    <li className={`${GRID} min-h-11 border-b border-line px-4 last:border-b-0`}>
      <span className={`text-right font-bold ${d.position === 1 ? "text-accent-text" : "text-muted"}`}>{pos}</span>
      <span className="flex min-w-0 items-center gap-2">
        <TeamStrip colour={d.team.colour} className={`h-6 ${dnf ? "opacity-50" : ""}`} />
        <span className="min-w-0 leading-tight">
          <span className={`block font-bold tracking-[0.04em] ${dnf ? "text-muted" : ""}`}>{d.code}</span>
          <span className="block truncate text-xs text-muted">{d.team.name}</span>
        </span>
      </span>
      <div className={`relative h-11 ${dnf ? "opacity-50" : ""}`}>
        {d.stints.map((s, i) => (
          <Segment
            key={s.start_lap}
            stint={s}
            total={data.total_laps}
            isLast={i === d.stints.length - 1}
            pxPerLap={pxPerLap}
            onHover={onHover}
          />
        ))}
        {/* red flag: a red line through every row, at the end of the red flag lap */}
        {data.red_flag_laps.map((rf) => (
          <span key={rf} aria-hidden className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-accent" style={{ left: pct(rf, data.total_laps) }} />
        ))}
      </div>
      <span className="text-right font-bold">
        {d.stops}
        <span className="sr-only"> {d.stops === 1 ? "stop" : "stops"}{dnf ? ", did not finish" : ""}</span>
      </span>
    </li>
  );
}

export function StintTower({ round }: { round: number }) {
  const q = useStrategies(round);
  const [showAll, setShowAll] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  const [barRef, barWidth] = useElementWidth<HTMLDivElement>();

  const header = (aside?: React.ReactNode) => (
    <SectionHeader label="Strategy" title="Who ran what" id="tower-title" aside={aside} />
  );

  if (q.isPending) return <section>{header()}<TowerSkeleton /></section>;
  if (q.isError) return <section>{header()}<ErrorState error={q.error} onRetry={() => q.refetch()} /></section>;

  const data = q.data;
  if (data.drivers.length === 0 || data.total_laps === 0) {
    return <section>{header()}<EmptyState title="No stint data for this race" /></section>;
  }
  const rows = showAll ? data.drivers : data.drivers.slice(0, TOP_N);
  const pxPerLap = barWidth / data.total_laps;

  return (
    <section aria-labelledby="tower-title">
      {header(<Legend data={data} />)}
      <div className="overflow-x-auto rounded-lg border border-line bg-surface" tabIndex={0} aria-label="Stint timeline, scrolls sideways">
        <div className="min-w-[44rem]">
          <LapAxis data={data} barRef={barRef} />
          <ol>
            {rows.map((d) => (
              <Row key={d.code} d={d} data={data} pxPerLap={pxPerLap} onHover={setHover} />
            ))}
          </ol>
        </div>
      </div>
      {data.drivers.length > TOP_N && (
        <button
          type="button"
          aria-expanded={showAll}
          onClick={() => setShowAll((v) => !v)}
          className="pressable mt-3 min-h-11 rounded-md border border-line bg-surface px-4 font-bold hover:border-muted"
        >
          {showAll ? `Show top ${TOP_N}` : `Show all ${data.drivers.length} drivers`}
        </button>
      )}
      {hover && (
        <div
          role="tooltip"
          className="pointer-events-none fixed z-30 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-line bg-inner px-2.5 py-1.5 text-sm font-semibold shadow-lg shadow-black/40"
          // keep the tooltip on screen near the edges
          style={{ left: Math.min(Math.max(hover.x, 160), window.innerWidth - 160), top: hover.y - 12 }}
        >
          {hover.text}
        </div>
      )}
    </section>
  );
}
