"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  cancelSide, chooseSide, getDebate, listMessages, sendMessage, subscribeDebate,
} from "@/lib/debate/client";
import { dateText, roomRemaining, timeText } from "@/lib/debate/format";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateConnection, DebateMessage, DebateSideKey, DebateSummary } from "@/lib/debate/types";
import { SideResult } from "./debate-cards";
import { DebateHomeHeader, DebateScreen, DebateState, errorText, useToast, type ShowToast } from "./debate-chrome";
import { ChatIcon, CircleCheckIcon, ClockIcon, SendIcon, TrophyIcon, UsersIcon } from "./debate-icons";
import { AuthorLine, Badge, DebateGuide, InfoNote, WorkChips, useNow } from "./debate-parts";
import styles from "./debate.module.css";

type Update = (change: (current: DebateSummary) => DebateSummary) => void;

/** 토론방·주제 화면의 머리 부분: 내 활동처럼 둥근 뒤로가기 + 로고, 오른쪽에 이용 안내(ⓘ)와 알림 */
function RoomHeader({ title, back }: { title: string; back: string }) {
  const router = useRouter();
  const [guideOpen, setGuideOpen] = useState(false);
  const closeGuide = useCallback(() => setGuideOpen(false), []);
  return <>
    <DebateHomeHeader title={title} onGuide={() => setGuideOpen(true)} back={() => router.push(back)} />
    {guideOpen ? <DebateGuide onClose={closeGuide} /> : null}
  </>;
}

/** 토론 하나의 화면. 상태에 따라 실시간 채팅·결과를 보여준다.
 *  투표 중인 주제는 따로 화면이 없다 (2026-11-23 사용자 결정: 카드에서 바로 투표하고 선택지도 카드에 보인다).
 *  예전 주소로 들어오면 주제 투표 탭으로 보낸다.
 *  참여 화면도 없앴다 (2026-11-24 사용자 결정): 참여는 카드의 '토론 참여하기'로 하고(알림 상세에도 카드가 있다),
 *  참여하지 않은 채 열린 방 주소로 바로 들어오면 실시간 토론방 탭으로 보낸다. */
export function DebateRoom({ debateId }: { debateId: number }) {
  const router = useRouter();
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
  const voting = debate?.status === "voting";
  const outside = debate?.status === "open" && !debate.joined;
  useEffect(() => {
    if (voting) router.replace("/pivot/debate?tab=vote");
    else if (outside) router.replace("/pivot/debate");
  }, [voting, outside, router]);

  if (!debate || voting || outside) {
    return <DebateScreen label="토론방">
      <RoomHeader title="토론방" back="/pivot/debate" />
      <div className={styles.stack}><DebateState loading={!error} error={error} onRetry={load} /></div>
    </DebateScreen>;
  }
  if (debate.status === "open") {
    // 참여한 사람만 채팅방을 본다 (참여하지 않았으면 위에서 실시간 토론방 탭으로 보냈다).
    return <OpenRoom debate={debate} onChange={setDebate} update={update} showToast={showToast} toast={toast} />;
  }
  // 끝난 방: 채팅 중에 방이 끝났거나 알림으로 들어온 경우. 기준 표를 못 받은 주제(expired)는 서버가 삭제된 것으로 보고 보내지 않는다.
  return <DebateScreen label={debate.title}>
    <RoomHeader title="지난 토론방" back="/pivot/debate?tab=past" />
    <div className={styles.stack}>
      <ClosedResult debate={debate} />
    </div>
    {toast}
  </DebateScreen>;
}

/** 채팅방 위 토론 카드 (2026-11-23 시안): 작품 칩과 '투표창 접기', 제목, 설명, 작성자·참여 인원·남은 시간.
 *  펼친 동안 아래에 children(진영 투표 등)을 둔다. 머리 부분 아래에 붙어 있고 채팅은 그 아래로 흐른다.
 *  접으면 (2026-11-25 시안) 제목과 참여 인원·남은 시간 한 줄만 남기고, 그 줄 오른쪽 끝에 '투표창 펼치기'를 둔다. */
