import { apiBaseUrl } from "@/lib/api";

/** Explicit opt-in for local demonstration accounts, never actual social login. */
export function isLocalDemoLoginAvailable(): boolean {
  if (
    process.env.NEXT_PUBLIC_ENABLE_LOCAL_DEMO_LOGIN !== "true" ||
    typeof window === "undefined"
  ) {
    return false;
  }

  const loopbackHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  try {
    const apiUrl = new URL(apiBaseUrl);
    return loopbackHosts.has(window.location.hostname) &&
      loopbackHosts.has(apiUrl.hostname) &&
      (apiUrl.protocol === "http:" || apiUrl.protocol === "https:");
  } catch {
    return false;
  }
}
