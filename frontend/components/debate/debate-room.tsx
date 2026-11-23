"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  cancelVote, chooseSide, getDebate, getVoteTickets, listMessages, sendMessage, subscribeDebate, voteDebate,
} from "@/lib/debate/client";
import { dateText, leadingText, roomRemaining, timeText, votingRemaining } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateConnection, DebateMessage, DebateSideKey, DebateSummary, VoteTickets } from "@/lib/debate/types";
import { DebateHeader, DebateScreen, DebateState, errorText, useToast } from "./debate-chrome";
import { ChatIcon, CheckIcon, ClockIcon, SendIcon, TrophyIcon, UserIcon, UsersIcon } from "./debate-icons";
import { Badge, InfoNote, VoteProgress, WorkChips, useNow } from "./debate-parts";
import styles from "./debate.module.css";

type Update = (change: (current: DebateSummary) => DebateSummary) => void;

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
  // 실시간 이벤트가 연달아 와도 서로 덮어쓰지 않게 지금 값을 기준으로 바꾼다.
  const update = useCallback<Update>((change) => setDebate((current) => (current ? change(current) : current)), []);

  if (!debate) {
    return <DebateScreen label="토론방">
      <DebateHeader title="토론방" back="/pivot/debate" />
      <div className={styles.stack}><DebateState loading={!error} error={error} onRetry={load} /></div>
    </DebateScreen>;
  }
  if (debate.status === "open") {
    return <OpenRoom debate={debate} onChange={setDebate} update={update} showToast={showToast} toast={toast} />;
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

const CONNECTION_LABEL: Record<DebateConnection, string> = { connecting: "연결 중", live: "실시간", reconnecting: "다시 연결 중", stopped: "연결 끊김" };

function OpenRoom({ debate, onChange, update, showToast, toast }: {
  debate: DebateSummary; onChange: (debate: DebateSummary) => void; update: Update; showToast: (message: string) => void; toast: React.ReactNode;
}) {
  const debateId = debate.debateId;
  const [messages, setMessages] = useState<DebateMessage[] | null>(null);
  const [olderCursor, setOlderCursor] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [connection, setConnection] = useState<DebateConnection>("connecting");
  const [streamKey, setStreamKey] = useState(0);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  // 화면이 가진 가장 최근 채팅 번호. 실시간 연결을 (다시) 열 때 이 뒤의 채팅부터 받는다.
  const lastIdRef = useRef(0);
  const now = useNow();

  const remember = useCallback((items: DebateMessage[]) => {
    for (const item of items) lastIdRef.current = Math.max(lastIdRef.current, item.messageId);
  }, []);

  // 같은 메시지가 보낸 응답과 실시간 스트림으로 두 번 와도 번호로 하나만 남기고 번호순으로 둔다.
  const merge = useCallback((incoming: DebateMessage[]) => {
    remember(incoming);
    setMessages((current) => {
      const byId = new Map((current ?? []).map((item) => [item.messageId, item]));
      for (const item of incoming) byId.set(item.messageId, item);
      return [...byId.values()].sort((left, right) => left.messageId - right.messageId);
    });
  }, [remember]);

  /** 최근 채팅으로 목록을 새로 채운다 (처음 들어올 때, 놓친 채팅이 너무 많을 때). 그사이 실시간으로 온 것은 남긴다. */
  const reload = useCallback(async () => {
    const page = await listMessages(debateId);
    remember(page.items);
    const newest = page.items.at(-1)?.messageId ?? 0;
    setMessages((current) => [...page.items, ...(current ?? []).filter((item) => item.messageId > newest)]);
    setOlderCursor(page.nextCursor);
  }, [debateId, remember]);

  // 1) 최근 채팅을 불러온 뒤 2) 그 마지막 번호부터 실시간 연결을 연다. 그사이 올라온 채팅은 서버가 먼저 보내 준다.
  useEffect(() => {
    let alive = true;
    reload().catch(() => {
      if (!alive) return;
      setMessages((current) => current ?? []);
      showToast("채팅을 불러오지 못했어요.");
    }).finally(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, [reload, showToast]);

  useEffect(() => {
    if (!loaded) return;
    let alive = true;
    // 연결이 끝났을 때 이유를 토론 상태로 확인한다: 끝난 방이면 결과 화면, 로그인이 끝났으면(401)
    // 공통 처리가 로그인 화면으로 보낸다. 그 밖(동시 연결 수 초과 등)은 다시 연결 버튼을 보여준다.
    const recheck = (reason: "closed" | "session-expired" | "stopped") => {
      getDebate(debateId).then((next) => {
        if (!alive) return;
        onChange(next);
        if (next.status !== "open") showToast("토론방이 끝났어요. 결과만 남고 채팅은 사라져요.");
        else if (reason === "session-expired") setStreamKey((key) => key + 1); // 그새 다시 로그인했다: 새 세션으로 연결
      }, () => undefined);
    };
    const unsubscribe = subscribeDebate(debateId, (event) => {
      if (!alive) return;
      if (event.type === "chat") {
        merge([event.message]);
        const count = event.messageCount;
        if (count !== undefined) update((current) => ({ ...current, messageCount: Math.max(current.messageCount, count) }));
      } else if (event.type === "sides") {
        update((current) => ({ ...current, sideA: event.sideA, sideB: event.sideB, participantCount: event.participantCount }));
      } else if (event.type === "resync") {
        reload().catch(() => showToast("채팅을 다시 불러오지 못했어요."));
      } else {
        setConnection("stopped");
        recheck(event.type);
      }
    }, {
      after: lastIdRef.current,
      onConnection: (state) => {
        if (!alive) return;
        setConnection(state);
        if (state === "stopped") recheck("stopped");
      },
    });
    // 다른 주소로 떠날 때 브라우저가 페이지를 얼려 두면(뒤로 가기 저장소) 연결이 열린 채 남아 연결 수 한도를 차지한다.
    // 떠날 때 바로 닫고, 뒤로 가기로 돌아오면 다시 연다 (놓친 채팅은 마지막 번호 뒤부터 다시 받는다).
    const leave = () => {
      alive = false;
      unsubscribe();
    };
    const back = (event: PageTransitionEvent) => { if (event.persisted) setStreamKey((key) => key + 1); };
    window.addEventListener("pagehide", leave);
    window.addEventListener("pageshow", back);
    return () => {
      leave();
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("pageshow", back);
    };
  }, [loaded, streamKey, debateId, merge, reload, update, onChange, showToast]);

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

  const reconnect = () => {
    setConnection("connecting");
    setStreamKey((key) => key + 1);
  };

  const loadOlder = async () => {
    if (!olderCursor || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await listMessages(debateId, olderCursor);
      merge(page.items);
      setOlderCursor(page.nextCursor);
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setLoadingOlder(false);
    }
  };

  const pickSide = async (side: DebateSideKey) => {
    if (debate.mySide === side || choosing) return;
    setChoosing(true);
    try {
      const next = await chooseSide(debateId, side);
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
      const sent = await sendMessage(debateId, text);
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
    <DebateHeader title="토론방" back="/pivot/debate" extra={<span className={styles.liveDot} data-state={connection}>{CONNECTION_LABEL[connection]}</span>} />
    <div className={styles.stack}>
      {connection === "reconnecting" ? <p className={styles.connectionNote} role="status">연결이 잠깐 끊겼어요. 다시 연결하는 중이에요.</p> : null}
      {connection === "stopped" ? <p className={styles.connectionNote} data-tone="error" role="status">
        실시간 연결이 끊겼어요. 이 토론방을 다른 창에서도 열어 두었다면 닫고 다시 연결해 주세요.
        <button type="button" onClick={reconnect}>다시 연결</button>
      </p> : null}
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
      {olderCursor ? <button type="button" className={styles.olderButton} disabled={loadingOlder} onClick={() => void loadOlder()}>
        {loadingOlder ? "불러오는 중이에요" : "이전 채팅 더 보기"}
      </button> : null}
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
    <InfoNote>채팅은 방이 끝날 때 사라져서 볼 수 없어요. 결과는 끝난 뒤 {DEBATE_RULES.resultRetentionDays}일 동안 지난 토론방에 남아요.</InfoNote>
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
    <InfoNote>{DEBATE_RULES.votingDays}일 동안 {debate.voteThreshold}표를 못 모아서 소각됐어요. 소각된 주제는 다시 투표할 수 없고, 기록은 {DEBATE_RULES.resultRetentionDays}일 동안 지난 토론방에 남아요.</InfoNote>
  </>;
}
