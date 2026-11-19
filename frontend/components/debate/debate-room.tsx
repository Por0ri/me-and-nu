"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  DEBATE_CHAT_READY, cancelVote, chooseSide, getDebate, getVoteTickets, listMessages, sendMessage, subscribeDebate, voteDebate,
} from "@/lib/debate/client";
import { dateText, leadingText, resultText, roomRemaining, timeText, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateMessage, DebateSideKey, DebateSummary, VoteTickets } from "@/lib/debate/types";
import { DebateHeader, DebateScreen, DebateState, errorText, useToast } from "./debate-chrome";
import { ChatIcon, CheckIcon, ClockIcon, SendIcon, TrophyIcon, UserIcon, UsersIcon } from "./debate-icons";
import { Badge, InfoNote, VoteProgress, WorkChips, useNow } from "./debate-parts";
import styles from "./debate.module.css";

/** 토론 하나의 화면. 상태에 따라 투표·실시간 채팅·결과·소각 안내를 보여준다. */
export function DebateRoom({ debateId }: { debateId: number }) {
  const [debate, setDebate] = useState<DebateSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, showToast] = useToast();
  const load = useCallback(() => {
    setError(null);
    getDebate(debateId).then(setDebate, (reason: unknown) => setError(errorText(reason)));
  }, [debateId]);
  useEffect(() => { load(); }, [load]);

  if (!debate) {
    return <DebateScreen label="토론방">
      <DebateHeader title="토론방" back="/pivot/debate" />
      <div className={styles.stack}><DebateState loading={!error} error={error} onRetry={load} /></div>
    </DebateScreen>;
  }
  if (debate.status === "open") {
    return DEBATE_CHAT_READY ? <OpenRoom debate={debate} onChange={setDebate} showToast={showToast} toast={toast} /> : <PendingRoom debate={debate} />;
  }
  return <DebateScreen label={debate.title}>
    <DebateHeader title={debate.status === "voting" ? "주제 후보" : "지난 토론방"} back={debate.status === "voting" ? "/pivot/debate?tab=vote" : "/pivot/debate?tab=past"} />
    <div className={styles.stack}>
      {debate.status === "voting" ? <VotingDetail debate={debate} onChange={setDebate} showToast={showToast} />
        : debate.status === "closed" ? <ClosedResult debate={debate} />
        : <ExpiredDetail debate={debate} />}
    </div>
    {toast}
  </DebateScreen>;
}

/* ───────────── 진행 중: 실시간 채팅 ───────────── */

