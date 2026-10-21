"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  Fragment,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

import { API_UNAUTHORIZED_EVENT, getSession, type Session } from "@/lib/api";
import { isLocalDemoLoginAvailable } from "@/lib/consumer-api/local-demo";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";
import { clearApiContentState } from "@/lib/content-state";
import type { LoginResult } from "@/types/consumer";

type ConsumerSessionContextValue = {
  session: LoginResult | null;
  setSession: Dispatch<SetStateAction<LoginResult | null>>;
  apiSession: Session | null;
  isRestoring: boolean;
  refreshSession: () => Promise<Session>;
};

const ConsumerSessionContext = createContext<ConsumerSessionContextValue | null>(
  null,
);

type ConsumerSessionProviderProps = {
  children: ReactNode;
};

const CONSUMER_SESSION_STORAGE_KEY = "menu:consumer-session";

function routeForSession(
  current: Session | null,
  pathname: string,
  localDemoLoginAvailable: boolean,
): string | null {
  // Opening the local demo entry never signs out or replaces an existing account.
  if (localDemoLoginAvailable && pathname === consumerRoutes.login) return null;
  if (!current?.authenticated || current.sessionState === "restoration_pending") {
    return localDemoLoginAvailable ? consumerRoutes.login : "/#api-test";
  }
  if (current.sessionState === "onboarding_pending") {
    return pathname === consumerRoutes.onboarding ? null : consumerRoutes.onboarding;
  }
  if (current.sessionState === "active") {
    return pathname === consumerRoutes.login || pathname === consumerRoutes.onboarding
      ? consumerRoutes.home
      : null;
  }
  return "/#api-test";
}

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

/** Mock state stays in this tab; API mode restores the server's cookie session. */
export function ConsumerSessionProvider({
  children,
}: ConsumerSessionProviderProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<LoginResult | null>(null);
  const [apiSession, setApiSession] = useState<Session | null>(null);
  const [isRestored, setIsRestored] = useState(false);
  const [isRestoringApi, setIsRestoringApi] = useState(isConsumerApiMode);
  const [localDemoLoginAvailable, setLocalDemoLoginAvailable] = useState(false);
  const requestEpoch = useRef(0);
  const isMounted = useRef(false);
  const previousUserId = useRef<number | null>(null);
  const currentApiSession = useRef<Session | null>(null);

  const clearSession = useCallback(() => {
    clearApiContentState();
    previousUserId.current = null;
    currentApiSession.current = null;
    setApiSession(null);
    setSession(null);
    setIsRestoringApi(false);
  }, []);

  const refreshSession = useCallback(async () => {
    const epoch = ++requestEpoch.current;
    try {
      const current = await getSession();
      if (!isMounted.current || epoch !== requestEpoch.current) {
        throw new Error("Session request was superseded.");
      }
      const userId = current.authenticated ? current.user?.id ?? null : null;
      if (isConsumerApiMode && previousUserId.current !== userId) {
        clearApiContentState();
        previousUserId.current = userId;
      }
      currentApiSession.current = current;
      setApiSession(current);
      setSession(null);
      setIsRestoringApi(false);
      return current;
    } catch (error) {
      if (isMounted.current && epoch === requestEpoch.current) {
        if (currentApiSession.current?.authenticated) {
          // A temporary read failure must not discard a successful onboarding POST.
          // Actual 401 responses invalidate the session through onUnauthorized.
          setIsRestoringApi(false);
        } else {
          clearSession();
        }
      }
      throw error;
    }
  }, [clearSession]);

  useEffect(() => {
    isMounted.current = true;
    if (isConsumerApiMode) {
      // The helper checks browser origin; defer it until after hydration.
      setLocalDemoLoginAvailable(isLocalDemoLoginAvailable());
      // A 401 clears state directly, so a failed session request cannot recurse.
      const onUnauthorized = () => {
        requestEpoch.current += 1;
        clearSession();
      };
      window.addEventListener(API_UNAUTHORIZED_EVENT, onUnauthorized);
      void refreshSession().catch(() => undefined);
      return () => {
        isMounted.current = false;
        requestEpoch.current += 1;
        window.removeEventListener(API_UNAUTHORIZED_EVENT, onUnauthorized);
      };
    }
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
    return () => {
      isMounted.current = false;
      requestEpoch.current += 1;
    };
  }, [clearSession, refreshSession]);

  const redirectTarget = isConsumerApiMode && !isRestoringApi
    ? routeForSession(apiSession, pathname, localDemoLoginAvailable)
    : null;

  useEffect(() => {
    if (redirectTarget) router.replace(redirectTarget);
  }, [redirectTarget, router]);

  useEffect(() => {
    if (isConsumerApiMode || !isRestored) {
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

  const isRestoring = isConsumerApiMode ? isRestoringApi : !isRestored;
  const value = useMemo(
    () => ({ session, setSession, apiSession, isRestoring, refreshSession }),
    [session, apiSession, isRestoring, refreshSession],
  );
  const isLocalDemoLoginRoute = localDemoLoginAvailable && pathname === consumerRoutes.login;
  const canRender = isConsumerApiMode
    ? !isRestoringApi && (apiSession?.authenticated === true || isLocalDemoLoginRoute) && !redirectTarget
    : isRestored;

  return (
    <ConsumerSessionContext.Provider value={value}>
      {/* API login is exposed only through the explicitly enabled local demo entry. */}
      {canRender ? (
        <Fragment key={isConsumerApiMode ? apiSession?.user?.id : "mock"}>
          {children}
        </Fragment>
      ) : null}
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
