// Error and empty states with readable messages.

import type { ReactNode } from "react";
import { ApiError, errorMessage } from "../api/client";

const TITLES: Record<string, string> = {
  offline: "The API isn't running",
  busy: "The database is busy",
  not_found: "Not found",
  refused: "Can't work that out",
  server: "Something went wrong",
};

/** A failed request: what happened, what to do, and a retry button. */
export function ErrorState({ error, onRetry, compact = false }: {
  error: unknown;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const kind = error instanceof ApiError ? error.kind : "server";
  return (
    <div
      role="alert"
      className={`rounded-lg border border-bad/40 bg-bad/5 ${compact ? "p-3" : "p-5"}`}
    >
      <p className="font-bold text-bad">{TITLES[kind]}</p>
      <p className="mt-1 text-sm text-text">{errorMessage(error)}</p>
      {onRetry && kind !== "refused" && kind !== "not_found" && (
        <button
          type="button"
          onClick={onRetry}
          className="pressable mt-3 min-h-11 rounded-md border border-line bg-inner px-4 text-sm font-bold hover:border-muted"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center">
      <p className="font-bold">{title}</p>
      {children && <div className="mt-1 text-sm text-muted">{children}</div>}
    </div>
  );
}
