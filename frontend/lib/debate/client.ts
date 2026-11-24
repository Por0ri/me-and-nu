/**
 * 토론방 화면이 데이터를 가져오는 유일한 창구.
 *
 * 기본은 백엔드 API(api.ts)다. 가짜 데이터(mock-store.ts)는 개발 서버(next dev)에서
 * NEXT_PUBLIC_DEBATE_DATA_SOURCE=mock 으로 직접 켰을 때만 쓴다. 배포용 빌드(next build)는 설정과 상관없이
 * 항상 진짜 API 를 써서, 회원이 가짜 토론방을 보는 일이 없게 한다 (2026-11-23 사용자 결정: 베타는 빈 진짜 토론방으로 시작).
 * 두 쪽은 함수 이름과 돌려주는 모양이 같아서 화면 코드는 고치지 않아도 된다.
 * 실시간 채팅은 api 모드에서 SSE(GET /debates/{id}/stream), 가짜 모드에서는 몇 초마다 흉내 낸 이벤트다.
 */
import * as api from "./api";
import * as mock from "./mock-store";

export const DEBATE_DATA_SOURCE: "mock" | "api" =
  process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_DEBATE_DATA_SOURCE === "mock" ? "mock" : "api";

const source = DEBATE_DATA_SOURCE === "api" ? api : mock;

export const listDebates = source.listDebates;
export const getDebate = source.getDebate;
export const getVoteTickets = source.getVoteTickets;
export const voteDebate = source.voteDebate;
export const cancelVote = source.cancelVote;
export const listMyDebates = source.listMyDebates;
export const listMyVotes = source.listMyVotes;
export const createDebate = source.createDebate;
export const deleteDebate = source.deleteDebate;
export const searchWorks = source.searchWorks;
export const searchDebates = source.searchDebates;
export const similarDebates = source.similarDebates;
export const joinDebate = source.joinDebate;
export const leaveDebate = source.leaveDebate;
export const chooseSide = source.chooseSide;
export const cancelSide = source.cancelSide;
export const listMessages = source.listMessages;
export const sendMessage = source.sendMessage;
export const subscribeDebate = source.subscribeDebate;

// 가짜 데이터에만 있다.
export const resetMockDebates = mock.resetMockDebates;
