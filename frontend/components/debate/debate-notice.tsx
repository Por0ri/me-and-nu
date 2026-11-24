"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { getDebate } from "@/lib/debate/client";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary } from "@/lib/debate/types";
import { LiveCard, PastCard, useRoomActions } from "./debate-cards";
import { DebateState, errorText, useToast } from "./debate-chrome";
import { useNow } from "./debate-parts";
import styles from "./debate.module.css";

/**
 * 알림함의 토론방 개설 알림(제목은 토론 주제) 상세 아래에 그 토론방 카드를 바로 보여 준다 (2026-11-24 사용자 결정, 예전 '토론방 보러 가기' 버튼 대신).
 * - 열린 방: 실시간 토론방 탭과 같은 카드. '토론 참여하기'를 누르면 참여하고 바로 채팅방으로 간다.
 *   이미 참여했으면 '방 나가기'·'다시 입장하기'.
 * - 끝난 방: 지난 토론방 카드('투표 결과 확인하기'는 결과 창).
 * - 끝난 지 보관 기간(9일)이 지나 지워진 방: 안내 문구만.
 * 알림함(pivot 화면)은 이 부품을 필요할 때만 불러온다 (notification-inbox.tsx 의 next/dynamic).
 */
export function DebateNoticeCard({ debateId }: { debateId: number }) {
  const [debate, setDebate] = useState<DebateSummary | null>(null);
  const [failure, setFailure] = useState<{ gone: boolean; message: string } | null>(null);
  const [toast, showToast] = useToast();
  const room = useRoomActions(setDebate, showToast);
  const now = useNow();
  const load = useCallback(() => {
    setFailure(null);
    getDebate(debateId).then(setDebate, (reason: unknown) => setFailure({
      gone: (reason as { code?: unknown } | null)?.code === "DEBATE_NOT_FOUND",
      message: errorText(reason),
    }));
  }, [debateId]);
  useEffect(() => { load(); }, [load]);

  let body: ReactNode;
  if (debate?.status === "open") {
    body = <LiveCard debate={debate} now={now} busy={room.pending === debate.debateId} onEnter={room.enter} onLeave={room.askLeave} />;
  } else if (debate?.status === "closed") {
    body = <PastCard debate={debate} />;
  } else if (debate) {
    body = null; // 열린 적이 있는 방만 개설 알림이 오므로 투표 중인 주제는 오지 않는다.
  } else if (failure?.gone) {
    body = <p className={styles.noticeGone}>끝난 지 {DEBATE_RULES.resultRetentionDays}일이 지나서 더 볼 수 없는 토론방이에요.</p>;
  } else {
    body = <DebateState loading={!failure} error={failure?.message ?? null} onRetry={load} />;
  }
  // 토론 화면 밖이라 카드의 색·버튼 규칙(.screen 아래 규칙)을 쓰려고 .screen 을 붙이고, 화면 틀(폭·높이·여백·바탕)만 끈다.
  return <div className={`${styles.screen} ${styles.embedded}`}>
    {body}
    {room.dialog}
    {toast}
  </div>;
}
