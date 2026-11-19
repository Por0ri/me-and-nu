/**
 * 토론방 화면이 쓰는 데이터 모양.
 * 백엔드 설계(docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장)의 API 응답과 같은 모양으로 둔다.
 * 지금은 가짜 데이터(mock-store.ts)가 채우고, API가 생기면 같은 모양으로 서버가 채운다.
 */

/** voting 투표 중 → open 진행 중 → closed 종료, 또는 voting → expired 소멸 */
export type DebateStatus = "voting" | "open" | "closed" | "expired";
export type DebateSideKey = "a" | "b";
/** 종료된 방의 결과. a·b 이긴 쪽, draw 동률, none 참여자 없음 */
export type DebateResult = "a" | "b" | "draw" | "none";

export type DebateWork = { entityId: number; name: string };

export type DebateSideSummary = { label: string; count: number; percent: number | null };

export type DebateSummary = {
  debateId: number;
  /** 분야 번호 (API 응답에만 있음) */
  topicId?: number;
  title: string;
  description: string | null;
  sideA: DebateSideSummary;
  sideB: DebateSideSummary;
  works: DebateWork[];
  status: DebateStatus;
  voteCount: number;
  voteThreshold: number;
  votingEndsAt: string;
  openedAt: string | null;
  closesAt: string | null;
  /** 진영을 고른 사람 수 */
  participantCount: number;
  messageCount: number;
  result: DebateResult | null;
  myVote: boolean;
  mySide: DebateSideKey | null;
  isMine: boolean;
  createdAt: string;
};

export type VoteTickets = { balance: number; max: number; nextRefillAt: string | null };

export type DebateMessage = {
  messageId: number;
  /** 보낸 순간의 진영. 진영 선택은 채팅과 별개라 고르지 않았으면 null */
  side: DebateSideKey | null;
  nickname: string;
  body: string;
  createdAt: string;
  isMine: boolean;
};

export type DebateWorkOption = DebateWork & { aliases: string[] };

export type DebateSearchResult = { works: DebateWorkOption[]; debates: DebateSummary[] };

export type DebateListQuery =
  | { status: "voting"; sort?: "recent" | "votes"; entityId?: number }
  | { status: "open"; entityId?: number }
  | { status: "ended"; result?: "all" | "closed" | "expired"; entityId?: number; q?: string };

export type CreateDebateInput = {
  title: string;
  description: string;
  sideA: string;
  sideB: string;
  workIds: number[];
};

/** 실시간 스트림 이벤트 (설계 3.2의 chat·sides·closed) */
export type DebateStreamEvent =
  | { type: "chat"; message: DebateMessage }
  | { type: "sides"; sideA: DebateSideSummary; sideB: DebateSideSummary; participantCount: number }
  | { type: "closed"; debate: DebateSummary };

/** 서버 오류 코드(설계 7장)와 같은 이름을 쓴다. */
export class DebateError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = "DebateError";
  }
}
