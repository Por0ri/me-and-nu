export const REQUEST_DEADLINE_MS = 15_000;

export class RequestTimeoutError extends Error {
  constructor() { super("Request deadline exceeded."); this.name = "RequestTimeoutError"; }
}

/** Opt-in only: abort the transport and settle even if a source ignores abort. */
export function withRequestDeadline<T>(
  load: (signal: AbortSignal) => Promise<T>,
  { signal, timeoutMs = REQUEST_DEADLINE_MS }: { signal?: AbortSignal | null; timeoutMs?: number } = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const request = new AbortController();
    const cancel = () => request.abort(signal?.reason);
    const aborted = () => reject(request.signal.reason);
    request.signal.addEventListener("abort", aborted, { once: true });
    signal?.addEventListener("abort", cancel, { once: true });
    const timer = setTimeout(() => request.abort(new RequestTimeoutError()), timeoutMs);
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      request.signal.removeEventListener("abort", aborted);
      request.signal.removeEventListener("abort", cleanup);
    };
    request.signal.addEventListener("abort", cleanup, { once: true });
    if (signal?.aborted) { cancel(); return; }
    try {
      load(request.signal).then(
        value => { if (!request.signal.aborted) resolve(value); },
        reject,
      ).finally(cleanup);
    } catch (error) { cleanup(); reject(error); }
  });
}
