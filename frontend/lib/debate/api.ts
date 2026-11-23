/**
 * 토론방 백엔드 API 호출 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장).
 * client.ts가 데이터 출처를 "api"로 고르면 화면이 이 함수들을 쓴다. 함수 이름과 돌려주는 모양은
 * 가짜 데이터(mock-store.ts)와 같다. 목록 응답의 다음 쪽(nextCursor)은 아직 화면에서 쓰지 않는다.
 */
import { apiBaseUrl, apiRequest, getMyTopics } from "@/lib/api";
import {
  DebateError,
  type CreateDebateInput,
  type DebateListQuery,
  type DebateMessage,
  type DebateMessagePage,
  type DebateSearchResult,
  type DebateSideKey,
  type DebateStreamEvent,
  type DebateSubscribeOptions,
  type DebateSummary,
  type DebateWorkOption,
  type VoteTickets,
} from "./types";

type ListResponse = { items: DebateSummary[]; nextCursor: string | null };
type VoteResponse = { debate: DebateSummary; tickets: VoteTickets; opened: boolean };

let topicRequest: Promise<number> | null = null;

/** 지금 회원의 분야 번호. 주제 올리기·검색·목록에 쓴다 (다른 화면처럼 분야 기준). */
export function currentTopicId(): Promise<number> {
  topicRequest ??= getMyTopics().then((response) => {
    const topic = response.topics.find((item) => item.status === "active") ?? response.topics[0];
    if (!topic) throw new DebateError("TOPIC_NOT_FOUND", "관심 분야가 없어요. 온보딩을 먼저 마쳐 주세요.");
    return topic.id;
  }).catch((error: unknown) => {
    topicRequest = null;
    throw error;
  });
  return topicRequest;
}

export async function listDebates(query: DebateListQuery): Promise<DebateSummary[]> {
  const params = new URLSearchParams({ status: query.status, limit: "50", topicId: String(await currentTopicId()) });
  if (query.status === "voting" && query.sort) params.set("sort", query.sort);
  if (query.status === "ended") {
    if (query.q?.trim()) params.set("q", query.q.trim());
  }
  if (query.entityId) params.set("entityId", String(query.entityId));
  return (await apiRequest<ListResponse>(`/api/v1/debates?${params}`)).items;
}

export function getDebate(debateId: number): Promise<DebateSummary> {
  return apiRequest<DebateSummary>(`/api/v1/debates/${debateId}`);
}

export function getVoteTickets(): Promise<VoteTickets> {
  return apiRequest<VoteTickets>("/api/v1/users/me/debate-tickets");
}

export function voteDebate(debateId: number): Promise<VoteResponse> {
  return apiRequest<VoteResponse>(`/api/v1/debates/${debateId}/vote`, { method: "PUT" });
}

export async function cancelVote(debateId: number): Promise<{ debate: DebateSummary; tickets: VoteTickets }> {
  return apiRequest<VoteResponse>(`/api/v1/debates/${debateId}/vote`, { method: "DELETE" }, 200);
}

export async function listMyDebates(): Promise<DebateSummary[]> {
  return (await apiRequest<ListResponse>("/api/v1/users/me/debates")).items;
}

export async function listMyVotes(): Promise<DebateSummary[]> {
  return (await apiRequest<ListResponse>("/api/v1/users/me/debate-votes")).items;
}

export async function createDebate(input: CreateDebateInput): Promise<DebateSummary> {
  return apiRequest<DebateSummary>("/api/v1/debates", {
    method: "POST",
    body: JSON.stringify({
      topicId: await currentTopicId(),
      title: input.title,
      description: input.description.trim() || null,
      sideA: input.sideA,
      sideB: input.sideB,
      workIds: input.workIds,
    }),
  }, 201);
}

export async function deleteDebate(debateId: number): Promise<void> {
  await apiRequest<void>(`/api/v1/debates/${debateId}`, { method: "DELETE" });
}

/** 주제 올리기의 작품 고르기 (D-17). 검색어가 비면 서버도 빈 목록을 준다. */
export async function searchWorks(text: string): Promise<DebateWorkOption[]> {
  if (!text.trim()) return [];
  const params = new URLSearchParams({ topicId: String(await currentTopicId()), q: text.trim(), limit: "10" });
  const response = await apiRequest<{ items: DebateWorkOption[] }>(`/api/v1/debates/works?${params}`);
  return response.items.map(({ entityId, name, aliases }) => ({ entityId, name, aliases }));
}

