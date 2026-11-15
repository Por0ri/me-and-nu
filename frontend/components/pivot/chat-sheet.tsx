"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import type { HomeDetailHandler, HomeDetailTarget } from "@/lib/pivot/home";
import type { SavedCard } from "@/lib/pivot/saved";
import { detailHref } from "@/lib/pivot/detail-navigation";
import { chatConversation, type ChatAnswer, type ChatPlace, type ChatTurn } from "@/lib/pivot/chat-service";
import { AI_QUESTION_MAX_LENGTH } from "@/lib/ai-question";
import { useDragScroll } from "@/lib/pivot/drag-scroll";
import { ProtectedSlideImage } from "./content-carousel";
import { useReportViewport } from "./report-viewport";
import styles from "./chat.module.css";

export type ChatCopy = { title: string; placeholder: string; examples: readonly string[] };

// 디자인 시안 문구 (11/13). 상세 예시 질문은 시안의 음악 글 예시 대신 어느 글에나 맞는 질문을 쓴다 (서원 결정 11/15).
export const HOME_CHAT_COPY: ChatCopy = {
  title: "어떤 콘텐츠를 찾고 계시나요?",
  placeholder: "보고싶은 콘텐츠가 무엇인가요?",
  examples: ["판타지 장르의 애니 콘텐츠 찾아줘", "‘나루토’ 관련 카드뉴스 보여줘", "팝업이나 전시 콘텐츠만 보고 싶어"],
};
export const DETAIL_CHAT_COPY: ChatCopy = {
  title: "이 콘텐츠에서 어떤 점이 궁금하세요?",
  placeholder: "콘텐츠 내용 중 궁금한 점이 있나요?",
  examples: ["이 글 핵심만 짧게 알려줘", "이 작품 정보 알려줘", "이거랑 비슷한 글 보여줘"],
};
// 답 옆 얼굴: 켜진 얼굴과 같은 그림에서 그림자를 뺀 것
const FACE = "/pivot/chat/face.svg";
const CLOSE_DRAG = 80; // 손잡이를 이만큼 넘게 끌어내리면 닫는다
const DRAG_SLOP = 6; // 이보다 적게 움직이면 끈 게 아니라 누른 것

function SendIcon() {
  return <svg width={16} height={16} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z" fill="currentColor" />
  </svg>;
}

/** 고른 글 카드: 홈 피드처럼 제목을 그림 위에 얹는다. */
function ChatCard({ card, onOpen }: { card: SavedCard; onOpen: (card: SavedCard) => void }) {
  const [failed, setFailed] = useState(false);
  return <button type="button" className={styles.card} data-chat-content={card.contentId} onClick={() => onOpen(card)}>
    <span className={styles.thumb}>
      {card.image?.protected ? <ProtectedSlideImage key={card.image.src} src={card.image.src} alt="" retryable={false} />
        : card.image?.src && !failed ? <img src={card.image.src} alt="" width={144} height={180} loading="lazy" draggable={false} onError={() => setFailed(true)} />
        : <span className={styles.placeholder}>이미지 준비 중</span>}
      <span className={styles.cardOverlay}>
        {card.eyebrow ? <span className={styles.cardEyebrow}>{card.eyebrow}</span> : null}
        <span className={styles.cardTitle}>{card.title}</span>
      </span>
    </span>
  </button>;
}

function Answer({ answer, onOpen }: { answer: ChatAnswer; onOpen: (card: SavedCard) => void }) {
  // 마우스로 잡아 옆으로 넘기기 (터치는 원래대로 손가락으로 넘긴다). 끈 다음에는 카드가 눌리지 않는다.
  const dragScroll = useDragScroll<HTMLUListElement>();
  return <div className={styles.answerBody}>
    <p className={styles.bubble}>{answer.text}</p>
    {answer.cards.length ? <ul className={styles.cards} aria-label="찾은 글" {...dragScroll}>
      {answer.cards.map(card => <li key={card.contentId}><ChatCard card={card} onOpen={onOpen} /></li>)}
    </ul> : null}
    {answer.notes.map(note => <p key={note} className={styles.meta}>{note}</p>)}
    {answer.references.length ? <p className={styles.meta}>참고한 글{" "}
      {answer.references.map((card, index) => <span key={card.contentId}>{index ? " · " : ""}
        <button type="button" onClick={() => onOpen(card)}>{card.title}</button></span>)}
    </p> : null}
    {answer.facts.length ? <p className={styles.meta}>근거{" "}
      {answer.facts.map((fact, index) => <span key={fact.url}>{index ? " · " : ""}
        <a href={fact.url} target="_blank" rel="noopener noreferrer">{fact.name}</a></span>)}
    </p> : null}
  </div>;
}

function Turn({ turn, onOpen, onRetry }: { turn: ChatTurn; onOpen: (card: SavedCard) => void; onRetry: (question: string) => void }) {
  if (turn.role === "user") return <p className={styles.question}>{turn.text}</p>;
  return <div className={styles.answer} data-chat-turn={turn.status}>
    <img className={styles.avatar} src={FACE} alt="" width={32} height={32} />
    {turn.status === "pending" ? <p className={styles.typing} role="status" aria-label="답을 만들고 있어"><span /><span /><span /></p>
      : turn.status === "error" ? <div className={styles.answerBody}>
        <p className={styles.bubble} data-error="">{turn.message}</p>
        <button type="button" className={styles.retry} onClick={() => onRetry(turn.question)}>다시 묻기</button>
      </div>
      : <Answer answer={turn.answer} onOpen={onOpen} />}
  </div>;
}

