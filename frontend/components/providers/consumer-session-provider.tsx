"use client";

import {
  createContext,
  useContext,
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

export function ConsumerSessionProvider({
  children,
}: ConsumerSessionProviderProps) {
  const [session, setSession] = useState<LoginResult | null>(null);
  const value = useMemo(() => ({ session, setSession }), [session]);

  return (
    <ConsumerSessionContext.Provider value={value}>
      {children}
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
