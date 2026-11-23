/**
 * 토론방 화면이 쓰는 데이터 모양.
 * 백엔드 설계(docs/DEBATE_BACKEND_DESIGN_2026-11-19.md 7장)의 API 응답과 같은 모양으로 둔다.
 * 지금은 가짜 데이터(mock-store.ts)가 채우고, API가 생기면 같은 모양으로 서버가 채운다.
 */

/** voting 투표 중 → open 진행 중 → closed 종료. 7일 안에 기준 표를 못 받은 주제(expired)는 바로 삭제돼 화면에 오지 않는다. */
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
  /** 토론방에 참여 중인 사람 수 (방을 나가면 빠진다) */
  memberCount: number;
  /** 내가 이 토론방에 참여 중인지. 참여한 사람만 채팅·진영 고르기를 하고, 참여한 뒤의 채팅만 보인다. */
  joined: boolean;
  /** 내가 이 토론방에 참여한 시각. 참여 중이 아니면 null ('참여 중인 방'을 최근에 참여한 순서로 보여 줄 때 쓴다) */
  joinedAt: string | null;
  messageCount: number;
  result: DebateResult | null;
  myVote: boolean;
  mySide: DebateSideKey | null;
  isMine: boolean;
  /** 주제를 올린 사람. 탈퇴했거나 지워진 회원이면 "알 수 없음" */
  authorNickname: string;
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
  | { status: "ended"; entityId?: number; q?: string };

export type CreateDebateInput = {
  title: string;
  description: string;
  sideA: string;
  sideB: string;
  workIds: number[];
};

/** 채팅 한 쪽. nextCursor로 더 이전 채팅을 받는다. */
export type DebateMessagePage = { items: DebateMessage[]; nextCursor: string | null };

/** 실시간 스트림 이벤트 (설계 3.2). closed·session-expired를 받으면 연결이 끝난다. */
export type DebateStreamEvent =
  /** 새 채팅. messageCount는 이 채팅까지 센 방의 채팅 수 (놓친 채팅을 다시 보낼 때는 없음) */
  | { type: "chat"; message: DebateMessage; messageCount?: number }
  | { type: "sides"; sideA: DebateSideSummary; sideB: DebateSideSummary; participantCount: number }
  /** 방이 끝났다. 화면이 토론을 다시 불러와 결과를 보여준다. */
  | { type: "closed" }
  /** 연결이 끊긴 동안 놓친 채팅이 너무 많다. 채팅을 새로 불러온다. */
  | { type: "resync" }
  /** 로그인 세션이 끝났다. */
  | { type: "session-expired" };

/** 실시간 연결 상태. stopped는 서버가 연결을 받아 주지 않아(끝난 방·로그인 만료·연결 수 초과) 브라우저가 다시 붙기를 멈춘 상태 */
export type DebateConnection = "connecting" | "live" | "reconnecting" | "stopped";

export type DebateSubscribeOptions = {
  /** 화면이 이미 가진 마지막 채팅 번호. 그 뒤의 채팅을 먼저 받는다. */
  after?: number;
  onConnection?: (state: DebateConnection) => void;
};

/** 서버 오류 코드(설계 7장)와 같은 이름을 쓴다. */
export class DebateError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = "DebateError";
  }
}
