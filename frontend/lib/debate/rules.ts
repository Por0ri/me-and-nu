/**
 * 토론방 규칙 값. 백엔드 설정(DEBATE_*)의 기본값과 같다.
 * 글자 수 상한은 DB 칼럼 크기와 같다 (backend/app/models/debate.py).
 */
export const DEBATE_RULES = {
  voteThreshold: 10,
  votingDays: 7,
  roomHours: 48,
  ticketMax: 3,
  ticketRefillMinutes: 60,
  activePerUser: 1,
  messageRateCount: 5,
  messageRateWindowSeconds: 10,
  titleMax: 60,
  descriptionMax: 200,
  sideLabelMax: 20,
  messageMax: 300,
  worksMax: 3,
} as const;
