// Fetch helpers. Every failure becomes an ApiError with a message a person can act on.

export type ApiErrorKind = "offline" | "busy" | "not_found" | "refused" | "server";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number;

  constructor(kind: ApiErrorKind, status: number, message: string) {
    super(message);
    this.kind = kind;
    this.status = status;
  }
}

const OFFLINE_MESSAGE =
  "Can't reach the API. Start it with api.bat (or start.bat for both), then try again.";

function kindFor(status: number): ApiErrorKind {
  if (status === 503) return "busy";
  if (status === 404) return "not_found";
  if (status === 422) return "refused";
  return "server";
}

/** GET a JSON endpoint under /api. */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { signal, headers: { Accept: "application/json" } });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("offline", 0, OFFLINE_MESSAGE);
  }

  // The backend always answers JSON. Anything else (the Vite proxy's empty 500 / 502
  // when nothing listens on :8000) means the API isn't running.
  const isJson = res.headers.get("content-type")?.includes("application/json") ?? false;
  if (!isJson) throw new ApiError("offline", res.status, OFFLINE_MESSAGE);

  const body: unknown = await res.json();
  if (!res.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : `The API answered ${res.status}.`;
    throw new ApiError(kindFor(res.status), res.status, detail);
  }
  return body as T;
}

/** Turn anything thrown into a readable message. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Something went wrong.";
}
