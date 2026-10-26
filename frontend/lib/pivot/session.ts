// Pivot's runtime boundary for backend-labs /api/v1/auth/session.
// Keep the legacy Consumer Session and its authentication flow independent.
export type PivotSessionUser = { id: number; accountType: "user" };
export type PivotPendingSession = {
  authenticated: true; sessionState: "onboarding_pending";
  onboardingCompleted: false; user: null; csrfToken: string;
};
export type PivotSession = PivotPendingSession | {
  authenticated: false; sessionState: "anonymous";
  onboardingCompleted: false; user: null; csrfToken: string;
} | {
  authenticated: true; sessionState: "active";
  onboardingCompleted: true; user: PivotSessionUser; csrfToken: string;
} | ({
  authenticated: true; sessionState: "restoration_pending"; csrfToken: string;
} & ({ onboardingCompleted: false; user: null } | { onboardingCompleted: true; user: PivotSessionUser }));

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Validate only the session contract. Cookie expiry remains server-owned. */
export function parsePivotSession(value: unknown): PivotSession | null {
  if (!isRecord(value) || typeof value.csrfToken !== "string" || !value.csrfToken.trim()) return null;
  const { authenticated, sessionState, onboardingCompleted, user, csrfToken } = value;
  if (authenticated === false && sessionState === "anonymous" && onboardingCompleted === false && user === null) {
    return { authenticated, sessionState, onboardingCompleted, user, csrfToken };
  }
  if (authenticated !== true) return null;
  // The BE deliberately hides the user until onboarding is complete.
  if (onboardingCompleted === false && user === null &&
      (sessionState === "onboarding_pending" || sessionState === "restoration_pending")) {
    return { authenticated, sessionState, onboardingCompleted, user, csrfToken };
  }
  if (onboardingCompleted === true && (sessionState === "active" || sessionState === "restoration_pending") &&
      isRecord(user) && user.accountType === "user" && typeof user.id === "number" &&
      Number.isSafeInteger(user.id) && user.id > 0) {
    return { authenticated, sessionState, onboardingCompleted, user: { id: user.id, accountType: "user" }, csrfToken };
  }
  return null;
}

export function isPendingOnboarding(value: unknown): value is PivotPendingSession {
  return parsePivotSession(value)?.sessionState === "onboarding_pending";
}
