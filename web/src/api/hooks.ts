// TanStack Query hooks, one per endpoint. Keys start with the endpoint name so a
// round's data can be found / refetched together.

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getJson } from "./client";
import type { Battles, Deg, Meta, RaceHeader, RaceSummary, Strategies, Traffic } from "./types";

export function useMeta() {
  return useQuery({ queryKey: ["meta"], queryFn: ({ signal }) => getJson<Meta>("/meta", signal) });
}

export function useRaces() {
  return useQuery({ queryKey: ["races"], queryFn: ({ signal }) => getJson<RaceSummary[]>("/races", signal) });
}

export function useRace(round: number) {
  return useQuery({
    queryKey: ["race", round],
    queryFn: ({ signal }) => getJson<RaceHeader>(`/races/${round}`, signal),
  });
}

export function useStrategies(round: number) {
  return useQuery({
    queryKey: ["strategies", round],
    queryFn: ({ signal }) => getJson<Strategies>(`/races/${round}/strategies`, signal),
  });
}

export function useDeg(round: number) {
  return useQuery({
    queryKey: ["deg", round],
    queryFn: ({ signal }) => getJson<Deg>(`/races/${round}/deg`, signal),
  });
}

export interface BattleFilters {
  hideSc: boolean;
  hideOutliers: boolean;
}

export function useBattles(round: number, f: BattleFilters) {
  const qs = new URLSearchParams({ hide_sc: String(f.hideSc), hide_outliers: String(f.hideOutliers) });
  return useQuery({
    queryKey: ["battles", round, f.hideSc, f.hideOutliers],
    queryFn: ({ signal }) => getJson<Battles>(`/races/${round}/battles?${qs}`, signal),
    // Flipping a filter keeps the old list on screen until the new one arrives (no skeleton flash).
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === round ? keepPreviousData(prev) : undefined),
  });
}

export function useTraffic(round: number, driver: string | null, blocker: string | null) {
  const qs = new URLSearchParams({ driver: driver ?? "" });
  if (blocker) qs.set("blocker", blocker);
  return useQuery({
    queryKey: ["traffic", round, driver, blocker],
    queryFn: ({ signal }) => getJson<Traffic>(`/races/${round}/traffic?${qs}`, signal),
    enabled: driver !== null,
    placeholderData: (prev, prevQuery) => (prevQuery?.queryKey[1] === round ? keepPreviousData(prev) : undefined),
  });
}
