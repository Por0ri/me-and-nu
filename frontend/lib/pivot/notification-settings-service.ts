import { ApiError, getSession, getNotificationSettings, patchNotificationSettings, type Session, type NotificationSettingsResponse } from "@/lib/api";
import { NotificationSettingsError, type NotificationSettingsSource } from "./notification-settings";
import { isActiveSession, requirePivotApi } from "./services";

function parse(value: unknown): NotificationSettingsResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid settings response");
  const row = value as Record<string, unknown>;
  if (typeof row.inAppEnabled !== "boolean" || typeof row.nightInAppEnabled !== "boolean"
    || !Number.isSafeInteger(row.version) || (row.version as number) < 1 || (row.version as number) > 2147483647) {
    throw new Error("Invalid settings response");
  }
  return { inAppEnabled: row.inAppEnabled, nightInAppEnabled: row.nightInAppEnabled, version: row.version as number };
}

export function createServiceNotificationSettingsSource(
  readSession: () => Session | null,
  api = { getSession, getNotificationSettings, patchNotificationSettings },
  guard: () => void = requirePivotApi,
): NotificationSettingsSource {
  const owner = readSession(), userId = owner?.user?.id;
  let version: number | null = null;
  let generation = 0;
  const isCurrent = () => isActiveSession(owner) && readSession() === owner && owner?.user?.id === userId;
  function check(signal: AbortSignal) {
    signal.throwIfAborted(); guard();
    if (!isCurrent()) throw new NotificationSettingsError("session");
  }
  async function preflight(signal: AbortSignal) {
    check(signal);
    const session = await api.getSession({ signal });
    check(signal);
    // Pin both identity and session, including same-user cookie rotation in another tab.
    if (!isActiveSession(session) || session.user?.id !== userId || !owner?.csrfToken
      || session.csrfToken !== owner.csrfToken) throw new NotificationSettingsError("session");
    return owner.csrfToken;
  }
  function failure(error: unknown, request: number): never {
    if (request === generation) version = null;
    if (error instanceof ApiError) {
      if (error.status === 401 || error.status === 403) throw new NotificationSettingsError("session");
      if (error.status === 409 && error.code === "NOTIFICATION_SETTINGS_CONFLICT") throw new NotificationSettingsError("conflict");
    }
    throw error;
  }
  return {
    available: true, isCurrent,
    async load({ signal }) {
      const request = ++generation;
      version = null;
      try {
        await preflight(signal);
        const result = await api.getNotificationSettings({ signal });
        check(signal);
        // GET has no CSRF precondition: recheck the cookie before publishing a read.
        await preflight(signal);
        const row = parse(result); version = row.version;
        return { allowed: row.inAppEnabled, after21Allowed: row.nightInAppEnabled };
      } catch (error) { return failure(error, request); }
    },
    async save(patch, { signal }) {
      check(signal);
      const request = ++generation;
      const expected = version; version = null;
      const keys = Object.keys(patch);
      if (expected === null || !keys.length || keys.some(key => !["allowed", "after21Allowed"].includes(key)
        || typeof patch[key as keyof typeof patch] !== "boolean")) throw new Error("Settings must be reloaded");
      try {
        const csrf = await preflight(signal);
        const result = await api.patchNotificationSettings({ version: expected,
          ...(patch.allowed === undefined ? {} : { inAppEnabled: patch.allowed }),
          ...(patch.after21Allowed === undefined ? {} : { nightInAppEnabled: patch.after21Allowed }),
        }, { signal, headers: { "X-CSRF-Token": csrf } });
        check(signal);
        await preflight(signal);
        const row = parse(result);
        if (row.version !== expected + 1
          || (patch.allowed !== undefined && row.inAppEnabled !== patch.allowed)
          || (patch.after21Allowed !== undefined && row.nightInAppEnabled !== patch.after21Allowed)) throw new Error("Unconfirmed settings write");
        version = row.version;
        return { allowed: row.inAppEnabled, after21Allowed: row.nightInAppEnabled };
      } catch (error) { return failure(error, request); }
    },
  };
}
