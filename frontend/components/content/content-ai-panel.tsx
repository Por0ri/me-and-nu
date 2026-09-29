"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";

import { SourceInfo } from "@/components/content/source-info";
import { sendContentAIQuestion } from "@/lib/consumer-api/ai";
import type { ContentAIResponse } from "@/types/ai";

type ContentAIPanelProps = {
  contentId: string;
  topicId: string;
};

const CONTENT_EXAMPLE_QUESTIONS = [
  "이 글 내용이 정확한 사실인지 확인해줄래?",
  "이 작품을 처음 보는 사람에게 추천해도 될까?",
  "비슷한 분위기의 작품도 알려줘",
];

type AIRequestState =
  | { status: "idle" }
  | { status: "pending"; question: string }
  | { status: "success"; question: string; result: ContentAIResponse }
  | { status: "error"; question: string };

export function ContentAIPanel({ contentId, topicId }: ContentAIPanelProps) {
  const inputId = useId();
  const [question, setQuestion] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [request, setRequest] = useState<AIRequestState>({ status: "idle" });
  const inFlight = useRef(false);
  const requestGeneration = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [isAiOpen, setIsAiOpen] = useState(false);
  const isPending = request.status === "pending";

  useEffect(() => {
    requestGeneration.current += 1;

    return () => {
      requestGeneration.current += 1;
      inFlight.current = false;
    };
  }, [contentId, topicId]);

  useEffect(() => {
    if (!isAiOpen) {
      return;
    }

    const body = document.body;
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const previousStyles = ["overflow", "position", "top", "left", "width"].map(
      (property) => ({
        property,
        value: body.style.getPropertyValue(property),
        priority: body.style.getPropertyPriority(property),
      }),
    );

    body.style.width = `${document.documentElement.clientWidth}px`;
    body.style.overflow = "hidden";
    body.style.position = "fixed";
    body.style.top = `-${scrollY}px`;
    body.style.left = `-${scrollX}px`;

    return () => {
      for (const { property, value, priority } of previousStyles) {
        body.style.setProperty(property, value, priority);
      }
      window.scrollTo({ left: scrollX, top: scrollY, behavior: "instant" });
    };
  }, [isAiOpen]);

  function openAI() {
    const dialog = dialogRef.current;

    if (!dialog || dialog.open) {
      return;
    }

    dialog.showModal();
    setIsAiOpen(true);

    // A pending request disables the textarea, but must not prevent reopening.
    if (isPending) {
      dialog
        .querySelector<HTMLButtonElement>("[data-ai-close]")
        ?.focus({ preventScroll: true });
    } else {
      inputRef.current?.focus({ preventScroll: true });
    }
  }

  function closeAI() {
    dialogRef.current?.close();
  }

  function handleAIClose() {
    if (dialogRef.current?.open) {
      return;
    }

    setIsAiOpen(false);
    if (triggerRef.current?.isConnected) {
      triggerRef.current.focus({ preventScroll: true });
    }
  }

  async function submitQuestion(value: string) {
    if (inFlight.current) {
      return;
    }

    const trimmedQuestion = value.trim();

    if (!trimmedQuestion) {
      setValidationError("질문을 입력해 주세요.");
      return;
    }

    const generation = requestGeneration.current;
    inFlight.current = true;
    setValidationError(null);
    setRequest({ status: "pending", question: trimmedQuestion });

    try {
      const result = await sendContentAIQuestion({
        contentId,
        topicContext: { topicId },
        question: trimmedQuestion,
      });

      if (requestGeneration.current === generation) {
        setRequest({ status: "success", question: trimmedQuestion, result });
      }
    } catch {
      if (requestGeneration.current === generation) {
        setRequest({ status: "error", question: trimmedQuestion });
      }
    } finally {
      if (requestGeneration.current === generation) {
        inFlight.current = false;
      }
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label="이 글에 대해 AI 질문 열기"
        aria-haspopup="dialog"
        aria-expanded={isAiOpen}
        aria-controls={`${inputId}-dialog`}
        onClick={openAI}
        className="va-ai-jump va-article-ai-trigger"
      >
        <Image src="/ui-version-a/edit.svg" alt="" width={28} height={28} />
      </button>
      <dialog
        ref={dialogRef}
        id={`${inputId}-dialog`}
        aria-labelledby={`${inputId}-title`}
        className="va-ai-dialog va-article-ai-dialog"
        onClose={handleAIClose}
        onCancel={(event) => {
          event.preventDefault();
          closeAI();
        }}
        onClick={(event) => {
          if (event.target !== event.currentTarget) {
            return;
          }

          const bounds = event.currentTarget.getBoundingClientRect();
          if (
            event.clientX < bounds.left ||
            event.clientX > bounds.right ||
            event.clientY < bounds.top ||
            event.clientY > bounds.bottom
          ) {
            closeAI();
          }
        }}
      >
        <div className="va-ai-sheet">
          <header className="va-ai-sheet-header">
            <span aria-hidden="true" className="va-ai-grabber" />
            <p className="va-muted text-xs">현재 글에 대한 질문</p>
            <button
              data-ai-close
              type="button"
              onClick={closeAI}
              aria-label="AI 질문 닫기"
              className="va-ai-close"
            >
              닫기
            </button>
          </header>

          <div className="va-ai-scroll">
            <h2
              id={`${inputId}-title`}
              className={request.status === "idle" ? "va-ai-title" : "sr-only"}
            >
              현재 컨텐츠에 대한<br />다양한 질문을 해보세요!
            </h2>
            {request.status === "idle" ? (
              <ul aria-label="예시 질문" className="va-ai-examples">
                {CONTENT_EXAMPLE_QUESTIONS.map((example) => (
                  <li key={example}>
                    <button type="button" disabled={isPending} onClick={() => void submitQuestion(example)}>
                      {example}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}

            {request.status !== "idle" ? (
              <p className="va-ai-question whitespace-pre-wrap text-sm leading-6">
                <span className="sr-only">전송한 질문: </span>{request.question}
              </p>
            ) : null}
            {isPending ? (
              <p role="status" className="va-muted text-sm">
                답변을 기다리는 중입니다.
              </p>
            ) : null}
            {request.status === "error" ? (
              <div className="space-y-3">
                <p role="alert" className="text-sm text-red-600">
                  AI 답변을 불러오지 못했습니다. 다시 시도해 주세요.
                </p>
                <button
                  type="button"
                  disabled={isPending}
                  onClick={() => void submitQuestion(request.question)}
                  className="va-secondary"
                >
                  실패한 질문 다시 시도
                </button>
              </div>
            ) : null}
            {request.status === "success" ? (
              <>
                <div className="va-ai-answer space-y-3" role="status">
                  <h3 className="sr-only">AI 답변</h3>
                  <p className="whitespace-pre-wrap text-sm leading-6">
                    {request.result.answer}
                  </p>
                  {request.result.evidenceStatus === "insufficient" ? (
                    <p className="va-muted text-sm leading-6">
                      현재 근거가 충분하지 않습니다.
                    </p>
                  ) : null}
                </div>
                <div className="va-article-sources">
                  <SourceInfo
                    sources={request.result.sources}
                    headingId={`${inputId}-sources`}
                    title="AI 답변 근거"
                    internal
                  />
                </div>
              </>
            ) : null}
          </div>

          <form
            className="va-ai-composer"
            aria-busy={isPending}
            onSubmit={(event) => {
              event.preventDefault();
              void submitQuestion(question);
            }}
          >
            <label htmlFor={inputId} className="sr-only">
              이 글에 대한 AI 질문
            </label>
            <div className="va-ai-input-wrap">
              <textarea
                id={inputId}
                ref={inputRef}
                value={question}
                disabled={isPending}
                aria-invalid={validationError !== null}
                aria-describedby={validationError ? `${inputId}-error` : undefined}
                onChange={(event) => {
                  setQuestion(event.target.value);
                  setValidationError(null);
                }}
                rows={2}
                placeholder="현재 컨텐츠 내용에 대한 질문"
                className="va-ai-input"
              />
              <button
                type="submit"
                disabled={isPending}
                className="va-ai-send"
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d="M12 19V5M5 12l7-7 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span className="sr-only">전송</span>
              </button>
            </div>
            {validationError ? (
              <p id={`${inputId}-error`} role="alert" className="mt-3 text-sm text-red-600">
                {validationError}
              </p>
            ) : null}
          </form>
        </div>
      </dialog>
    </>
  );
}
