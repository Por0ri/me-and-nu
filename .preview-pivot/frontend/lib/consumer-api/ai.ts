import {
  mockContentAIResponsesByTopicId,
  mockHomeAIAnswersByTopicId,
  mockHomeAIQuestionTargets,
} from "@/mocks/ai";
import { mockContentsByTopicId } from "@/mocks/contents";
import { mockTopics } from "@/mocks/topics";
import {
  ApiError,
  createChatSession,
  getChatJob,
  getTopics,
  sendChatMessage,
  type ChatJob,
} from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";
import type {
  ContentAIRequest,
  ContentAIResponse,
  HomeAIRequest,
  HomeAIResponse,
} from "@/types/ai";

// ---------- 실제 API (API-048 → API-052 → API-053) ----------

const POLL_INTERVAL_MS = 800;
const POLL_TIMEOUT_MS = 30_000;
// 같은 화면(분야+글)에서는 같은 대화를 이어 쓴다.
const chatSessionIds = new Map<string, number>();

async function chatSessionFor(topicId: number, contentId: number | null): Promise<number> {
  const key = `${topicId}:${contentId ?? "home"}`;
  const cached = chatSessionIds.get(key);
  if (cached) return cached;
  const session = await createChatSession(topicId, contentId ?? undefined);
  chatSessionIds.set(key, session.sessionId);
  return session.sessionId;
}

async function askChatApi(
  topicId: string,
  contentId: string | null,
  question: string,
): Promise<NonNullable<ChatJob["result"]>> {
  const numericTopicId = Number(topicId);
  const numericContentId = contentId ? Number(contentId) : null;
  const trimmed = question.trim();
  if (!trimmed) throw new Error("A question is required.");

  let accepted;
  try {
    accepted = await sendChatMessage(await chatSessionFor(numericTopicId, numericContentId), trimmed, crypto.randomUUID());
  } catch (cause) {
    // 로그인 계정이 바뀌면 예전 대화는 404가 된다. 새 대화로 한 번만 다시 보낸다.
    if (!(cause instanceof ApiError && cause.status === 404)) throw cause;
    chatSessionIds.delete(`${numericTopicId}:${numericContentId ?? "home"}`);
    accepted = await sendChatMessage(await chatSessionFor(numericTopicId, numericContentId), trimmed, crypto.randomUUID());
  }

  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    const job = await getChatJob(accepted.jobId);
    if (job.status === "completed" && job.result) return job.result;
    if (job.status === "failed") throw new Error(job.error?.message ?? "AI answer failed.");
  }
  throw new Error("AI answer timed out.");
}

function sourcesFromChat(result: NonNullable<ChatJob["result"]>, topicId: string) {
  return result.sources.map((source) => ({
    id: String(source.contentId),
    name: source.title,
    url: `${consumerRoutes.content(String(source.contentId))}?topicId=${encodeURIComponent(topicId)}`,
  }));
}

export async function sendContentAIQuestion({
  contentId,
  question,
  topicContext,
}: ContentAIRequest): Promise<ContentAIResponse> {
  if (isConsumerApiMode) {
    const result = await askChatApi(topicContext.topicId, contentId, question);
    return {
      answer: result.content,
      sources: sourcesFromChat(result, topicContext.topicId),
      evidenceStatus: result.contextCoverage === "full" ? "sufficient" : "insufficient",
      affectsPreference: false,
    };
  }
  const trimmedQuestion = question.trim();

  if (!trimmedQuestion) {
    throw new Error("A Content AI question is required.");
  }

  const topicId = topicContext.topicId;

  if (!mockTopics.some((topic) => topic.id === topicId)) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  if (
    !mockContentsByTopicId[topicId]?.some((content) => content.id === contentId)
  ) {
    throw new Error(`Unknown Mock content in ${topicId}: ${contentId}`);
  }

  // 질문 내용 분석이나 저장 없이 고정 Fixture를 반환한다.
  // sourceTopicId는 참고 출처의 분야이므로 요청 Context 검증에 사용하지 않는다.
  const response =
    mockContentAIResponsesByTopicId[topicId]?.[contentId];

  if (!response) {
    throw new Error(
      `Unknown Mock AI context: ${topicContext.topicId}/${contentId}`,
    );
  }

  return structuredClone(response);
}

// Home 전용 Mock facade. 실제 연동의 호출 흐름과 DTO 변환은 이 경계 안에서 처리한다.
export async function sendHomeAIQuestion({
  topicContext,
  question,
}: HomeAIRequest): Promise<HomeAIResponse> {
  if (isConsumerApiMode) {
    const result = await askChatApi(topicContext.topicId, null, question);
    if (result.type === "topicSwitchSuggested" && result.targetTopicId) {
      const { topics } = await getTopics();
      const target = topics.find((topic) => topic.id === result.targetTopicId);
      if (target) {
        return {
          type: "topicSwitchSuggested",
          targetTopic: { id: String(target.id), name: target.name, code: target.code },
          message: result.content,
        };
      }
    }
    return { type: "answer", answer: result.content, sources: sourcesFromChat(result, topicContext.topicId) };
  }
  const trimmedQuestion = question.trim();

  if (!trimmedQuestion) {
    throw new Error("A Home AI question is required.");
  }

  const topicId = topicContext.topicId;

  if (!mockTopics.some((topic) => topic.id === topicId)) {
    throw new Error(`Unknown Mock topic: ${topicId}`);
  }

  const questionTarget = mockHomeAIQuestionTargets.find(
    (fixture) => fixture.question === trimmedQuestion,
  );

  if (questionTarget && questionTarget.topicId !== topicId) {
    const targetTopic = mockTopics.find(
      (topic) => topic.id === questionTarget.topicId,
    );

    if (!targetTopic) {
      throw new Error(`Unknown Mock target topic: ${questionTarget.topicId}`);
    }

    return {
      type: "topicSwitchSuggested",
      targetTopic: { ...targetTopic },
      message: `이 질문은 ${targetTopic.name} 분야에서 다루는 질문이에요. ${targetTopic.name} 분야에서 질문해 주세요.`,
    };
  }

  const answer = mockHomeAIAnswersByTopicId[topicId];

  if (!answer) {
    throw new Error(`Unknown Mock Home AI context: ${topicId}`);
  }

  return { type: "answer", answer };
}
