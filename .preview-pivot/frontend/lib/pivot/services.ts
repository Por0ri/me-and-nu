import { apiBaseUrl, getPolicies, getSession, type Session } from "@/lib/api";
import { validatePolicies } from "./consent";

export type SnsProvider = "kakao" | "naver";
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Slice A can only read an explicitly enabled loopback backend. */
export function isLocalApiAllowed(enabled: boolean, browserHost: string, baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    return enabled && loopbackHosts.has(browserHost) && loopbackHosts.has(url.hostname) &&
      (url.protocol === "http:" || url.protocol === "https:") &&
      !url.username && !url.password;
  } catch { return false; }
}
export function canUsePivotApi(): boolean {
  return typeof window !== "undefined" && isLocalApiAllowed(
    process.env.NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API === "true", window.location.hostname, apiBaseUrl,
  );
}
function requireLocalApi() {
  if (!canUsePivotApi()) throw new Error("약관과 세션을 확인할 로컬 서버가 아직 연결되지 않았어요.");
}
export function isPendingConsumer(session: Session | null): boolean {
  return session?.authenticated === true && session.sessionState === "onboarding_pending" &&
    session.onboardingCompleted === false && session.user?.accountType === "consumer" &&
    Number.isSafeInteger(session.user.id) && session.user.id > 0;
}
export async function restorePivotSession() {
  requireLocalApi();
  return getSession();
}
export async function loadPivotPolicies() {
  requireLocalApi();
  const response = await getPolicies();
  return validatePolicies(response.consentItems);
}

/** Replace only after the OAuth start/callback/CSRF contract has been agreed. */
export async function startSnsLogin(provider: SnsProvider): Promise<never> {
  const name = provider === "kakao" ? "카카오" : "네이버";
  throw new Error(`${name} 로그인을 준비하고 있어요. 연결이 완료되면 이용할 수 있어요.`);
}
