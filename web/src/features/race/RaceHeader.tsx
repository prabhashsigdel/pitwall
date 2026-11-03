// Race header (round line, name, badges, race picker) and the four stat cards.

import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import { useRace, useRaces } from "../../api/hooks";
import type { RaceHeader as RaceHeaderData } from "../../api/types";
import { Select } from "../../components/controls";
import { ErrorState } from "../../components/states";
import { Badge, Card, TeamStrip } from "../../components/ui";
import { fixed, raceDate } from "../../lib/format";
import { HeaderSkeleton } from "./RacePageSkeleton";

function RacePicker({ round }: { round: number }) {
  const races = useRaces();
  const navigate = useNavigate();
  if (!races.data) return null; // the header still works without the picker
  const options = [...races.data].reverse().map((r) => ({
    value: String(r.round),
    label: `R${String(r.round).padStart(2, "0")} · ${r.event_name}${r.is_reliable === false ? " · unreliable" : ""}`,
  }));
  return (
    <Select
      label="Change race"
      value={String(round)}
      options={options}
      onChange={(v) => navigate(`/race/${v}`)}
      className="w-full sm:w-72"
    />
  );
}

function Badges({ race }: { race: RaceHeaderData }) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {race.is_reliable === true && <Badge kind="ok">Reliable</Badge>}
      {race.is_reliable === false && <Badge kind="bad">Unreliable · {race.unreliable_reason}</Badge>}
      {race.fuel?.source === "estimated" && <Badge kind="ok">Fuel estimated</Badge>}
      {race.fuel?.source === "fixed" && (
        <Badge kind="warn" title="This race's own fuel estimate was out of range, so the season value is used">
          Fuel fixed
        </Badge>
      )}
      {race.red_flag_laps.map((lap) => (
        <Badge key={lap} kind="accent">Red flag lap {lap}</Badge>
      ))}
    </div>
  );
}

function Stat({ label, value, sub }: { label: string; value: ReactNode; sub: ReactNode }) {
  return (
    <Card label={label}>
      <div className="mt-1.5 text-2xl font-bold leading-tight">{value}</div>
      <p className="mt-1 text-sm text-muted">{sub}</p>
    </Card>
  );
}

function StatCards({ race }: { race: RaceHeaderData }) {
  const { winner, fuel, track_temp: temp } = race;
  const fuelSub = !fuel
    ? "no fuel model"
    : fuel.source === "estimated"
      ? `Own estimate · 95% CI ${fixed(fuel.ci_low, 3)} – ${fixed(fuel.ci_high, 3)}`
      : `Season fallback · own estimate ${fixed(fuel.own_estimate, 3)} was out of range`;
  return (
    <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat
        label="Winner"
        value={
          winner ? (
            <span className="flex items-center gap-2.5">
              <TeamStrip colour={winner.team.colour} className="h-7" />
              <span className="text-accent-text">{winner.code}</span>
              <span className="truncate text-base font-semibold text-muted">{winner.team.name}</span>
            </span>
          ) : (
            "–"
          )
        }
        sub={winner?.strategy ?? "no classified winner"}
      />
      <Stat
        label="Fuel + track"
        value={fuel ? <>{fuel.value.toFixed(3)} <span className="text-base font-semibold text-muted">s/lap</span></> : "–"}
        sub={fuelSub}
      />
      <Stat
        label="Track temp"
        value={
          <>
            {fixed(temp.first_10, 1)}° <span className="text-muted" aria-label="to">→</span> {fixed(temp.last_10, 1)}°
            <span className="text-base font-semibold text-muted">C</span>
          </>
        }
        sub="First 10 → last 10 laps"
      />
      <Stat
        label="Model fit"
        value={<>R² {fixed(race.r_squared, 2)}</>}
        sub={`${race.clean_laps ?? "–"} clean laps · ${race.drivers_in_model} drivers`}
      />
    </div>
  );
}

export function RaceHeader({ round }: { round: number }) {
  const race = useRace(round);
  if (race.isPending) return <HeaderSkeleton />;
  if (race.isError) return <ErrorState error={race.error} onRetry={() => race.refetch()} />;
  const r = race.data;
  return (
    <section aria-labelledby="race-title">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-bold uppercase tracking-[0.14em] text-accent-text">
            Round {r.round} · {r.circuit} · {raceDate(r.date)}
          </p>
          <h1 id="race-title" className="mt-1 text-4xl font-black leading-[1.05] tracking-tight sm:text-5xl">
            {r.event_name}
          </h1>
          <Badges race={r} />
        </div>
        <RacePicker round={round} />
      </div>
      <StatCards race={r} />
    </section>
  );
}
