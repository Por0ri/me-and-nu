import { createChatSession, getChatJob, getMyTopics, sendChatMessage, type ChatJob, type FeedCard } from "@/lib/api";
import { aiQuestionError } from "@/lib/ai-question";
import { requirePivotApi } from "./services";
import { mapFeedCard } from "./home-service";
import { mapCoverImage } from "./carousel-data";
import { activeTopicId, isPositiveId } from "./service-data";
import type { SavedCard } from "./saved";

/** 챗봇을 연 곳. 홈은 우리 글 찾기, 상세는 지금 보는 글이 기준이다. */
export type ChatPlace = { kind: "home" } | { kind: "detail"; topicId: number; contentId: number };
export type ChatFact = { name: string; url: string };
export type ChatAnswer = {
  text: string;
  /** 글 찾기 답에서 고른 글 (고른 순서) */
  cards: SavedCard[];
  /** 답에 참고한 우리 글 (보고 있는 글 · 카드로 이미 보인 글은 뺀다) */
  references: SavedCard[];
  /** 정보사전 근거 */
  facts: ChatFact[];
  /** 자료 부족 같은 안내 문구 */
  notes: string[];
};
export type ChatTurn =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; status: "pending" }
  | { id: number; role: "assistant"; status: "answered"; answer: ChatAnswer }
  | { id: number; role: "assistant"; status: "error"; message: string; question: string };
export type ChatSnapshot = { readonly turns: readonly ChatTurn[]; readonly busy: boolean; readonly aiNotice: string };
export type ChatApi = {
  createChatSession: typeof createChatSession;
  sendChatMessage: typeof sendChatMessage;
  getChatJob: typeof getChatJob;
  getMyTopics: typeof getMyTopics;
};
export type ChatConversation = {
  snapshot(): ChatSnapshot;
  subscribe(listener: () => void): () => void;
  ask(question: string): Promise<void>;
};

export const POLL_INTERVAL_MS = 800;
// 답은 보통 6~10초. LLM 이 느린 날도 기다리되 끝없이 기다리지는 않는다.
export const ANSWER_TIMEOUT_MS = 40_000;
export const DEFAULT_AI_NOTICE = "AI가 생성한 답변입니다. 부정확한 내용이 포함될 수 있으니 출처와 근거를 확인해 주세요.";

class ChatTimeout extends Error {}

/** 화면에 보일 오류 문구. 답 말투(반말)에 맞춘다. */
export function chatErrorMessage(error: unknown): string {
  if (error instanceof ChatTimeout) return "답이 너무 늦어. 다시 물어봐 줘.";
  const status = typeof error === "object" && error !== null && "status" in error ? (error as { status: unknown }).status : undefined;
  if (status === 429) return "오늘 물어볼 수 있는 질문을 다 썼어. 내일 다시 물어봐.";
  if (status === 401) return "로그인이 끝났어. 다시 로그인하고 물어봐 줘.";
  if (status === 403 || status === 404) return "지금 화면에서는 물어볼 수 없어. 홈에서 다시 열어 줘.";
  if (typeof status === "number") return "지금은 답을 가져오지 못했어. 잠시 뒤에 다시 물어봐 줘.";
  const { name, message } = typeof error === "object" && error !== null ? error as { name?: unknown; message?: unknown } : {};
  // fetch 는 연결이 끊기면 TypeError 를 던진다.
  if (name === "TypeError") return "인터넷 연결을 확인하고 다시 물어봐 줘.";
  if (typeof message === "string" && message.trim()) return message;
  return "지금은 답을 가져오지 못했어. 잠시 뒤에 다시 물어봐 줘.";
}

/** 고른 글 카드: 홈 피드 카드와 같은 검사를 거친 뒤 작은 목록 카드로 바꾼다. */
export function mapChatCard(card: FeedCard, topicId: number): SavedCard {
  const home = mapFeedCard(card, topicId);
  const cover = home.slides[0];
  const eyebrow = cover && typeof cover.eyebrow === "string" && cover.eyebrow.trim() ? cover.eyebrow : undefined;
  return {
    postId: home.id, contentId: home.detailContentId, topicId, title: home.title,
    image: mapCoverImage(card.imageUrl, home.title, card.id, topicId) ?? cover?.image,
    summary: typeof card.summary === "string" ? card.summary.trim() || undefined : undefined,
    eyebrow,
  };
}

function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

/** 완료된 답을 화면 모양으로 바꾼다. 잘못된 카드 · 근거는 그것만 빼고 답은 살린다. */
export function mapChatAnswer(result: NonNullable<ChatJob["result"]>, topicId: number, anchorContentId?: number): ChatAnswer {
  const text = typeof result.content === "string" ? result.content.trim() : "";
  const cards: SavedCard[] = [];
  for (const card of Array.isArray(result.contents) ? result.contents : []) {
    try { cards.push(mapChatCard(card, topicId)); } catch { /* 이 카드만 뺀다 */ }
  }
  const shown = new Set(cards.map(card => card.contentId));
  if (anchorContentId !== undefined) shown.add(String(anchorContentId));
  const references: SavedCard[] = [];
  for (const source of Array.isArray(result.sources) ? result.sources : []) {
    if (!source || !isPositiveId(source.contentId) || typeof source.title !== "string" || !source.title.trim()) continue;
    const contentId = String(source.contentId);
    if (shown.has(contentId)) continue;
    shown.add(contentId);
    references.push({ postId: contentId, contentId, topicId, title: source.title.trim() });
  }
  const facts: ChatFact[] = [];
  const urls = new Set<string>();
  for (const fact of Array.isArray(result.factSources) ? result.factSources : []) {
    const url = safeUrl(fact?.url);
    if (!url || urls.has(url) || typeof fact.name !== "string" || !fact.name.trim()) continue;
    urls.add(url);
    facts.push({ name: fact.name.trim(), url });
  }
  const notes = (Array.isArray(result.notices) ? result.notices : [])
    .map(notice => typeof notice?.message === "string" ? notice.message.trim() : "")
    .filter(Boolean);
  return { text: text || "답을 비워 보냈어. 다시 물어봐 줘.", cards, references, facts, notes };
}