/** 홈 · 상세 공용 AI 대화 시트 (디자인 시안: 손잡이 · 가운데 제목 · 예시 질문 · 입력창).
 *  브라우저 기본 대화상자라 뒤 화면은 막히고, 손잡이를 끌어내리거나 · 누르거나 · 바깥을 누르거나 · Esc · 휴대폰 뒤로 가기로 닫힌다. */
export function ChatSheet({ place, copy, onClose, onViewDetail }: {
  place: ChatPlace; copy: ChatCopy; onClose: () => void;
  /** 글 카드를 누르면 여는 상세. 없으면 상세 주소로 이동한다. */
  onViewDetail?: HomeDetailHandler;
}) {
  const dialog = useRef<HTMLDialogElement>(null), title = useRef<HTMLHeadingElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const id = useId();
  const conversation = chatConversation(place);
  const snapshot = useSyncExternalStore(conversation.subscribe, conversation.snapshot, conversation.snapshot);
  const [draft, setDraft] = useState("");
  const [drag, setDrag] = useState<number | null>(null);
  const dragged = useRef(false);
  const pending = useRef<HomeDetailTarget | null>(null); // 카드를 눌러 시트를 닫은 뒤 열 상세
  const latest = useRef({ onClose, onViewDetail });
  useEffect(() => { latest.current = { onClose, onViewDetail }; });

  function goTo(target: HomeDetailTarget) {
    if (latest.current.onViewDetail) latest.current.onViewDetail(target);
    else window.location.assign(detailHref(window.location.pathname, target));
  }
  /** 시트를 열 때 넣은 방문 기록 한 칸을 되돌려 닫는다 (뒤로 가기와 같은 길). */
  function requestClose(target?: HomeDetailTarget) {
    pending.current = target ?? null;
    if (window.history.state?.pivotChatSheet) { window.history.back(); return; }
    latest.current.onClose();
    if (target) goTo(target);
  }
  useEffect(() => {
    // 휴대폰 뒤로 가기로 시트만 닫히게 방문 기록에 한 칸 넣는다 (신고 창과 같은 방식). 개발 모드에서 두 번 돌아도 한 칸만.
    if (!window.history.state?.pivotChatSheet) window.history.pushState({ ...window.history.state, pivotChatSheet: true }, "");
    const onPop = () => {
      const target = pending.current;
      pending.current = null;
      latest.current.onClose();
      if (target) goTo(target);
    };
    window.addEventListener("popstate", onPop);
    const element = dialog.current, trigger = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element?.showModal(); title.current?.focus({ preventScroll: true });
    return () => {
      window.removeEventListener("popstate", onPop);
      element?.close(); document.body.style.overflow = previousOverflow;
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, []);
  useReportViewport(dialog);
  const last = snapshot.turns.at(-1);
  const lastKey = last ? `${last.id}:${last.role === "assistant" ? last.status : "user"}` : "";
  useEffect(() => {
    const element = log.current;
    if (!element || !lastKey) return;
    const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [lastKey]);
  function ask(question: string, from: "input" | "example") {
    if (!question.trim() || snapshot.busy) return;
    void conversation.ask(question);
    if (from === "input") setDraft("");
    else log.current?.focus({ preventScroll: true });
  }
  function open(card: SavedCard) {
    requestClose({ postId: card.postId, contentId: card.contentId, ...(card.topicId === undefined ? {} : { topicId: card.topicId }) });
  }
  function startDrag(event: ReactPointerEvent) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const start = event.clientY;
    let offset = 0;
    dragged.current = false;
    const move = (moveEvent: PointerEvent) => {
      offset = Math.max(0, moveEvent.clientY - start);
      if (offset > DRAG_SLOP) dragged.current = true;
      if (dragged.current) setDrag(offset);
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      setDrag(null);
      if (dragged.current && offset > CLOSE_DRAG) requestClose();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  }
  return <dialog ref={dialog} className={styles.sheet} aria-labelledby={`${id}-title`} data-chat-place={place.kind}
    data-dragging={drag !== null || undefined} style={drag !== null ? { "--chat-drag": `${drag}px` } as CSSProperties : undefined}
    onCancel={event => { event.preventDefault(); requestClose(); }}
    onClick={event => { if (event.target === event.currentTarget) requestClose(); }}>
    <div className={styles.panel}>
      <header className={styles.header} onPointerDown={startDrag}>
        <button type="button" className={styles.handle} aria-label="AI 대화 닫기"
          onClick={() => { if (dragged.current) { dragged.current = false; return; } requestClose(); }} />
        <h2 ref={title} id={`${id}-title`} tabIndex={-1}>{copy.title}</h2>
      </header>
      <div ref={log} className={styles.log} role="log" aria-label="AI 대화" tabIndex={-1}>
        {snapshot.turns.length === 0
          ? <ul className={styles.examples} aria-label="예시 질문">{copy.examples.map(example => <li key={example}>
            <button type="button" onClick={() => ask(example, "example")}>{example}</button>
          </li>)}</ul>
          : <>
            <p className={styles.aiNotice}>{snapshot.aiNotice}</p>
            {snapshot.turns.map(turn => <Turn key={turn.id} turn={turn} onOpen={open} onRetry={question => ask(question, "example")} />)}
          </>}
      </div>
      <form className={styles.composer} onSubmit={event => { event.preventDefault(); ask(draft, "input"); }}>
        <input value={draft} onChange={event => setDraft(event.target.value)} placeholder={copy.placeholder}
          aria-label="AI 에게 물어볼 내용" maxLength={AI_QUESTION_MAX_LENGTH} enterKeyHint="send" autoComplete="off" />
        <button type="submit" className={styles.send} aria-label="보내기" disabled={!draft.trim() || snapshot.busy}><SendIcon /></button>
      </form>
    </div>
  </dialog>;
}
