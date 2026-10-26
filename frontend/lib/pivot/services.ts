import { apiBaseUrl, getPolicies, getSession } from "@/lib/api";
import { validatePolicies } from "./consent";
import { parsePivotSession, type PivotSession } from "./session";

export type SnsProvider = "kakao" | "naver";
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Requires explicit opt-in and matching loopback hosts; CORS/cookie/CSRF checks still apply. */
export function isLocalApiAllowed(enabled: boolean, browserHost: string, baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    // URL.hostname brackets IPv6; normalize only the bare loopback equivalent.
    const hostname = browserHost === "::1" ? "[::1]" : browserHost;
    return enabled && loopbackHosts.has(hostname) && url.hostname === hostname &&
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
export async function restorePivotSession(): Promise<PivotSession> {
  requireLocalApi();
  const session = parsePivotSession(await getSession());
  if (!session) throw new Error("로그인 상태 응답을 확인하지 못했어요. 다시 로그인해 주세요.");
  return session;
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
