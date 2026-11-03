// /race/:round. Every section fetches its own data in parallel and shows its own
// skeleton / error, so one slow or failing endpoint never blanks the whole page.

import { useParams } from "react-router";
import { EmptyState } from "../../components/states";
import { BattlesPanel } from "./BattlesPanel";
import { DegPanel } from "./DegPanel";
import { RaceHeader } from "./RaceHeader";
import { StintTower } from "./StintTower";
import { TrafficPanel } from "./TrafficPanel";

export function RacePage() {
  const { round: raw } = useParams();
  const round = Number(raw);
  if (!Number.isInteger(round) || round < 1) {
    return <EmptyState title="That isn't a race">Race pages look like /race/19.</EmptyState>;
  }

  // key={round}: switching races resets every section's local state (selection, driver pick).
  return (
    <div key={round} className="flex flex-col gap-12">
      <RaceHeader round={round} />
      <StintTower round={round} />
      <div className="grid gap-10 lg:grid-cols-2 lg:gap-6">
        <DegPanel round={round} />
        <BattlesPanel round={round} />
      </div>
      <TrafficPanel round={round} />
    </div>
  );
}
