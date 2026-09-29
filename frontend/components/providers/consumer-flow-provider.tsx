"use client";

import {
  createContext,
  useCallback,
  useContext,
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

/**
 * Mock Consumer Flow에서 Onboarding 결과를 Home까지 전달하는 메모리 상태다.
 * 실제 사용자 Session이나 Backend Topic Contract를 의미하지 않는다.
 */
export function ConsumerFlowProvider({ children }: ConsumerFlowProviderProps) {
  const [flowState, setFlowState] = useState<ConsumerFlowState | null>(null);
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
      {children}
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