const browserApi: ChatApi = { createChatSession, sendChatMessage, getChatJob, getMyTopics };
const wait = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

/** 한 곳(홈 · 글 하나)의 대화. 시트를 닫았다 열어도 이어지고, 닫은 사이에 온 답도 남는다. */
export function createChatConversation(place: ChatPlace, deps: {
  api?: ChatApi; guard?: () => void; sleep?: (ms: number) => Promise<void>; now?: () => number; newId?: () => string;
} = {}): ChatConversation {
  const api = deps.api ?? browserApi, guard = deps.guard ?? requirePivotApi, sleep = deps.sleep ?? wait;
  const now = deps.now ?? (() => Date.now()), newId = deps.newId ?? (() => crypto.randomUUID());
  let state: ChatSnapshot = { turns: [], busy: false, aiNotice: DEFAULT_AI_NOTICE };
  let session: { sessionId: number; topicId: number } | null = null;
  let nextId = 1;
  const listeners = new Set<() => void>();
  function update(next: Partial<ChatSnapshot>) {
    state = { ...state, ...next };
    for (const listener of listeners) listener();
  }
  function replace(id: number, turn: ChatTurn) {
    update({ turns: state.turns.map(item => item.id === id ? turn : item) });
  }
  async function topicId(): Promise<number> {
    if (place.kind === "detail") return place.topicId;
    const topic = activeTopicId(await api.getMyTopics());
    if (!topic) throw new Error("구독한 분야가 없어서 물어볼 수 없어.");
    return topic;
  }
  async function openSession(): Promise<{ sessionId: number; topicId: number }> {
    const topic = await topicId();
    const created = await api.createChatSession(topic, place.kind === "detail" ? place.contentId : undefined);
    if (!created || !isPositiveId(created.sessionId)) throw new Error("대화를 시작하지 못했어. 다시 물어봐 줘.");
    if (typeof created.aiNotice === "string" && created.aiNotice.trim()) update({ aiNotice: created.aiNotice.trim() });
    session = { sessionId: created.sessionId, topicId: topic };
    return session;
  }
  async function send(question: string) {
    let current = session ?? await openSession();
    try {
      return { current, accepted: await api.sendChatMessage(current.sessionId, question, newId()) };
    } catch (error) {
      // 대화가 만료됐거나 계정이 바뀌면 예전 대화는 404 가 된다. 새 대화로 한 번만 다시 보낸다.
      if (!(typeof error === "object" && error !== null && (error as { status?: unknown }).status === 404)) throw error;
      session = null;
      current = await openSession();
      return { current, accepted: await api.sendChatMessage(current.sessionId, question, newId()) };
    }
  }
  async function answer(jobId: number) {
    const deadline = now() + ANSWER_TIMEOUT_MS;
    while (now() < deadline) {
      await sleep(POLL_INTERVAL_MS);
      const job = await api.getChatJob(jobId);
      if (job?.status === "completed" && job.result) return job.result;
      if (job?.status === "failed") throw new Error("답을 만들지 못했어. 다시 물어봐 줘.");
    }
    throw new ChatTimeout();
  }
  return {
    snapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    async ask(raw) {
      const question = raw.trim();
      const invalid = aiQuestionError(question);
      if (state.busy || invalid) return;
      const userTurn: ChatTurn = { id: nextId++, role: "user", text: question };
      const pendingId = nextId++;
      update({ busy: true, turns: [...state.turns, userTurn, { id: pendingId, role: "assistant", status: "pending" }] });
      try {
        guard();
        const { current, accepted } = await send(question);
        if (!accepted || !isPositiveId(accepted.jobId)) throw new Error("질문을 보내지 못했어. 다시 물어봐 줘.");
        const result = await answer(accepted.jobId);
        replace(pendingId, { id: pendingId, role: "assistant", status: "answered",
          answer: mapChatAnswer(result, current.topicId, place.kind === "detail" ? place.contentId : undefined) });
      } catch (error) {
        replace(pendingId, { id: pendingId, role: "assistant", status: "error", message: chatErrorMessage(error), question });
      } finally {
        update({ busy: false });
      }
    },
  };
}

const conversations = new Map<string, ChatConversation>();

/** 같은 곳의 대화는 페이지를 새로 열기 전까지 하나만 쓴다. */
export function chatConversation(place: ChatPlace): ChatConversation {
  const key = place.kind === "home" ? "home" : `${place.topicId}:${place.contentId}`;
  let conversation = conversations.get(key);
  if (!conversation) {
    conversation = createChatConversation(place);
    conversations.set(key, conversation);
  }
  return conversation;
}
