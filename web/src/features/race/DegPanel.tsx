// "Degradation per lap": one card per dry compound, then the deg chart.

import { useDeg } from "../../api/hooks";
import type { CompoundDeg } from "../../api/types";
import { EmptyState, ErrorState } from "../../components/states";
import { SectionHeader } from "../../components/ui";
import { compoundInfo, signed } from "../../lib/format";
import { DegChart } from "./DegChart";
import { PanelSkeleton } from "./RacePageSkeleton";

/** Why a compound has no usable number, or null if it has one. */
function naReason(c: CompoundDeg): string | null {
  if (!c.ran) return "Nobody ran it";
  if (c.deg_per_lap === null) return "Too few clean laps to fit";
  if (c.low_confidence) return `Low confidence · ${c.n_laps ?? 0} laps, ${c.n_drivers ?? 0} drivers`;
  return null;
}

function CompoundCard({ c }: { c: CompoundDeg }) {
  const info = compoundInfo(c.compound);
  const na = naReason(c);
  const name = (
    <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.1em] text-muted">
      <span aria-hidden className="size-2.5 rounded-full" style={{ background: info.colour }} />
      {info.label}
    </p>
  );
  if (na) {
    return (
      <div className="rounded-lg border border-dashed border-line p-3">
        {name}
        <p className="mt-1 text-xl font-bold text-muted">n/a</p>
        <p className="mt-0.5 text-xs text-muted">{na}</p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      {name}
      <p className="mt-1 text-xl font-bold">
        {signed(c.deg_per_lap!, 3)}
        <span className="text-sm font-semibold text-muted"> s/lap</span>
      </p>
      <p className="mt-0.5 text-xs text-muted">
        95% CI {signed(c.ci_low ?? 0, 3)} to {signed(c.ci_high ?? 0, 3)}
      </p>
      <p className="text-xs text-muted">{c.n_laps} laps · {c.n_drivers} drivers</p>
    </div>
  );
}

export function DegPanel({ round }: { round: number }) {
  const q = useDeg(round);
  const header = <SectionHeader label="Tyres" title="Degradation per lap" id="deg-title" />;

  let body;
  if (q.isPending) body = <PanelSkeleton label="Loading tyre degradation" />;
  else if (q.isError) body = <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  else if (q.data.points.length === 0) body = <EmptyState title="No clean dry laps in this race">Nothing to fit a tyre model on (wet or chaotic race).</EmptyState>;
  else {
    const shown = q.data.compounds.filter((c) => c.line);
    body = (
      <>
        <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-3">
          {q.data.compounds.map((c) => (
            <CompoundCard key={c.compound} c={c} />
          ))}
        </div>
        <figure className="mt-3 rounded-lg border border-line bg-surface p-3">
          <DegChart compounds={q.data.compounds} points={q.data.points} />
          <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            {shown.map((c) => (
              <span key={c.compound} className="flex items-center gap-1.5">
                <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ background: compoundInfo(c.compound).colour }} />
                {compoundInfo(c.compound).label}
              </span>
            ))}
            <span>Dots: clean laps vs the driver's baseline, fuel corrected. Bands: 95% CI.</span>
          </figcaption>
        </figure>
      </>
    );
  }
  return (
    <section aria-labelledby="deg-title" className="min-w-0">
      {header}
      {body}
    </section>
  );
}