export function RoomPanel({ debate, now, collapsed, onToggle, children }: {
  debate: DebateSummary; now: number; collapsed: boolean; onToggle?: () => void; children?: ReactNode;
}) {
  const fold = onToggle ? <button type="button" className={styles.foldButton} aria-expanded={!collapsed} onClick={onToggle}>
    {collapsed ? "투표창 펼치기" : "투표창 접기"}
  </button> : null;
  return <section className={styles.roomPanel} aria-label="토론 정보" data-collapsed={collapsed || undefined}>
    <div className={styles.roomPanelCard}>
      {collapsed ? null : <div className={styles.roomTop}>
        <WorkChips works={debate.works} />
        {fold}
      </div>}
      <h2 className={styles.topicTitle}>{debate.title}</h2>
      {!collapsed && debate.description ? <p className={styles.roomDescription}>{debate.description}</p> : null}
      <div className={styles.topicMeta}>
        {collapsed ? null : <AuthorLine debate={debate} />}
        <span className={styles.meta}><UsersIcon size={16} />{debate.memberCount}명 참여 중</span>
        <span className={styles.metaDivider} aria-hidden="true" />
        <span className={styles.meta}><ClockIcon size={16} />남은 시간 {roomRemaining(debate.closesAt, now)}</span>
        {collapsed ? fold : null}
      </div>
      {!collapsed ? children : null}
    </div>
  </section>;
}

/** 진영 투표 (시안): 고르기 전에는 두 버튼, 고른 뒤에는 지금까지의 비율 막대와 '투표 취소'.
 *  진영은 채팅과 별개라 언제든 고르고 취소할 수 있다. 총 N명은 진영을 고른 사람 수. */
function SidePoll({ debate, busy, onPick, onCancel }: {
  debate: DebateSummary; busy: boolean; onPick: (side: DebateSideKey) => void; onCancel: () => void;
}) {
  const total = <span>총 {debate.participantCount.toLocaleString("ko-KR")}명 투표</span>;
  if (!debate.mySide) {
    return <div className={styles.poll}>
      <div className={styles.sideChoices} role="group" aria-label="투표하기">
        {(["a", "b"] as const).map((side) => <button key={side} type="button" className={styles.sideChoice} disabled={busy} onClick={() => onPick(side)}>
          <CircleCheckIcon size={22} /><span>{side === "a" ? debate.sideA.label : debate.sideB.label}</span>
        </button>)}
      </div>
      <p className={styles.pollFoot}>{total}</p>
    </div>;
  }
  return <div className={styles.poll}>
    {/* 내가 고른 쪽은 보라 글씨 (시안에 없는 작은 표시) */}
    <SideResult debate={debate} mine={debate.mySide} />
    <p className={styles.pollFoot}>{total}<button type="button" disabled={busy} onClick={onCancel}>투표 취소</button></p>
  </div>;
}

/* ───────────── 진행 중: 실시간 채팅 ───────────── */

/** 앞 메시지와 같은 사람이 같은 분 안에 보낸 메시지인지. 이름·시간이 같아서 다시 쓰지 않는다.
 *  (탈퇴한 회원은 모두 '알 수 없음'이라 함께 묶일 수 있다) */
function sameRun(previous: DebateMessage, next: DebateMessage): boolean {
  const minute = (message: DebateMessage) => Math.floor(Date.parse(message.createdAt) / 60_000);
  return previous.isMine === next.isMine && previous.nickname === next.nickname && minute(previous) === minute(next);
}

/** 프로필 사진 기능이 생기기 전까지 얼굴 자리에 닉네임 첫 글자를 보여 준다 (2026-11-25 사용자 결정). */
export function nicknameInitial(nickname: string): string {
  return Array.from(nickname.trim())[0] ?? "";
}

