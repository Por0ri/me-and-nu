/**
 * 토론방 화면이 데이터를 가져오는 유일한 창구.
 * 지금은 가짜 데이터(mock-store.ts)를 쓴다. 백엔드 API가 생기면 이 파일만 바꿔서
 * 같은 이름의 함수가 서버를 부르게 한다. 화면 코드는 고치지 않아도 된다.
 */
export const DEBATE_DATA_SOURCE = "mock" as const;

export {
  cancelVote,
  chooseSide,
  createDebate,
  deleteDebate,
  getDebate,
  getVoteTickets,
  listDebates,
  listMessages,
  listMyDebates,
  listMyVotes,
  resetMockDebates,
  searchDebates,
  searchWorks,
  sendMessage,
  similarDebates,
  subscribeDebate,
  voteDebate,
} from "./mock-store";
