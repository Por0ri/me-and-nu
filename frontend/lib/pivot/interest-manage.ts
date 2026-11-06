import { getMyTopics, type Session } from "@/lib/api";
import { isActiveSession, requirePivotApi } from "./services";

export type ManagedInterest = { status: "ready" | "empty" };

/** This screen supports anime only; another active Topic is never a substitute. */
export function managedInterest(value: unknown): ManagedInterest {
  if (!value || typeof value !== "object" || !("topics" in value) || !Array.isArray(value.topics)) {
    throw new Error("Invalid interest response");
  }
  let subscribed = false;
  for (const topic of value.topics) {
    if (!topic || typeof topic !== "object" || !Number.isSafeInteger(topic.id) || topic.id <= 0 ||
      typeof topic.code !== "string" || !topic.code.trim() || typeof topic.status !== "string" || !topic.status.trim() ||
      (topic.deletedAt !== null && typeof topic.deletedAt !== "string")) {
      throw new Error("Invalid interest response");
    }
    if (topic.code === "anime" && topic.status === "active" && topic.deletedAt === null) subscribed = true;
  }
  return { status: subscribed ? "ready" : "empty" };
}

/** Bind even same-user refreshes to the exact session that started this read. */
export async function loadManagedInterest(
  readSession: () => Session | null,
  signal: AbortSignal,
  api: Pick<typeof import("@/lib/api"), "getMyTopics"> = { getMyTopics },
  guard: () => void = requirePivotApi,
): Promise<ManagedInterest> {
  const owner = readSession();
  const userId = owner?.user?.id;
  const checkSession = () => {
    signal.throwIfAborted();
    if (!isActiveSession(owner) || readSession() !== owner || owner?.user?.id !== userId) {
      throw new DOMException("Interest session changed", "AbortError");
    }
    guard();
  };
  checkSession();
  try {
    const response = await api.getMyTopics({}, { signal });
    checkSession();
    return managedInterest(response);
  } catch (error) {
    checkSession();
    throw error;
  }
}
