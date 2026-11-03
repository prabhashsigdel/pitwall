// /race with no round: send the user to the latest race loaded.

import { Navigate } from "react-router";
import { useMeta } from "../api/hooks";
import { EmptyState, ErrorState } from "../components/states";
import { RacePageSkeleton } from "../features/race/RacePageSkeleton";

export function LatestRace() {
  const meta = useMeta();
  if (meta.isPending) return <RacePageSkeleton />;
  if (meta.isError) return <ErrorState error={meta.error} onRetry={() => meta.refetch()} />;
  if (!meta.data.last_race) {
    return <EmptyState title="No races loaded yet">Run the pipeline scripts (src/ingest.py ...) first.</EmptyState>;
  }
  return <Navigate to={`/race/${meta.data.last_race.round}`} replace />;
}
