// "Time lost in traffic": per lap loss vs the driver's clean air pace, and what it
// would have meant for the finishing position.

import { useState } from "react";
import { useRace, useStrategies, useTraffic } from "../../api/hooks";
import type { Traffic } from "../../api/types";
import { Select } from "../../components/controls";
import { EmptyState, ErrorState } from "../../components/states";
import { Badge, Card, SectionHeader, Skeleton } from "../../components/ui";
import { signed } from "../../lib/format";
import { linear, ticks } from "../../lib/scale";

const ANY = "__any__";
const W = 560;
const H = 180;
const M = { l: 44, r: 8, t: 12, b: 28 };

function LossBars({ t }: { t: Traffic }) {
  const laps = t.per_lap;
  const vals = laps.map((l) => l.lost);
  const yTicks = ticks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
  const y = linear(Math.min(yTicks[0], ...vals), Math.max(yTicks[yTicks.length - 1], ...vals), H - M.b, M.t);
  const slot = (W - M.l - M.r) / laps.length;
  const barW = Math.min(18, slot * 0.7);
  const every = Math.ceil(laps.length / 12); // label at most ~12 laps
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
      aria-label={`Time lost per lap: ${laps.map((l) => `lap ${l.lap} ${signed(l.lost)} seconds`).join(", ")}`}>
      {yTicks.map((v) => (
        <g key={v}>
          <line x1={M.l} x2={W - M.r} y1={y(v)} y2={y(v)} stroke="var(--color-line)" strokeWidth={v === 0 ? 1.2 : 0.6} />
          <text x={M.l - 8} y={y(v)} dy="0.32em" textAnchor="end" fontSize="12" style={{ fill: "var(--color-muted)" }}>
            {signed(v, 1)}
          </text>
        </g>
      ))}
      {laps.map((l, i) => {
        const cx = M.l + slot * (i + 0.5);
        const top = y(Math.max(0, l.lost));
        return (
          <g key={l.lap}>
            <rect x={cx - barW / 2} y={top} width={barW} height={Math.max(1.5, Math.abs(y(0) - y(l.lost)))} rx={1.5}
              style={{ fill: l.lost > 0 ? "var(--color-bad)" : "var(--color-good)" }}>
              <title>{`Lap ${l.lap}: ${signed(l.lost)}s behind ${l.ahead ?? "?"} (actual ${l.actual.toFixed(3)}, clean air ${l.clean_air.toFixed(3)})`}</title>
            </rect>
            {i % every === 0 && (
              <text x={cx} y={H - 8} textAnchor="middle" fontSize="12" style={{ fill: "var(--color-muted)" }}>
                {l.lap}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function Result({ t }: { t: Traffic }) {
  const clear = t.total_lost > 0 && t.total_lost > t.uncertainty;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Card label="Time lost">
        {clear ? (
          <p className="mt-1.5 text-3xl font-black text-bad">
            {signed(t.total_lost, 1)}s <span className="text-base font-semibold text-muted">± {t.uncertainty.toFixed(1)}s</span>
          </p>
        ) : (
          <p className="mt-1.5 text-2xl font-bold">No clear time lost</p>
        )}
        <p className="mt-1 text-sm text-muted">
          {clear
            ? `95% range · ${t.per_lap.length} laps in traffic`
            : `${signed(t.total_lost, 1)}s ± ${t.uncertainty.toFixed(1)}s over ${t.per_lap.length} laps is within the model's uncertainty`}
        </p>
      </Card>
      <Card label="Finish on time alone">
        <p className="mt-1.5 text-3xl font-black">P{t.new_pos}</p>
        <p className="mt-1 text-sm text-muted">actually finished P{t.actual_pos}</p>
      </Card>
      <Card label="Gap at the flag">
        <p className="mt-1.5 text-3xl font-black">{t.car_ahead && t.gap_to_car_ahead !== null ? `${t.gap_to_car_ahead.toFixed(1)}s` : "–"}</p>
        <p className="mt-1 text-sm text-muted">{t.car_ahead ? `behind ${t.car_ahead}` : "no car ahead on the same lap"}</p>
      </Card>
    </div>
  );
}

function TrafficResult({ round, driver, blocker }: { round: number; driver: string; blocker: string | null }) {
  const q = useTraffic(round, driver, blocker);
  if (q.isPending) {
    return (
      <div aria-busy="true" aria-label="Loading traffic" className="flex flex-col gap-3">
        <Skeleton className="h-44" />
        <div className="grid gap-3 sm:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-28" />)}</div>
      </div>
    );
  }
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const t = q.data;
  return (
    <div className={`flex flex-col gap-3 transition-opacity duration-150 ${q.isPlaceholderData ? "opacity-60" : ""}`}>
      {t.per_lap.length === 0 ? (
        <EmptyState title="No laps in traffic">
          {driver} was never within {t.traffic_gap_s}s behind {blocker ?? "another car"} on a lap that can be compared.
        </EmptyState>
      ) : (
        <>
          <figure className="rounded-lg border border-line bg-surface p-3">
            <LossBars t={t} />
            <figcaption className="mt-1 text-xs text-muted">
              Seconds lost per lap vs {driver}'s clean air pace. Red = slower, green = no loss.
            </figcaption>
          </figure>
          <Result t={t} />
        </>
      )}
      <p className="text-sm text-muted">{t.caveat}</p>
      {t.skipped.length > 0 && (
        <details className="text-sm text-muted">
          <summary className="min-h-11 cursor-pointer content-center font-semibold text-text">
            {t.skipped.length} traffic lap{t.skipped.length === 1 ? "" : "s"} skipped
          </summary>
          <p>{t.skipped.map((s) => `lap ${s.lap}: ${s.reason}`).join(" · ")}</p>
        </details>
      )}
    </div>
  );
}

export function TrafficPanel({ round }: { round: number }) {
  const strategies = useStrategies(round);
  const race = useRace(round);
  const [driver, setDriver] = useState<string | null>(null);
  const [blocker, setBlocker] = useState<string>(ANY);

  const header = <SectionHeader label="Traffic" title="Time lost in traffic" id="traffic-title" />;
  if (strategies.isPending) {
    return <section>{header}<Skeleton className="h-11 w-80 max-w-full" /></section>;
  }
  if (strategies.isError) {
    return <section>{header}<ErrorState error={strategies.error} onRetry={() => strategies.refetch()} /></section>;
  }

  const finishers = strategies.data.drivers.filter((d) => d.status === "finished");
  if (finishers.length === 0) return <section>{header}<EmptyState title="No finishers in this race" /></section>;
  const current = driver ?? finishers[0].code;
  const others = strategies.data.drivers.filter((d) => d.code !== current);

  return (
    <section aria-labelledby="traffic-title">
      {header}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Select
          label="Driver"
          value={current}
          options={finishers.map((d) => ({ value: d.code, label: `P${d.position} · ${d.code} · ${d.team.name}` }))}
          onChange={(v) => {
            setDriver(v);
            if (v === blocker) setBlocker(ANY);
          }}
          className="w-full sm:w-72"
        />
        <Select
          label="Held up by"
          value={blocker}
          options={[{ value: ANY, label: "Any car" }, ...others.map((d) => ({ value: d.code, label: `${d.code} · ${d.team.name}` }))]}
          onChange={setBlocker}
          className="w-full sm:w-64"
        />
        {race.data?.is_reliable === false && (
          <div className="flex min-h-11 items-center">
            <Badge kind="bad">Race unreliable · wet laps excluded</Badge>
          </div>
        )}
      </div>
      <TrafficResult round={round} driver={current} blocker={blocker === ANY ? null : blocker} />
    </section>
  );
}
