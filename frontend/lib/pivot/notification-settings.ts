// FE view model; the Service adapter maps the in-app API contract to these keys.
export type NotificationPreferences = { allowed: boolean; after21Allowed: boolean };
export type NotificationPreferenceKey = keyof NotificationPreferences;
export type NotificationSettingsSource =
  | { available: false }
  | {
      available: true;
      // A future adapter must bind this to one active session object, not just userId.
      isCurrent: () => boolean;
      load: (options: { signal: AbortSignal }) => Promise<unknown>;
      save: (patch: Partial<NotificationPreferences>, options: { signal: AbortSignal }) => Promise<unknown>;
    };
export type NotificationSettingsState =
  | { status: "unavailable" | "loading" | "expired"; values: null }
  | { status: "ready" | "saving"; values: NotificationPreferences; saved?: boolean }
  | { status: "error"; values: null; operation: "load" | "save"; reason?: "conflict" };

export class NotificationSettingsError extends Error {
  constructor(public readonly kind: "session" | "conflict") { super(kind); }
}

// Unbound fallback only; Service supplies a session-bound HTTP adapter.
export const pendingNotificationSettingsSource: NotificationSettingsSource = { available: false };
export function initialNotificationSettingsState(source: NotificationSettingsSource): NotificationSettingsState {
  return { status: source.available ? "loading" : "unavailable", values: null };
}

function preferences(value: unknown): NotificationPreferences {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid settings");
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.allowed !== "boolean" || typeof candidate.after21Allowed !== "boolean") {
    throw new Error("Invalid settings");
  }
  return { allowed: candidate.allowed, after21Allowed: candidate.after21Allowed };
}

export function createNotificationSettingsController(
  source: NotificationSettingsSource,
  publish: (state: NotificationSettingsState) => void,
) {
  let state = initialNotificationSettingsState(source);
  let active: AbortController | undefined;
  let disposed = false;
  const update = (next: NotificationSettingsState) => { state = next; publish(next); };
  function current() {
    if (disposed || !source.available) return false;
    if (!source.isCurrent()) {
      active?.abort();
      update({ status: "expired", values: null });
      return false;
    }
    return true;
  }
  async function request(operation: "load" | "save", patch?: Partial<NotificationPreferences>) {
    if (disposed) return;
    if (!source.available) { update({ status: "unavailable", values: null }); return; }
    if (!current() || state.status === "saving") return;
    if (operation === "save" && state.status !== "ready") return;
    active?.abort();
    const task = new AbortController(); active = task;
    if (operation === "save" && state.status === "ready") update({ status: "saving", values: state.values });
    else update({ status: "loading", values: null });
    try {
      const result = operation === "load"
        ? await source.load({ signal: task.signal })
        : await source.save(patch!, { signal: task.signal });
      if (task.signal.aborted || active !== task || !current()) return;
      update({ status: "ready", values: preferences(result), saved: operation === "save" });
    } catch (error) {
      if (task.signal.aborted || active !== task || !current()) return;
      if (error instanceof NotificationSettingsError && error.kind === "session") {
        update({ status: "expired", values: null }); return;
      }
      // A failed PATCH may already have committed. Clear unknown values; retry GET,
      // never replay a write or claim rollback/success based on a transport error.
      update({ status: "error", values: null, operation,
        ...(error instanceof NotificationSettingsError && error.kind === "conflict" ? { reason: "conflict" as const } : {}) });
    }
  }
  return {
    load: () => request("load"),
    change(key: NotificationPreferenceKey, value: boolean) {
      if ((key !== "allowed" && key !== "after21Allowed") || typeof value !== "boolean") return Promise.resolve();
      return request("save", { [key]: value });
    },
    cancel() { disposed = true; active?.abort(); },
  };
}
