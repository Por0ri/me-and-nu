/**
 * 토론방 백엔드 API 호출 (설계 docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장).
 * client.ts가 데이터 출처를 "api"로 고르면 화면이 이 함수들을 쓴다. 함수 이름과 돌려주는 모양은
 * 가짜 데이터(mock-store.ts)와 같다. 목록 응답의 다음 쪽(nextCursor)은 아직 화면에서 쓰지 않는다.
 */
import { apiRequest, getMyTopics } from "@/lib/api";
import {
  DebateError,
  type CreateDebateInput,
  type DebateListQuery,
  type DebateSearchResult,
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
    if (query.result) params.set("result", query.result);
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