/** 검색 입력창 하나 (D-16). record는 사용자가 검색을 확정했을 때만 켠다 (검색 기록용). */
export async function searchDebates(text: string, options: { record?: boolean; pickedEntityId?: number } = {}): Promise<DebateSearchResult> {
  if (!text.trim()) return { works: [], debates: [] };
  const params = new URLSearchParams({ topicId: String(await currentTopicId()), q: text.trim() });
  if (options.record) params.set("record", "true");
  if (options.pickedEntityId) params.set("pickedEntityId", String(options.pickedEntityId));
  return apiRequest<DebateSearchResult>(`/api/v1/debates/search?${params}`);
}

/** 주제 올리기 화면의 '비슷한 주제': 제목에서 가장 긴 단어(두 글자 이상)로 찾는다. 기록은 남기지 않는다. */
export async function similarDebates(title: string): Promise<DebateSummary[]> {
  const word = title.split(/[\s,?!.·]+/).filter((item) => item.length >= 2).sort((left, right) => right.length - left.length)[0];
  if (!word) return [];
  return (await searchDebates(word)).debates.slice(0, 3);
}

/* ───────────── 토론방 안 (D-09~12) ───────────── */

/** 토론방 참여 (D-18). 이미 참여 중이면 그대로. */
export function joinDebate(debateId: number): Promise<DebateSummary> {
  return apiRequest<DebateSummary>(`/api/v1/debates/${debateId}/membership`, { method: "PUT" });
}

/** 토론방 나가기 (D-19). 참여 기록과 고른 진영이 지워지고, 다시 참여하면 그때부터의 채팅만 보인다. */
export function leaveDebate(debateId: number): Promise<DebateSummary> {
  return apiRequest<DebateSummary>(`/api/v1/debates/${debateId}/membership`, { method: "DELETE" }, 200);
}

/** 진영 고르기·바꾸기. 채팅과는 별개라 언제든 바꿀 수 있다. */
export function chooseSide(debateId: number, side: DebateSideKey): Promise<DebateSummary> {
  return apiRequest<DebateSummary>(`/api/v1/debates/${debateId}/side`, { method: "PUT", body: JSON.stringify({ side }) });
}

/** 최근 채팅을 오래된 순으로. cursor를 주면 그보다 이전 채팅 */
export function listMessages(debateId: number, cursor?: string): Promise<DebateMessagePage> {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor) params.set("cursor", cursor);
  return apiRequest<DebateMessagePage>(`/api/v1/debates/${debateId}/messages?${params}`);
}

export function sendMessage(debateId: number, body: string): Promise<DebateMessage> {
  return apiRequest<DebateMessage>(`/api/v1/debates/${debateId}/messages`, { method: "POST", body: JSON.stringify({ body }) }, 201);
}

/**
 * 실시간 스트림(SSE). 브라우저의 EventSource가 연결이 끊기면 알아서 다시 붙고,
 * 그때 마지막으로 받은 채팅 번호(Last-Event-ID)를 보내서 놓친 채팅을 받는다.
 * 돌려주는 함수를 부르면 연결을 닫는다.
 */
export function subscribeDebate(
  debateId: number, onEvent: (event: DebateStreamEvent) => void, options: DebateSubscribeOptions = {},
): () => void {
  const query = options.after !== undefined ? `?after=${options.after}` : "";
  const source = new EventSource(`${apiBaseUrl}/api/v1/debates/${debateId}/stream${query}`, { withCredentials: true });
  let stopped = false;
  const stop = () => {
    stopped = true;
    source.close();
  };
  const read = (event: Event) => JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>;

  options.onConnection?.("connecting");
  source.onopen = () => { if (!stopped) options.onConnection?.("live"); };
  source.onerror = () => {
    if (stopped) return;
    // CONNECTING: 잠깐 끊겨서 브라우저가 다시 붙는 중. CLOSED: 서버가 연결을 받아 주지 않아(401·409·429) 멈췄다.
    options.onConnection?.(source.readyState === EventSource.CLOSED ? "stopped" : "reconnecting");
  };
  source.addEventListener("chat", (event) => {
    const { messageCount, ...message } = read(event);
    onEvent({ type: "chat", message: message as DebateMessage, messageCount: typeof messageCount === "number" ? messageCount : undefined });
  });
  source.addEventListener("sides", (event) => onEvent({ type: "sides", ...(read(event) as Omit<Extract<DebateStreamEvent, { type: "sides" }>, "type">) }));
  source.addEventListener("resync", () => onEvent({ type: "resync" }));
  for (const type of ["closed", "session-expired"] as const) {
    // 서버가 연결을 끝내도 EventSource는 다시 붙으려 하므로 여기서 직접 닫는다.
    source.addEventListener(type, () => {
      stop();
      onEvent({ type });
    });
  }
  return stop;
}
