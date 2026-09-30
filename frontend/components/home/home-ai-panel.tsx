"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";

import { SourceInfo } from "@/components/content/source-info";
import { sendHomeAIQuestion } from "@/lib/consumer-api/ai";
import type { HomeAIResponse } from "@/types/ai";
import type { Topic } from "@/types/home";

const HOME_EXAMPLE_QUESTIONS = [
  "요즘 화제인 작품 이야기를 보고 싶어",
  "평론가가 분석한 글 위주로 보여줘",
  "가볍게 읽을 수 있는 글 추천해줘",
];

type HomeAIPanelProps = {
  topic: Topic;
  availableTopicIds: string[];
};

type HomeAIRequestState =
  | { status: "idle" }
  | { status: "pending"; question: string }
  | { status: "success"; question: string; result: HomeAIResponse }
  | { status: "error"; question: string };

export function HomeAIPanel({ topic, availableTopicIds }: HomeAIPanelProps) {
  const inputId = useId();
  const [question, setQuestion] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [request, setRequest] = useState<HomeAIRequestState>({ status: "idle" });
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
  }, [topic.id]);

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

    // Keep focus inside the sheet when the pending textarea is disabled.
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
      const result = await sendHomeAIQuestion({
        topicContext: { topicId: topic.id },
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
        aria-label="현재 분야 AI 질문 열기"
        aria-haspopup="dialog"
        aria-expanded={isAiOpen}
        aria-controls={`${inputId}-dialog`}
        onClick={openAI}
        className="va-ai-jump"
      >
        <Image src="/ui-version-a/edit.svg" alt="" width={28} height={28} />
      </button>
      <dialog
        ref={dialogRef}
        id={`${inputId}-dialog`}
        aria-labelledby={`${inputId}-title`}
        className="va-ai-dialog"
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
            <p className="va-muted text-xs">현재 분야: {topic.name}</p>
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
              보고싶은 글의 유형을<br />알려주세요!
            </h2>
            {request.status === "idle" ? (
              <>
                <ul aria-label="예시 질문" className="va-ai-examples">
                  {HOME_EXAMPLE_QUESTIONS.map((example) => (
                    <li key={example}>
                      <button type="button" disabled={isPending} onClick={() => void submitQuestion(example)}>
                        {example}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
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
              request.result.type === "answer" ? (
                <>
                  <div className="va-ai-answer space-y-2" role="status">
                    <h3 className="sr-only">AI 답변</h3>
                    <p className="whitespace-pre-wrap text-sm leading-6">
                      {request.result.answer}
                    </p>
                  </div>
                  {request.result.sources?.length ? (
                    <div className="va-article-sources">
                      <SourceInfo
                        sources={request.result.sources}
                        headingId={`${inputId}-sources`}
                        title="AI 답변 근거"
                        internal
                      />
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="va-ai-answer space-y-2" role="status">
                  <h3 className="text-sm font-semibold">
                    분야 이동 제안: {request.result.targetTopic.name}
                  </h3>
                  <p className="text-sm leading-6">
                    {request.result.message}
                  </p>
                  <p className="va-muted text-sm">
                    {availableTopicIds.includes(request.result.targetTopic.id)
                      ? "이미 생성된 분야입니다."
                      : "현재 생성된 분야가 아닙니다."}
                  </p>
                </div>
              )
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
              AI 질문
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
                placeholder="매거진 목록에 대한 의견을 알려주세요"
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