/** 도배 막기 창 (2026-11-23 시안): 10초에 5개를 넘게 보내면 서버가 막고(429) 이 창을 띄운다. */
function RateLimitDialog({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return <div className={styles.modalBackdrop} onClick={onClose}>
    <section className={`${styles.confirm} ${styles.rateDialog}`} role="alertdialog" aria-modal="true" aria-labelledby="debate-rate-title"
      aria-describedby="debate-rate-body" onClick={(event) => event.stopPropagation()}>
      <h2 id="debate-rate-title">잠시만요!<span>너무 빠르게 연속으로 메시지를 보냈어요.</span></h2>
      <p id="debate-rate-body">{"채팅 도배는 다른 사용자의\n대화 참여를 방해할 수 있어요."}</p>
      <p className={styles.rateRule}><ClockIcon size={18} />{DEBATE_RULES.messageRateWindowSeconds}초에 {DEBATE_RULES.messageRateCount}개 이하로 메시지를 보내주세요.</p>
      <button type="button" className={styles.confirmOk} onClick={onClose} autoFocus>확인</button>
    </section>
  </div>;
}

function isRateLimited(reason: unknown): boolean {
  return (reason as { code?: unknown } | null)?.code === "DEBATE_MESSAGE_RATE_LIMITED";
}

function OpenRoom({ debate, onChange, update, showToast, toast }: {
  debate: DebateSummary; onChange: (debate: DebateSummary) => void; update: Update; showToast: ShowToast; toast: ReactNode;
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
  const [collapsed, setCollapsed] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  // 화면이 가진 가장 최근 채팅 번호. 실시간 연결을 (다시) 열 때 이 뒤의 채팅부터 받는다.
  const lastIdRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLFormElement>(null);
  // 보내기를 누른 순간 메시지 칸에 포커스가 있었는지 (있었으면 보낸 뒤에도 키보드를 그대로 둔다)
  const typingRef = useRef(false);
  const now = useNow();

  // 폰에서 메시지 칸을 눌러 올린 키보드는 채팅방 화면을 다시 누를 때까지 그대로 둔다 (2026-11-25).
  // 아이폰은 입력창 밖을 눌러도 키보드가 저절로 내려가지 않아서, 입력창 밖을 톡 누르면 직접 포커스를 푼다.
  // 화면을 밀어서 움직이는 것(pointercancel 이나 10px 넘게 움직임)은 누른 것으로 보지 않는다.
  useEffect(() => {
    let start: { x: number; y: number } | null = null;
    const down = (event: PointerEvent) => { start = { x: event.clientX, y: event.clientY }; };
    const cancel = () => { start = null; };
    const up = (event: PointerEvent) => {
      const tapped = start !== null && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 10;
      start = null;
      const input = inputRef.current;
      if (!tapped || !input || document.activeElement !== input) return;
      if (event.target instanceof Node && composerRef.current?.contains(event.target)) return;
      input.blur();
    };
    window.addEventListener("pointerdown", down, { passive: true });
    window.addEventListener("pointercancel", cancel, { passive: true });
    window.addEventListener("pointerup", up, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", down);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("pointerup", up);
    };
  }, []);

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

  // 휴대폰에서 설명이 길면 펼친 토론 카드가 채팅 자리를 거의 다 차지한다 (2026-11-23 사용자 결정, 디자이너와 다시 볼 예정).
  // 그래서 채팅 입력칸을 누르거나(아래 onFocus) 채팅을 손가락·휠·키보드로 움직이면 카드를 접고,
  // 위(지난 채팅 쪽)로 올려 맨 위에 닿으면 다시 펼친다 (채팅이 짧아 움직일 데가 없을 때는 위로 올리는 동작만으로 펼친다).
  // '투표창 접기/펼치기' 버튼도 그대로 된다. 새 메시지를 따라 내려가는 것처럼 화면이 스스로 움직일 때는 바꾸지 않도록 사용자 동작만 본다.
  // 안내 창(이용 안내·도배 막기 창) 안에서 움직인 것은 채팅이 아니라서 뺀다.
  useEffect(() => {
    let lastY: number | null = null;
    let moved = 0;
    let upwardAt = -Infinity;  // 마지막으로 위로 올린 때: 그 직후 맨 위에 닿으면 펼친다
    const atTop = () => window.scrollY <= 0;
    const down = () => setCollapsed(true);
    const up = () => { upwardAt = performance.now(); setCollapsed(!atTop()); };
    const inDialog = (event: Event) => event.target instanceof Element && !!event.target.closest("[role=dialog], [role=alertdialog]");
    const touchStart = (event: TouchEvent) => { lastY = inDialog(event) ? null : event.touches[0]?.clientY ?? null; moved = 0; };
    const touchMove = (event: TouchEvent) => {
      const y = event.touches[0]?.clientY;
      if (lastY === null || y === undefined) return;
      const delta = y - lastY;
      lastY = y;
      moved += Math.abs(delta);
      if (moved <= 8 || delta === 0) return;  // 살짝 누른 것은 움직임으로 보지 않는다
      if (delta < 0) down(); else up();       // 손가락을 위로 = 화면이 아래(새 채팅)로, 아래로 = 위(지난 채팅)로
    };
    const wheel = (event: WheelEvent) => {
      if (event.deltaY === 0 || inDialog(event)) return;
      if (event.deltaY > 0) down(); else up();
    };
    const key = (event: KeyboardEvent) => {
      if (event.target instanceof Element && event.target.closest("input, textarea, select, button, a, [role=dialog], [role=alertdialog]")) return;
      if (["ArrowDown", "PageDown", "End", " "].includes(event.key)) down();
      else if (event.key === "Home") setCollapsed(false);
      else if (["ArrowUp", "PageUp"].includes(event.key)) up();
    };
    // 위로 올리던 중에 화면이 맨 위에 닿으면 한 번 더 올리지 않아도 펼친다
    const scroll = () => { if (atTop() && performance.now() - upwardAt < 700) setCollapsed(false); };
    window.addEventListener("touchstart", touchStart, { passive: true });
    window.addEventListener("touchmove", touchMove, { passive: true });
    window.addEventListener("wheel", wheel, { passive: true });
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", scroll, { passive: true });
    return () => {
      window.removeEventListener("touchstart", touchStart);
      window.removeEventListener("touchmove", touchMove);
      window.removeEventListener("wheel", wheel);
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", scroll);
    };
  }, []);

  // 맨 아래를 보고 있을 때만 새 메시지로 따라 내려간다.
  useEffect(() => {
    const onScroll = () => {
      followRef.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  // 처음 채팅을 불러왔을 때는 항상 최신 메시지(맨 아래)를 보여 준다. 그 뒤에는 맨 아래를 보고 있을 때만 따라간다.
  const landed = useRef(false);
  useEffect(() => {
    if (!messages) return;
    if (!landed.current) {
      landed.current = true;
      followRef.current = true;
      endRef.current?.scrollIntoView({ block: "end" });
      return;
    }
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
      onChange(await chooseSide(debateId, side));
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setChoosing(false);
    }
  };

  // 시안의 '투표 취소': 고른 진영을 취소하고 다시 두 버튼으로 돌아간다 (서버 D-20).
  const cancelMySide = async () => {
    if (!debate.mySide || choosing) return;
    setChoosing(true);
    try {
      onChange(await cancelSide(debateId));
    } catch (reason) {
      showToast(errorText(reason));
    } finally {
      setChoosing(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    // 보내기 버튼을 눌러서 보낸 경우: 누른 동작 안에서 바로 메시지 칸으로 포커스를 돌려 둔다 (아이폰 키보드가 내려가지 않게)
    if (typingRef.current) inputRef.current?.focus({ preventScroll: true });
    typingRef.current = false;
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      const sent = await sendMessage(debateId, text);
      followRef.current = true;
      merge([sent]);
      setText("");
    } catch (reason) {
      if (isRateLimited(reason)) setRateLimited(true);
      else showToast(errorText(reason));
    } finally {
      setSending(false);
    }
  };
  const closeRateLimit = useCallback(() => setRateLimited(false), []);

  return <DebateScreen composer label={debate.title}>
    <RoomHeader title={debate.title} back="/pivot/debate" />
    <RoomPanel debate={debate} now={now} collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)}>
      <SidePoll debate={debate} busy={choosing} onPick={(side) => void pickSide(side)} onCancel={() => void cancelMySide()} />
    </RoomPanel>
    <div className={styles.chat}>
      {connection === "reconnecting" ? <p className={styles.connectionNote} role="status">연결이 잠깐 끊겼어요. 다시 연결하는 중이에요.</p> : null}
      {connection === "stopped" ? <p className={styles.connectionNote} data-tone="error" role="status">
        실시간 연결이 끊겼어요. 이 토론방을 다른 창에서도 열어 두었다면 닫고 다시 연결해 주세요.
        <button type="button" onClick={reconnect}>다시 연결</button>
      </p> : null}
      {messages === null ? <DebateState loading /> : null}
      {messages?.length === 0 ? <p className={styles.chatEmpty}>아직 채팅이 없어요. 첫 마디를 남겨보세요.</p> : null}
      {olderCursor ? <button type="button" className={styles.olderButton} disabled={loadingOlder} onClick={() => void loadOlder()}>
        {loadingOlder ? "불러오는 중이에요" : "이전 채팅 더 보기"}
      </button> : null}
      <ol className={styles.messages} aria-label="채팅" aria-live="polite">
        {/* 시안: 다른 사람은 왼쪽 흰 말풍선, 내 메시지는 오른쪽 연보라 말풍선. 얼굴 자리는 연보라 동그라미에 닉네임 첫 글자.
            진영 표시는 닉네임 옆에 두지 않는다 (2026-11-25 디자이너 요청).
            같은 사람이 같은 분 안에 연달아 보내면 이름·얼굴·시간 없이 말풍선만 바로 아래에 붙인다 (2026-11-24, 카카오톡처럼). */}
        {(messages ?? []).map((item, index, list) => {
          const continued = index > 0 && sameRun(list[index - 1], item);
          return <li key={item.messageId} className={styles.message} data-mine={item.isMine || undefined} data-continued={continued || undefined}>
            <span className={styles.avatar} aria-hidden="true">{nicknameInitial(item.nickname)}</span>
            <div className={styles.messageBody}>
              {continued
                // 화면 읽기 프로그램은 말풍선마다 누가 보냈는지 읽어 준다.
                ? <span className={styles.srOnly}>{item.nickname}</span>
                : <p className={styles.messageMeta}>
                  <b>{item.nickname}</b>
                  <time dateTime={item.createdAt}>{timeText(item.createdAt)}</time>
                </p>}
              <p className={styles.bubble}>{item.body}</p>
            </div>
          </li>;
        })}
      </ol>
      <div ref={endRef} className={styles.chatEnd} />
    </div>

    <form ref={composerRef} className={styles.composer} onSubmit={(event) => void submit(event)}>
      <input ref={inputRef} value={text} onChange={(event) => setText(event.target.value)} onFocus={() => setCollapsed(true)} maxLength={DEBATE_RULES.messageMax}
        placeholder="메시지를 입력하세요" aria-label="메시지" enterKeyHint="send" />
      {/* 보내기 버튼은 눌러도 포커스를 가져가지 않는다: 가져가면 메시지 칸에서 포커스가 빠져 폰 키보드가 내려간다 */}
      <button type="submit" className={styles.sendButton} aria-label="보내기" disabled={!text.trim() || sending}
        onPointerDown={() => { typingRef.current = document.activeElement === inputRef.current; }}
        onMouseDown={(event) => event.preventDefault()}><SendIcon size={18} /></button>
    </form>
    {rateLimited ? <RateLimitDialog onClose={closeRateLimit} /> : null}
    {toast}
  </DebateScreen>;
}

/* ───────────── 종료 ───────────── */

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
        <span className={styles.roomChip}><UsersIcon size={14} />총 {debate.participantCount}명 투표</span>
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
