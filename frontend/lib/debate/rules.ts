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
  /** 카드에 더보기 없이 전부 보여서 100자까지 (2026-11-25 결정) */
  descriptionMax: 100,
  /** 선택지 이름 8자까지 (2026-11-25 디자인) */
  sideLabelMax: 8,
  messageMax: 300,
  /** 관련 작품은 1~2개 필수 (2026-11-25 디자인) */
  worksMax: 2,
} as const;
