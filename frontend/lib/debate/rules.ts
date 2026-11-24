/**
 * 토론방 규칙 값. 백엔드 설정(DEBATE_*)의 기본값과 같다.
 * 글자 수 상한은 DB 칼럼 크기와 같다 (backend/app/models/debate.py). 선택지 이름만 칼럼(20자)보다 짧게 받는다 (SIDE_LABEL_INPUT_MAX).
 */
export const DEBATE_RULES = {
  voteThreshold: 10,
  votingDays: 7,
  roomHours: 48,
  ticketMax: 3,
  ticketRefillMinutes: 60,
  /** 투표 중이거나 토론 중(열린 48시간)인 내 주제는 1개까지. 토론 중에도 새 주제를 못 올린다 (2026-11-24 결정, 남용 방지) */
  activePerUser: 1,
  messageRateCount: 5,
  messageRateWindowSeconds: 10,
  /** 끝난 토론의 결과를 지난 토론방에 보여 주는 날 수 (DEBATE_RESULT_RETENTION_DAYS) */
  resultRetentionDays: 9,
  titleMax: 60,
  descriptionMax: 200,
  /** 진영 버튼 한 줄에 6자쯤 들어가서, 12자면 대부분 두 줄 안에 든다 (2026-11-24 결정) */
  sideLabelMax: 12,
  messageMax: 300,
  worksMax: 3,
} as const;
