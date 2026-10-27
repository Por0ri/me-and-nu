import { ApiError, apiBaseUrl, getPolicies, getSession, startOAuthLogin } from "@/lib/api";
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
  if (typeof window === "undefined" || isIsolatedPivotPath(window.location.pathname)) return false;
  return isLocalApiAllowed(
    process.env.NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API === "true", window.location.hostname, apiBaseUrl,
  ) || isTestApiAllowed(
    process.env.NEXT_PUBLIC_PIVOT_ENABLE_TEST_API === "true", window.location.origin, apiBaseUrl,
  );
}
/** The test deployment is distinct from the isolated /pivot/preview demo route. */
export function isTestApiAllowed(enabled: boolean, browserOrigin: string, baseUrl: string): boolean {
  return enabled && browserOrigin === "https://test.me-nu.kr" &&
    (baseUrl === "https://api.test.me-nu.kr" || baseUrl === "https://api.test.me-nu.kr/");
}
export function isIsolatedPivotPath(pathname = ""): boolean {
  return ["/pivot/preview", "/pivot/demo", "/pivot/home/gesture-spike"]
    .some((path) => pathname === path || pathname.startsWith(`${path}/`));
}
function requirePivotApi() {
  if (!canUsePivotApi()) throw new Error("로그인과 세션을 확인할 서버가 아직 연결되지 않았어요.");
}
export async function restorePivotSession(): Promise<PivotSession> {
  requirePivotApi();
  const session = parsePivotSession(await getSession());
  if (!session) throw new Error("로그인 상태 응답을 확인하지 못했어요. 다시 로그인해 주세요.");
  return session;
}
export async function loadPivotPolicies() {
  requirePivotApi();
  const response = await getPolicies();
  return validatePolicies(response.consentItems);
}

const authorizationEndpoints: Record<SnsProvider, string> = {
  kakao: "https://kauth.kakao.com/oauth/authorize",
  naver: "https://nid.naver.com/oauth2.0/authorize",
};
export function isSnsAuthorizationUrl(provider: SnsProvider, value: unknown): value is string {
  if ((provider !== "kakao" && provider !== "naver") || typeof value !== "string") return false;
  try {
    const url = new URL(value);
    return value.startsWith("https://") && value === value.trim() && !/[\u0000-\u0020\\]/.test(value) &&
      !url.username && !url.password && !url.hash &&
      `${url.origin}${url.pathname}` === authorizationEndpoints[provider];
  } catch { return false; }
}

export async function startSnsLogin(provider: SnsProvider): Promise<void> {
  if (provider !== "kakao" && provider !== "naver") throw new Error("지원하지 않는 로그인 방식이에요.");
  requirePivotApi();
  let response: Awaited<ReturnType<typeof startOAuthLogin>>;
  try {
    // Always reacquire CSRF from the current cookie, including on a retry.
    await restorePivotSession();
    requirePivotApi();
    response = await startOAuthLogin(provider);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
      throw new Error("로그인 세션을 다시 확인해야 해요. 로그인 버튼을 눌러 다시 시도해 주세요.");
    }
    throw new Error("로그인을 시작하지 못했어요. 잠시 후 다시 시도해 주세요.");
  }
  if (!isSnsAuthorizationUrl(provider, response?.authorizationUrl)) {
    throw new Error("안전한 로그인 주소를 확인하지 못했어요. 다시 시도해 주세요.");
  }
  requirePivotApi();
  window.location.assign(response.authorizationUrl);
}
