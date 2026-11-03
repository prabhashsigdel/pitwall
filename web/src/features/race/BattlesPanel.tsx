// "Who jumped who": pit stop battles as selectable rows, with the waterfall for the selected one.

import { useState } from "react";
import { useBattles } from "../../api/hooks";
import type { Battle } from "../../api/types";
import { Toggle } from "../../components/controls";
import { EmptyState, ErrorState } from "../../components/states";
import { SectionHeader, Skeleton, TeamStrip } from "../../components/ui";
import { signed } from "../../lib/format";
import { toneClass, Waterfall } from "./Waterfall";

const keyOf = (b: Battle) => `${b.attacker}-${b.defender}-${b.attacker_pit_lap}`;

function Pill({ children, kind }: { children: string; kind: "accent" | "warn" }) {
  const cls = kind === "accent" ? "border-accent text-accent-text" : "border-warn/50 text-warn";
  return (
    <span className={`rounded border px-1.5 py-px text-[0.68rem] font-bold uppercase tracking-[0.08em] ${cls}`}>
      {children}
    </span>
  );
}

function BattleRow({ b, selected, onSelect }: { b: Battle; selected: boolean; onSelect: () => void }) {
  const pits = `Pit L${b.attacker_pit_lap} / ${b.defender_pit_lap === null ? "no stop" : `L${b.defender_pit_lap}`}`;
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={`pressable grid min-h-11 w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md border px-3 py-2.5 text-left ${
          selected ? "border-accent bg-accent/[0.07]" : "border-line bg-surface hover:border-muted"
        }`}
      >
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 font-bold">
            <span className="flex items-center gap-1.5">
              <TeamStrip colour={b.attacker_team.colour} className="h-4" />
              {b.attacker}
            </span>
            <span className="text-sm font-semibold text-muted">vs</span>
            <span className="flex items-center gap-1.5">
              <TeamStrip colour={b.defender_team.colour} className="h-4" />
              {b.defender}
            </span>
            {b.sc_window && <Pill kind="accent">SC window</Pill>}
            {b.outlier && <Pill kind="warn">Outlier</Pill>}
          </span>
          <span className="mt-0.5 block text-sm text-muted">
            {pits} · {b.type}
          </span>
          <span className={`mt-0.5 block text-sm font-semibold ${toneClass(b.tone)}`}>{b.result}</span>
        </span>
        <span className="text-right">
          <span className="block text-lg font-bold">{b.gained === null ? "–" : `${signed(b.gained, 1)}s`}</span>
          <span className="block text-xs text-muted">gained</span>
        </span>
      </button>
    </li>
  );
}

export function BattlesPanel({ round }: { round: number }) {
  const [hideSc, setHideSc] = useState(true);
  const [hideOutliers, setHideOutliers] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const q = useBattles(round, { hideSc, hideOutliers });

  const controls = (
    <div className="flex flex-wrap gap-x-5">
      <Toggle label="Hide safety car windows" checked={hideSc} onChange={setHideSc} />
      <Toggle label="Hide outliers" checked={hideOutliers} onChange={setHideOutliers} />
    </div>
  );

  let body;
  if (q.isPending) {
    body = (
      <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading battles">
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-[4.75rem]" />)}
      </div>
    );
  } else if (q.isError) {
    body = <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  } else if (q.data.total === 0) {
    body = <EmptyState title="No pit battles in this race">No two cars were close enough when one of them stopped.</EmptyState>;
  } else if (q.data.battles.length === 0) {
    body = (
      <EmptyState title="No clean battles in this race">
        All {q.data.total} are hidden by the filters above (or are teammates / lap 1–4 stops).
      </EmptyState>
    );
  } else {
    const current = q.data.battles.find((b) => keyOf(b) === selected) ?? null;
    body = (
      <>
        <p className="mb-2 text-sm text-muted">
          {q.data.battles.length} of {q.data.total} battles · tap one for its breakdown
        </p>
        {/* desktop: the list scrolls so the breakdown below stays near the row you clicked */}
        <ul
          className={`flex flex-col gap-2 transition-opacity duration-150 lg:max-h-[27rem] lg:overflow-y-auto lg:pr-1 ${
            q.isPlaceholderData ? "opacity-60" : ""
          }`}
        >
          {q.data.battles.map((b) => (
            <BattleRow
              key={keyOf(b)}
              b={b}
              selected={keyOf(b) === selected}
              onSelect={() => setSelected((s) => (s === keyOf(b) ? null : keyOf(b)))}
            />
          ))}
        </ul>
        <div className="mt-4" aria-live="polite">
          {current ? <Waterfall battle={current} /> : null}
        </div>
      </>
    );
  }

  return (
    <section aria-labelledby="battles-title" className="min-w-0">
      <SectionHeader label="Pit stops" title="Who jumped who" id="battles-title" />
      <div className="-mt-2 mb-3">{controls}</div>
      {body}
    </section>
  );
}
