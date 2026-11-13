"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import type { HomeDetailHandler } from "@/lib/pivot/home";
import type { SavedCard } from "@/lib/pivot/saved";
import { detailHref } from "@/lib/pivot/detail-navigation";
import { chatConversation, type ChatAnswer, type ChatPlace, type ChatTurn } from "@/lib/pivot/chat-service";
import { AI_QUESTION_MAX_LENGTH } from "@/lib/ai-question";
import { ProtectedSlideImage } from "./content-carousel";
import { useReportViewport } from "./report-viewport";
import styles from "./chat.module.css";

export type ChatCopy = { title: string; placeholder: string; examples: readonly string[] };

// 디자인 시안 문구 (11/13). 상세 예시 질문은 시안의 음악 글 예시 대신 어느 글에나 맞는 질문을 쓴다.
export const HOME_CHAT_COPY: ChatCopy = {
  title: "어떤 콘텐츠를 찾고 계시나요?",
  placeholder: "보고싶은 콘텐츠가 무엇인가요?",
  examples: ["판타지 장르의 애니 콘텐츠 찾아줘", "'나루토' 관련 카드뉴스 보여줘", "팝업이나 전시 콘텐츠만 보고 싶어"],
};
export const DETAIL_CHAT_COPY: ChatCopy = {
  title: "이 콘텐츠에서 어떤 점이 궁금하세요?",
  placeholder: "콘텐츠 내용 중 궁금한 점이 있나요?",
  examples: ["이 글 핵심만 짧게 알려줘", "이 작품 정보 알려줘", "이거랑 비슷한 글 보여줘"],
};
const FACE = "/pivot/detail/ai-expanded.svg";

function CloseIcon() {
  return <svg width={20} height={20} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5l5.6 5.6L17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4-5.6-5.6Z" fill="currentColor" />
  </svg>;
}
function SendIcon() {
  return <svg width={18} height={18} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    <path d="M3.4 20.4 21 12 3.4 3.6 3.4 10l12.6 2-12.6 2z" fill="currentColor" />
  </svg>;
}

function ChatCard({ card, onOpen }: { card: SavedCard; onOpen: (card: SavedCard) => void }) {
  const [failed, setFailed] = useState(false);
  return <button type="button" className={styles.card} data-chat-content={card.contentId} onClick={() => onOpen(card)}>
    <span className={styles.thumb}>
      {card.image?.protected ? <ProtectedSlideImage key={card.image.src} src={card.image.src} alt="" retryable={false} />
        : card.image?.src && !failed ? <img src={card.image.src} alt="" width={120} height={150} loading="lazy" draggable={false} onError={() => setFailed(true)} />
        : <span className={styles.placeholder}>이미지 준비 중</span>}
    </span>
    <span className={styles.cardTitle}>{card.title}</span>
  </button>;
}

function Answer({ answer, onOpen }: { answer: ChatAnswer; onOpen: (card: SavedCard) => void }) {
  return <div className={styles.answerBody}>
    <p className={styles.bubble}>{answer.text}</p>
    {answer.cards.length ? <ul className={styles.cards} aria-label="찾은 글">
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

/** 홈 · 상세 공용 AI 대화 시트. 브라우저 기본 대화상자라 뒤 화면은 막히고 Esc 로 닫힌다. */
export function ChatSheet({ place, copy, onClose, onViewDetail }: {
  place: ChatPlace; copy: ChatCopy; onClose: () => void;
  /** 글 카드를 누르면 여는 상세. 없으면 상세 주소로 이동한다. */
  onViewDetail?: HomeDetailHandler;
}) {
  const dialog = useRef<HTMLDialogElement>(null), title = useRef<HTMLHeadingElement>(null);
  const log = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null);
  const id = useId();
  const conversation = chatConversation(place);
  const snapshot = useSyncExternalStore(conversation.subscribe, conversation.snapshot, conversation.snapshot);
  const [draft, setDraft] = useState("");
  useEffect(() => {
    const element = dialog.current, trigger = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    element?.showModal(); title.current?.focus({ preventScroll: true });
    return () => {
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
    const target = { postId: card.postId, contentId: card.contentId, ...(card.topicId === undefined ? {} : { topicId: card.topicId }) };
    onClose();
    if (onViewDetail) onViewDetail(target);
    else window.location.assign(detailHref(window.location.pathname, target));
  }
  return <dialog ref={dialog} className={styles.sheet} aria-labelledby={`${id}-title`} data-chat-place={place.kind}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={styles.panel}>
      <header className={styles.header}>
        <img className={styles.face} src={FACE} alt="" width={40} height={40} />
        <h2 ref={title} id={`${id}-title`} tabIndex={-1}>{copy.title}</h2>
        <button type="button" className={styles.close} aria-label="AI 대화 닫기" onClick={onClose}><CloseIcon /></button>
      </header>
      <div ref={log} className={styles.log} role="log" aria-label="AI 대화" tabIndex={-1}>
        {snapshot.turns.length === 0 ? <section aria-labelledby={`${id}-examples`}>
          <h3 id={`${id}-examples`} className={styles.examplesTitle}>이렇게 물어볼 수 있어요</h3>
          <ul className={styles.examples}>{copy.examples.map(example => <li key={example}>
            <button type="button" onClick={() => ask(example, "example")}>{example}</button>
          </li>)}</ul>
        </section> : snapshot.turns.map(turn => <Turn key={turn.id} turn={turn} onOpen={open} onRetry={question => ask(question, "example")} />)}
      </div>
      <form className={styles.composer} onSubmit={event => { event.preventDefault(); ask(draft, "input"); }}>
        <input ref={input} value={draft} onChange={event => setDraft(event.target.value)} placeholder={copy.placeholder}
          aria-label="AI 에게 물어볼 내용" maxLength={AI_QUESTION_MAX_LENGTH} enterKeyHint="send" autoComplete="off" />
        <button type="submit" className={styles.send} aria-label="보내기" disabled={!draft.trim() || snapshot.busy}><SendIcon /></button>
      </form>
      <p className={styles.aiNotice}>{snapshot.aiNotice}</p>
    </div>
  </dialog>;
}
