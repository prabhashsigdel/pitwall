// Loading placeholders shaped like the real Race page sections.

import { Skeleton } from "../../components/ui";

export function HeaderSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading race">
      <Skeleton className="h-3 w-64" />
      <Skeleton className="mt-3 h-10 w-80 max-w-full" />
      <div className="mt-4 flex gap-2">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="h-6 w-32" />
      </div>
      <div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-lg border border-line bg-surface p-4">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="mt-3 h-7 w-28" />
            <Skeleton className="mt-2 h-3 w-36 max-w-full" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function TowerSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading strategies" className="rounded-lg border border-line bg-surface">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-11 items-center gap-3 border-b border-line px-4 last:border-b-0">
          <Skeleton className="h-4 w-6" />
          <Skeleton className="h-5 w-1" />
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-4 flex-1" />
          <Skeleton className="h-4 w-6" />
        </div>
      ))}
    </div>
  );
}

export function PanelSkeleton({ label }: { label: string }) {
  return (
    <div aria-busy="true" aria-label={label} className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export function RacePageSkeleton() {
  return (
    <div className="flex flex-col gap-12">
      <HeaderSkeleton />
      <TowerSkeleton />
    </div>
  );
}
