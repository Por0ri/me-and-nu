"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

import type { LoginResult } from "@/types/consumer";

type ConsumerSessionContextValue = {
  session: LoginResult | null;
  setSession: Dispatch<SetStateAction<LoginResult | null>>;
};

const ConsumerSessionContext = createContext<ConsumerSessionContextValue | null>(
  null,
);

type ConsumerSessionProviderProps = {
  children: ReactNode;
};

const CONSUMER_SESSION_STORAGE_KEY = "menu:consumer-session";

function isMockSession(value: unknown): value is LoginResult {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const session = value as Partial<LoginResult>;
  return (
    session.authStatus === "authenticated" &&
    typeof session.providerId === "string" &&
    session.providerId.trim().length > 0 &&
    (session.signupStatus === "new" || session.signupStatus === "existing")
  );
}

/** Same-tab Mock login state only; this is not OAuth or a Backend session. */
export function ConsumerSessionProvider({
  children,
}: ConsumerSessionProviderProps) {
  const [session, setSession] = useState<LoginResult | null>(null);
  const [isRestored, setIsRestored] = useState(false);

  useEffect(() => {
    try {
      const stored: unknown = JSON.parse(
        window.sessionStorage.getItem(CONSUMER_SESSION_STORAGE_KEY) ?? "null",
      );
      setSession(
        isMockSession(stored)
          ? {
              authStatus: stored.authStatus,
              providerId: stored.providerId,
              signupStatus: stored.signupStatus,
            }
          : null,
      );
    } catch {
      setSession(null);
    }
    setIsRestored(true);
  }, []);

  useEffect(() => {
    if (!isRestored) {
      return;
    }

    try {
      if (session) {
        window.sessionStorage.setItem(
          CONSUMER_SESSION_STORAGE_KEY,
          JSON.stringify(session),
        );
      } else {
        window.sessionStorage.removeItem(CONSUMER_SESSION_STORAGE_KEY);
      }
    } catch {
      // Keep the in-memory Mock usable when storage is unavailable or full.
    }
  }, [session, isRestored]);

  const value = useMemo(() => ({ session, setSession }), [session]);

  return (
    <ConsumerSessionContext.Provider value={value}>
      {/* SSR and first client render agree; consumers mount after restoration. */}
      {isRestored ? children : null}
    </ConsumerSessionContext.Provider>
  );
}

export function useConsumerSession() {
  const context = useContext(ConsumerSessionContext);

  if (!context) {
    throw new Error(
      "useConsumerSession must be used within ConsumerSessionProvider.",
    );
  }

  return context;
}
