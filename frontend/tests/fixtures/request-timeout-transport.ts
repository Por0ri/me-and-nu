// Used only in the ignored preview copy. No request reaches an HTTP server.
export const timeoutFixture = { mode: "pending" as "pending" | "ready" | "error", events: [] as string[] };

export async function fixtureRead(signal: AbortSignal, label: string) {
  timeoutFixture.events.push(`${label}:start`);
  signal.throwIfAborted();
  if (timeoutFixture.mode === "error") throw new Error("Fixture network failure");
  if (timeoutFixture.mode === "ready") return;
  await new Promise<void>((_, reject) => signal.addEventListener("abort", () => {
    timeoutFixture.events.push(`${label}:abort`); reject(signal.reason);
  }, { once: true }));
}

export async function fixtureFetch(input: string | URL | Request, init: RequestInit = {}): Promise<Response> {
  const path = new URL(String(input)).pathname;
  const signal = init.signal;
  if (!signal) throw new Error("Fixture requires a cancellable request");
  if (path === "/api/v1/auth/session" && (init.method ?? "GET") === "GET") {
    await fixtureRead(signal, "session");
    return Response.json({ authenticated: false, sessionState: "anonymous", onboardingCompleted: false, user: null, csrfToken: "fixture-only" });
  }
  if (/^\/api\/v1\/auth\/login\/(kakao|naver)$/.test(path) && init.method === "POST") {
    timeoutFixture.events.push("oauth:start");
    return new Promise((_, reject) => signal.addEventListener("abort", () => {
      timeoutFixture.events.push("oauth:abort"); reject(signal.reason);
    }, { once: true }));
  }
  throw new Error(`Unexpected fixture request: ${path}`);
}
