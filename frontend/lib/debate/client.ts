/**
 * 토론방 화면이 데이터를 가져오는 유일한 창구.
 *
 * NEXT_PUBLIC_DEBATE_DATA_SOURCE=api 이면 백엔드 API(api.ts), 아니면 가짜 데이터(mock-store.ts)를 쓴다.
 * 두 쪽은 함수 이름과 돌려주는 모양이 같아서 화면 코드는 고치지 않아도 된다.
 * 실시간 채팅·진영 고르기는 아직 API가 없어(3단계 SSE) api 모드에서는 토론방에 안내만 보여준다.
 */
import * as api from "./api";
import * as mock from "./mock-store";

export const DEBATE_DATA_SOURCE: "mock" | "api" = process.env.NEXT_PUBLIC_DEBATE_DATA_SOURCE === "api" ? "api" : "mock";
/** 실시간 채팅·진영 고르기를 쓸 수 있는지. 3단계에서 API가 생기면 api 모드에서도 켠다. */
export const DEBATE_CHAT_READY = DEBATE_DATA_SOURCE === "mock";

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

// 아래는 아직 가짜 데이터에만 있다 (DEBATE_CHAT_READY일 때만 화면이 부른다).
export const chooseSide = mock.chooseSide;
export const listMessages = mock.listMessages;
export const sendMessage = mock.sendMessage;
export const subscribeDebate = mock.subscribeDebate;
export const resetMockDebates = mock.resetMockDebates;
