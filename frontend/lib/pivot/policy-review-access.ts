import "server-only";
import { isLoopbackPolicyUrl } from "./policy-review";

type ReviewEnvironment = { nodeEnv?: string; deploymentEnv?: string; enabled?: string };

/** No production/preview override, no cookies, no CORS, no static file access. */
export function canReadLocalPolicyReview(request: Request, environment: ReviewEnvironment): boolean {
  if (environment.nodeEnv !== "development" || environment.deploymentEnv || environment.enabled !== "true") return false;
  if (!isLoopbackPolicyUrl(request.url)) return false;
  const url = new URL(request.url), headers = request.headers;
  // Next normalizes request.url to localhost even when bound/requested at 127.0.0.1.
  // Validate BOTH loopback hosts and the port, then use the actual Host for origin checks.
  const host = headers.get("host"), browserUrl = `${url.protocol}//${host}`;
  if (!host || !isLoopbackPolicyUrl(browserUrl)) return false;
  const browser = new URL(browserUrl);
  if (browser.host !== host || browser.port !== url.port) return false;
  if (headers.has("origin") && headers.get("origin") !== browser.origin) return false;
  if (headers.has("referer") && (!isLoopbackPolicyUrl(headers.get("referer")!) || new URL(headers.get("referer")!).origin !== browser.origin)) return false;
  if (headers.has("sec-fetch-site") && !["same-origin", "none"].includes(headers.get("sec-fetch-site")!)) return false;
  // Next's local server can add these headers itself; reject proxy/remote values.
  if (headers.has("forwarded")) return false;
  if (headers.has("x-forwarded-host") && headers.get("x-forwarded-host") !== host) return false;
  if (headers.has("x-forwarded-for") && !["127.0.0.1", "::1", "::ffff:127.0.0.1"].includes(headers.get("x-forwarded-for")!)) return false;
  return true;
}
