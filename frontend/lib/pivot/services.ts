import { apiBaseUrl, apiRequest, getPolicies, getSession, type Session } from "@/lib/api";
import { validatePolicies } from "./consent";

export type SnsProvider = "kakao" | "naver";
const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
const approvedRemoteApis = new Map([
  ["https://test.me-nu.kr", "https://api.test.me-nu.kr"],
  ["https://labs.me-nu.kr", "https://api.labs.me-nu.kr"],
  ["https://checking.me-nu.kr", "https://api.checking.me-nu.kr"],
]);

function isBareOrigin(url: URL): boolean {
  return !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash;
}

/** Local cookie sessions require the same loopback hostname on both sides. */
export function isLocalApiAllowed(enabled: boolean, browserHost: string, baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    return enabled && loopbackHosts.has(browserHost) && url.hostname === browserHost &&
      (url.protocol === "http:" || url.protocol === "https:") &&
      isBareOrigin(url);
  } catch { return false; }
}

/** Keep each approved frontend paired with its own API; arbitrary previews stay disconnected. */
export function isPivotApiAllowed(localEnabled: boolean, browserOrigin: string, baseUrl: string): boolean {
  try {
    const browser = new URL(browserOrigin);
    const api = new URL(baseUrl);
    if (!isBareOrigin(browser) || !isBareOrigin(api)) return false;
    if (approvedRemoteApis.get(browser.origin) === api.origin) return true;
    return browser.protocol === api.protocol && isLocalApiAllowed(localEnabled, browser.hostname, baseUrl);
  } catch { return false; }
}
export function canUsePivotApi(): boolean {
  return typeof window !== "undefined" && isPivotApiAllowed(
    process.env.NEXT_PUBLIC_PIVOT_ENABLE_LOCAL_API === "true", window.location.origin, apiBaseUrl,
  );
}
export function requirePivotApi(): void {
  if (!canUsePivotApi()) throw new Error("이 주소에는 로그인과 콘텐츠 서버가 아직 연결되지 않았어요.");
}

/** Pending accounts intentionally have no public user details until onboarding finishes. */
export function isPendingConsumer(session: Session | null): boolean {
  return session?.authenticated === true && session.sessionState === "onboarding_pending" &&
    session.onboardingCompleted === false && session.user === null;
}
export function isActiveSession(session: Session | null): boolean {
  return session?.authenticated === true && session.sessionState === "active" &&
    session.onboardingCompleted === true && session.user?.accountType === "user" &&
    Number.isSafeInteger(session.user.id) && session.user.id > 0;
}
export function resolvePivotSessionRoute(session: Session | null, pathname: string): string | null {
  // The intro owns its transition, including for accounts still onboarding.
  if (pathname === "/pivot") return null;
  if (isActiveSession(session)) {
    return ["/pivot/login", "/pivot/consent", "/pivot/onboarding"].includes(pathname) ? "/pivot/home" : null;
  }
  if (isPendingConsumer(session)) {
    return pathname === "/pivot/consent" || pathname === "/pivot/onboarding" ? null : "/pivot/consent";
  }
  return pathname === "/pivot/home" || pathname.startsWith("/pivot/home/") ? "/pivot/login" : null;
}
export async function restorePivotSession(): Promise<Session> {
  requirePivotApi();
  return getSession();
}
export async function loadPivotPolicies() {
  requirePivotApi();
  const response = await getPolicies();
  return validatePolicies(response.consentItems);
}

export function oauthErrorMessage(code: string | null): string {
  switch (code) {
    case "AUTH_CANCELLED": return "로그인을 취소했어요. 다시 시작할 수 있어요.";
    case "AUTH_UNAVAILABLE": return "지금은 소셜 로그인을 이용할 수 없어요. 잠시 후 다시 시도해 주세요.";
    case "AUTH_ACCOUNT_EXISTS": return "이미 연결된 계정이 있어요. 기존 로그인 방법을 이용해 주세요.";
    case "AUTH_ACCOUNT_UNAVAILABLE": return "이 계정으로 로그인할 수 없어요. 계정 상태를 확인해 주세요.";
    case null: return "";
    default: return "로그인을 완료하지 못했어요. 다시 시작해 주세요.";
  }
}

export function validateAuthorizationUrl(value: unknown, provider: SnsProvider, baseUrl: string): string {
  if (typeof value !== "string") throw new Error("로그인 주소를 확인할 수 없어요.");
  const url = new URL(value);
  const endpoint = provider === "kakao" ? "https://kauth.kakao.com/oauth/authorize" : "https://nid.naver.com/oauth2.0/authorize";
  if (`${url.origin}${url.pathname}` !== endpoint || url.username || url.password || url.hash ||
    url.searchParams.get("redirect_uri") !== `${baseUrl.replace(/\/$/, "")}/api/v1/auth/login/${provider}/callback` ||
    url.searchParams.get("response_type") !== "code" || !url.searchParams.get("state") || !url.searchParams.get("client_id")) {
    throw new Error("로그인 주소를 확인할 수 없어요. 다시 시도해 주세요.");
  }
  return url.href;
}

/** Use the current browser's anonymous cookie and freshly read CSRF for the OAuth attempt. */
export async function startSnsLogin(provider: SnsProvider): Promise<void> {
  requirePivotApi();
  if (provider !== "kakao" && provider !== "naver") throw new Error("지원하지 않는 로그인 방법이에요.");
  await getSession();
  const result = await apiRequest<{ authorizationUrl: string }>(`/api/v1/auth/login/${provider}`, {
    method: "POST", body: JSON.stringify({ purpose: "signin" }),
  });
  window.location.assign(validateAuthorizationUrl(result.authorizationUrl, provider, apiBaseUrl));
}
