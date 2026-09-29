"use client";

import {
  createContext,
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

import { useConsumerSession } from "@/components/providers/consumer-session-provider";
import { getMyTopics } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";

export type ConsumerFlowState = {
  initialHomeTopicId: string;
  availableTopicIds: string[];
};

type ConsumerFlowContextValue = {
  flowState: ConsumerFlowState | null;
  setFlowState: Dispatch<SetStateAction<ConsumerFlowState | null>>;
  addAvailableTopic: (topicId: string) => Promise<void>;
  refreshTopics: () => Promise<void>;
  isRestoring: boolean;
  restoreError: string | null;
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
 * Mock flow is restored from this tab; API flow comes from the active user's Topics.
 */
export function ConsumerFlowProvider({ children }: ConsumerFlowProviderProps) {
  const { apiSession, isRestoring: isRestoringSession } = useConsumerSession();
  const [flowState, setFlowState] = useState<ConsumerFlowState | null>(null);
  const [isRestored, setIsRestored] = useState(false);
  const [loadedUserId, setLoadedUserId] = useState<number | null>(null);
  const [isLoadingTopics, setIsLoadingTopics] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const requestEpoch = useRef(0);
  const apiUserId = apiSession?.sessionState === "active"
    ? apiSession.user?.id ?? null
    : null;

  const refreshTopics = useCallback(async () => {
    if (!isConsumerApiMode || apiUserId === null) return;
    const epoch = ++requestEpoch.current;
    setIsLoadingTopics(true);
    setRestoreError(null);
    try {
      const result = await getMyTopics();
      if (epoch !== requestEpoch.current) return;
      const activeIds = result.topics
        .filter((topic) => topic.status === "active" && topic.deletedAt === null)
        .map((topic) => String(topic.id));
      setFlowState((current) => activeIds.length > 0
        ? {
            initialHomeTopicId: current && activeIds.includes(current.initialHomeTopicId)
              ? current.initialHomeTopicId
              : activeIds[0],
            availableTopicIds: activeIds,
          }
        : null);
    } catch (error) {
      if (epoch === requestEpoch.current) {
        // Keep the current user's flow so a successful add can retry only this read.
        setRestoreError("분야 정보를 불러오지 못했습니다. 다시 시도해 주세요.");
      }
      throw error;
    } finally {
      if (epoch === requestEpoch.current) {
        setLoadedUserId(apiUserId);
        setIsLoadingTopics(false);
      }
    }
  }, [apiUserId]);

  useEffect(() => {
    if (!isConsumerApiMode) return;
    requestEpoch.current += 1;
    setFlowState(null);
    setLoadedUserId(null);
    setIsLoadingTopics(false);
    setRestoreError(null);
    if (!isRestoringSession && apiUserId !== null) {
      void refreshTopics().catch(() => undefined);
    }
    // Invalidate old-user requests and requests from an unmounted provider.
    return () => { requestEpoch.current += 1; };
  }, [apiUserId, isRestoringSession, refreshTopics]);

  useEffect(() => {
    if (isConsumerApiMode) return;
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
    if (isConsumerApiMode || !isRestored) {
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

  const addAvailableTopic = useCallback(async (topicId: string) => {
    if (isConsumerApiMode) {
      await refreshTopics();
      return;
    }
    setFlowState((current) => {
      if (!current || current.availableTopicIds.includes(topicId)) {
        return current;
      }

      return {
        ...current,
        availableTopicIds: [...current.availableTopicIds, topicId],
      };
    });
  }, [refreshTopics]);
  const isRestoring = isConsumerApiMode
    ? isRestoringSession || isLoadingTopics || (apiUserId !== null && loadedUserId !== apiUserId)
    : !isRestored;
  const visibleFlowState = isConsumerApiMode && loadedUserId !== apiUserId ? null : flowState;
  const value = useMemo(
    () => ({ flowState: visibleFlowState, setFlowState, addAvailableTopic, refreshTopics, isRestoring, restoreError }),
    [visibleFlowState, addAvailableTopic, refreshTopics, isRestoring, restoreError],
  );
  const canRender = isConsumerApiMode
    ? !isRestoringSession && (apiUserId === null || loadedUserId === apiUserId)
    : isRestored;

  return (
    <ConsumerFlowContext.Provider value={value}>
      {/* Match SSR / first client render; mount Home with its restored topic. */}
      {/* This also prevents recovery UI from flashing before restoration. */}
      {canRender ? children : null}
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