function OpenRoom({ debate, onChange, showToast, toast }: {
  debate: DebateSummary; onChange: (debate: DebateSummary) => void; showToast: (message: string) => void; toast: React.ReactNode;
}) {
  const [messages, setMessages] = useState<DebateMessage[] | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const debateRef = useRef(debate);
  debateRef.current = debate;
  const now = useNow();

  // 같은 메시지가 보낸 응답과 실시간 스트림으로 두 번 와도 번호로 하나만 남기고 번호순으로 둔다.
  const merge = useCallback((incoming: DebateMessage[]) => {
    setMessages((current) => {
      const byId = new Map((current ?? []).map((item) => [item.messageId, item]));
      for (const item of incoming) byId.set(item.messageId, item);
      return [...byId.values()].sort((left, right) => left.messageId - right.messageId);
    });
  }, []);

  useEffect(() => {
    let alive = true;
    listMessages(debate.debateId).then((rows) => { if (alive) merge(rows); }, () => { if (alive) setMessages([]); });
    const unsubscribe = subscribeDebate(debate.debateId, (event) => {
      if (event.type === "chat") merge([event.message]);
      else if (event.type === "sides") onChange({ ...debateRef.current, sideA: event.sideA, sideB: event.sideB, participantCount: event.participantCount });
      else {
        onChange(event.debate);
        showToast("토론방이 끝났어요. 결과만 남고 채팅은 사라져요.");
      }
    });
    return () => { alive = false; unsubscribe(); };
  }, [debate.debateId, merge, onChange, showToast]);

  // 맨 아래를 보고 있을 때만 새 메시지로 따라 내려간다.
  useEffect(() => {
    const onScroll = () => {
      followRef.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (followRef.current) endRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const pickSide = async (side: DebateSideKey) => {
    if (debate.mySide === side || choosing) return;
    setChoosing(true);
    try {
      const next = await chooseSide(debate.debateId, side);
      onChange(next);
      showToast(`${side === "a" ? next.sideA.label : next.sideB.label} 편이 됐어요.`);
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setChoosing(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      const sent = await sendMessage(debate.debateId, text);
      followRef.current = true;
      merge([sent]);
      setText("");
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setSending(false);
    }
  };

  const label = (side: DebateSideKey) => (side === "a" ? debate.sideA.label : debate.sideB.label);
  const lead = leadingText(debate);
  const percentA = debate.sideA.percent ?? 50;
  return <DebateScreen composer label={debate.title}>
    <DebateHeader title="토론방" back="/pivot/debate" extra={<span className={styles.liveDot}>실시간</span>} />
    <div className={styles.stack}>
      <section className={styles.roomCard}>
        <Badge>실시간 토론방</Badge>
        <h2>{debate.title}</h2>
        {debate.description ? <p className={styles.description}>{debate.description}</p> : null}
        <WorkChips works={debate.works} />
        <div className={styles.roomChips}>
          <span className={styles.roomChip}><ClockIcon size={14} />남은 시간 {roomRemaining(debate.closesAt, now)}</span>
          <span className={styles.roomChip}><UsersIcon size={14} />진영 투표 {debate.participantCount}명</span>
          <span className={styles.roomChip}><ChatIcon size={14} />채팅 {Math.max(debate.messageCount, messages?.length ?? 0)}</span>
        </div>
      </section>

      <section className={styles.ratioCard} aria-label="진영 비율">
        <p className={styles.ratioHead}>
          {lead ? <strong>{lead}</strong> : <strong>아직 아무도 진영을 고르지 않았어요</strong>}
          {debate.sideA.percent !== null ? <span>{debate.sideA.percent}% / {debate.sideB.percent}%</span> : null}
        </p>
        <div className={styles.ratioBar} aria-hidden="true">
          <span className={styles.ratioA} style={{ width: `${percentA}%` }} />
          <span className={styles.ratioB} style={{ width: `${100 - percentA}%` }} />
        </div>
        <p className={styles.ratioLegend}><span>{debate.sideA.label} {debate.sideA.count}명</span><span>{debate.sideB.label} {debate.sideB.count}명</span></p>
        {/* 시안에 없는 진영 고르기 버튼. 채팅과 별개로 언제든 고르고 바꾼다 (설계 9장) */}
        <div className={styles.sideButtons} role="group" aria-label="진영 고르기">
          {(["a", "b"] as const).map((side) => <button key={side} type="button" className={styles.sideButton} data-side={side}
            aria-pressed={debate.mySide === side} disabled={choosing} onClick={() => void pickSide(side)}>
            {debate.mySide === side ? <CheckIcon size={15} style={{ verticalAlign: "-2px", marginRight: 4 }} /> : null}{label(side)} 편
          </button>)}
        </div>
        <p className={styles.sideHelp}>진영은 채팅과 별개로 언제든 고르고 바꿀 수 있어요. 방이 끝나는 순간 인원이 많은 쪽이 이겨요.</p>
      </section>

      <InfoNote>방이 끝나면 채팅은 사라지고 결과만 남아요.</InfoNote>

      {messages === null ? <DebateState loading /> : null}
      {messages?.length === 0 ? <p className={styles.state}>아직 채팅이 없어요. 첫 마디를 남겨보세요.</p> : null}
      <ol className={styles.messages} aria-label="채팅" aria-live="polite">
        {/* 내 메시지는 오른쪽, 다른 사람은 왼쪽. 보낸 순간의 진영은 닉네임 옆 표시로만 보여준다. */}
        {(messages ?? []).map((item) => <li key={item.messageId} className={styles.message} data-mine={item.isMine || undefined}>
          <span className={styles.avatar} aria-hidden="true"><UserIcon size={18} /></span>
          <div className={styles.messageBody}>
            <p className={styles.messageMeta}>
              <b>{item.isMine ? "나" : item.nickname}</b>
              {item.side ? <span className={styles.sideTag} data-side={item.side}>{label(item.side)} 편</span> : null}
              <time dateTime={item.createdAt}>{timeText(item.createdAt)}</time>
            </p>
            <p className={styles.bubble}>{item.body}</p>
          </div>
        </li>)}
      </ol>
      <div ref={endRef} />
    </div>

    <form className={styles.composer} onSubmit={(event) => void submit(event)}>
      <input value={text} onChange={(event) => setText(event.target.value)} maxLength={DEBATE_RULES.messageMax}
        placeholder="메시지를 입력하세요." aria-label="메시지" />
      <button type="submit" className={styles.sendButton} aria-label="보내기" disabled={!text.trim() || sending}><SendIcon size={20} /></button>
    </form>
    {toast}
  </DebateScreen>;
}

/* ───────────── 진행 중 (API 모드, 채팅 연결 전) ───────────── */

/** 3단계(실시간 채팅·진영 API) 전까지 진짜 토론방은 열렸다는 것까지만 보여준다. */
function PendingRoom({ debate }: { debate: DebateSummary }) {
  const now = useNow();
  const percentA = debate.sideA.percent ?? 50;
  return <DebateScreen label={debate.title}>
    <DebateHeader title="토론방" back="/pivot/debate" />
    <div className={styles.stack}>
      <section className={styles.roomCard}>
        <Badge>실시간 토론방</Badge>
        <h2>{debate.title}</h2>
        {debate.description ? <p className={styles.description}>{debate.description}</p> : null}
        <WorkChips works={debate.works} />
        <div className={styles.roomChips}>
          <span className={styles.roomChip}><ClockIcon size={14} />남은 시간 {roomRemaining(debate.closesAt, now)}</span>
          <span className={styles.roomChip}><UsersIcon size={14} />진영 투표 {debate.participantCount}명</span>
        </div>
      </section>
      <section className={styles.ratioCard} aria-label="진영 비율">
        <p className={styles.ratioHead}><strong>{leadingText(debate) ?? "아직 아무도 진영을 고르지 않았어요"}</strong></p>
        <div className={styles.ratioBar} aria-hidden="true">
          <span className={styles.ratioA} style={{ width: `${percentA}%` }} />
          <span className={styles.ratioB} style={{ width: `${100 - percentA}%` }} />
        </div>
        <p className={styles.ratioLegend}><span>{debate.sideA.label} {debate.sideA.count}명</span><span>{debate.sideB.label} {debate.sideB.count}명</span></p>
      </section>
      <InfoNote><strong>실시간 채팅은 다음 단계에서 연결돼요</strong>지금은 10표를 모아 토론방이 열리는 것까지 진짜 서버로 동작해요. 채팅과 진영 고르기는 3단계(SSE)에서 붙여요.</InfoNote>
    </div>
  </DebateScreen>;
}

/* ───────────── 투표 중 ───────────── */

function VotingDetail({ debate, onChange, showToast }: { debate: DebateSummary; onChange: (debate: DebateSummary) => void; showToast: (message: string) => void }) {
  const [tickets, setTickets] = useState<VoteTickets | null>(null);
  const [pending, setPending] = useState(false);
  const now = useNow();
  useEffect(() => { getVoteTickets().then(setTickets, () => setTickets(null)); }, []);
  const toggle = async () => {
    setPending(true);
    try {
      if (debate.myVote) {
        const result = await cancelVote(debate.debateId);
        setTickets(result.tickets);
        onChange(result.debate);
        showToast("투표를 취소했어요. 투표권 1개를 돌려받았어요.");
      } else {
        const result = await voteDebate(debate.debateId);
        setTickets(result.tickets);
        onChange(result.debate);
        showToast(result.opened ? "10표 달성! 토론방이 열렸어요." : "투표했어요.");
      }
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setPending(false);
    }
  };
  return <>
    <section className={styles.roomCard}>
      <Badge>주제 후보</Badge>
      <h2>{debate.title}</h2>
      {debate.description ? <p className={styles.description}>{debate.description}</p> : null}
      <WorkChips works={debate.works} />
      <p className={styles.description}>{debate.sideA.label} vs {debate.sideB.label}</p>
      <VoteProgress count={debate.voteCount} threshold={debate.voteThreshold} large />
      <div className={styles.roomChips}><span className={styles.roomChip}><ClockIcon size={14} />{votingRemaining(debate.votingEndsAt, now)}</span></div>
    </section>
    <button type="button" className={styles.primaryButton} disabled={pending} onClick={() => void toggle()}>
      {debate.myVote ? "투표 취소" : "투표하기"}
    </button>
    {tickets ? <p className={styles.centerNote}>내 투표권 {tickets.balance}/{tickets.max}</p> : null}
    <InfoNote>{debate.voteThreshold}표가 모이는 순간 토론방이 열려요. 마감까지 못 모으면 자동 소각돼요.</InfoNote>
  </>;
}

/* ───────────── 종료 · 소각 ───────────── */

function ClosedResult({ debate }: { debate: DebateSummary }) {
  const percentA = debate.sideA.percent ?? 50;
  const winner = debate.result === "a" ? debate.sideA.label : debate.result === "b" ? debate.sideB.label : null;
  return <>
    <section className={styles.roomCard}>
      <Badge tone="soft">방 종료</Badge>
      <h2>{debate.title}</h2>
      <WorkChips works={debate.works} />
      <div className={styles.roomChips}>
        <span className={styles.roomChip}><ClockIcon size={14} />{dateText(debate.closesAt)} 종료</span>
        <span className={styles.roomChip}><UsersIcon size={14} />진영 투표 {debate.participantCount}명</span>
        <span className={styles.roomChip}><ChatIcon size={14} />채팅 {debate.messageCount}개</span>
      </div>
    </section>
    <section className={styles.resultCard} aria-label="토론 결과">
      <TrophyIcon size={30} />
      <strong>{winner ? `${winner} 승리` : debate.result === "draw" ? "무승부" : "참여자 없음"}</strong>
      {debate.sideA.percent !== null ? <p className={styles.description}>{debate.sideA.label} {debate.sideA.percent}% vs {debate.sideB.label} {debate.sideB.percent}%</p> : null}
      <div className={styles.ratioBar} style={{ width: "100%" }} aria-hidden="true">
        <span className={styles.ratioA} style={{ width: `${percentA}%` }} />
        <span className={styles.ratioB} style={{ width: `${100 - percentA}%` }} />
      </div>
      <p className={styles.ratioLegend} style={{ width: "100%" }}>
        <span>{debate.sideA.label} {debate.sideA.count}명</span><span>{debate.sideB.label} {debate.sideB.count}명</span>
      </p>
    </section>
    <InfoNote>채팅은 방이 끝날 때 사라져서 볼 수 없어요.</InfoNote>
  </>;
}

function ExpiredDetail({ debate }: { debate: DebateSummary }) {
  return <>
    <section className={styles.roomCard}>
      <Badge tone="gray">자동 소각</Badge>
      <h2>{debate.title}</h2>
      <WorkChips works={debate.works} />
      <VoteProgress count={debate.voteCount} threshold={debate.voteThreshold} large />
      <div className={styles.roomChips}><span className={styles.roomChip}><ClockIcon size={14} />{dateText(debate.votingEndsAt)} 마감</span></div>
    </section>
    <InfoNote>{DEBATE_RULES.votingDays}일 동안 {debate.voteThreshold}표를 못 모아서 소각됐어요. 소각된 주제는 다시 투표할 수 없어요.</InfoNote>
  </>;
}
