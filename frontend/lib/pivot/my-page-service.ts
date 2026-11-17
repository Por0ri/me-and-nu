import { getProfile, type Session } from "@/lib/api";
import { maskProfileEmail, type MyPageDataSource } from "./my-page";
import { isActiveSession, requirePivotApi } from "./services";
import { createServiceNotificationSettingsSource } from "./notification-settings-service";

/** Bind each source to one session; never carry an identity across session changes. */
export function createServiceMyPageSource(
  readSession: () => Session | null,
  api: { getProfile: (options: { signal: AbortSignal }) => Promise<unknown> } = { getProfile },
  guard: () => void = requirePivotApi,
): MyPageDataSource {
  const owner = readSession();
  const userId = owner?.user?.id;
  return {
    kind: "service",
    notificationSettings: createServiceNotificationSettingsSource(readSession),
    async load({ signal }) {
      const checkSession = () => {
        signal.throwIfAborted();
        guard();
        if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) {
          throw new Error("Profile session changed");
        }
      };
      checkSession();
      const response = await api.getProfile({ signal });
      checkSession();
      if (!response || typeof response !== "object" || Array.isArray(response)) {
        throw new Error("Invalid profile response");
      }
      const profile = response as Record<string, unknown>;
      if (!Number.isSafeInteger(profile.userId) || profile.userId !== userId || profile.accountType !== "user"
        || typeof profile.nickname !== "string" || !profile.nickname.trim() || profile.nickname.trim().length > 30
        || /[\u0000-\u001f\u007f]/.test(profile.nickname)) {
        throw new Error("Invalid profile response");
      }
      // Keep the raw response/email out of React state, storage and diagnostics.
      return { status: "ready", profile: {
        origin: "service", nickname: profile.nickname.trim(), maskedEmail: maskProfileEmail(profile.email),
      } };
    },
  };
}

// Unbound/cross-mode fallback only. HomeRoute supplies the session-bound source.
export const pendingMyPageSource: MyPageDataSource = {
  kind: "service",
  async load() { return { status: "unconfigured", message: "로그인 상태를 확인한 뒤 프로필을 불러와주세요." }; },
};
