"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

export type ConsumerFlowState = {
  initialHomeTopicId: string;
  availableTopicIds: string[];
};

type ConsumerFlowContextValue = {
  flowState: ConsumerFlowState | null;
  setFlowState: Dispatch<SetStateAction<ConsumerFlowState | null>>;
  addAvailableTopic: (topicId: string) => void;
};

const ConsumerFlowContext = createContext<ConsumerFlowContextValue | null>(
  null,
);

type ConsumerFlowProviderProps = {
  children: ReactNode;
};

const CONSUMER_FLOW_STORAGE_KEY = "menu:consumer-flow";

function isConsumerFlowState(value: unknown): value is ConsumerFlowState {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const state = value as Partial<ConsumerFlowState>;
  return (
    typeof state.initialHomeTopicId === "string" &&
    state.initialHomeTopicId.trim().length > 0 &&
    Array.isArray(state.availableTopicIds) &&
    state.availableTopicIds.every(
      (id) => typeof id === "string" && id.trim().length > 0,
    ) &&
    state.availableTopicIds.includes(state.initialHomeTopicId)
  );
}

/**
 * Mock Consumer Flow의 Onboarding 결과를 같은 탭의 sessionStorage에 보관한다.
 * 실제 사용자 Session이나 Backend persistence / Topic Contract를 의미하지 않는다.
 */
export function ConsumerFlowProvider({ children }: ConsumerFlowProviderProps) {
  const [flowState, setFlowState] = useState<ConsumerFlowState | null>(null);
  const [isRestored, setIsRestored] = useState(false);

  useEffect(() => {
    try {
      const stored: unknown = JSON.parse(
        window.sessionStorage.getItem(CONSUMER_FLOW_STORAGE_KEY) ?? "null",
      );
      setFlowState(
        isConsumerFlowState(stored)
          ? {
              initialHomeTopicId: stored.initialHomeTopicId,
              availableTopicIds: [...stored.availableTopicIds],
            }
          : null,
      );
    } catch {
      // Invalid JSON or unavailable storage uses the existing safe recovery.
      setFlowState(null);
    }
    setIsRestored(true);
  }, []);

  useEffect(() => {
    // Do not overwrite the stored flow with the initial null before restoration.
    if (!isRestored) {
      return;
    }

    try {
      if (flowState) {
        window.sessionStorage.setItem(
          CONSUMER_FLOW_STORAGE_KEY,
          JSON.stringify(flowState),
        );
      } else {
        window.sessionStorage.removeItem(CONSUMER_FLOW_STORAGE_KEY);
      }
    } catch {
      // Keep the in-memory Mock usable when storage is unavailable or full.
    }
  }, [flowState, isRestored]);

  const addAvailableTopic = useCallback((topicId: string) => {
    setFlowState((current) => {
      if (!current || current.availableTopicIds.includes(topicId)) {
        return current;
      }

      return {
        ...current,
        availableTopicIds: [...current.availableTopicIds, topicId],
      };
    });
  }, []);
  const value = useMemo(
    () => ({ flowState, setFlowState, addAvailableTopic }),
    [flowState, addAvailableTopic],
  );

  return (
    <ConsumerFlowContext.Provider value={value}>
      {/* Match SSR / first client render; mount Home with its restored topic. */}
      {/* This also prevents recovery UI from flashing before restoration. */}
      {isRestored ? children : null}
    </ConsumerFlowContext.Provider>
  );
}

export function useConsumerFlow() {
  const context = useContext(ConsumerFlowContext);

  if (!context) {
    throw new Error(
      "useConsumerFlow must be used within ConsumerFlowProvider.",
    );
  }

  return context;
}
