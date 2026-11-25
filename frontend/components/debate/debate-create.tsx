"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";
import { usePivotSession } from "@/components/pivot/pivot-session";
import { createDebate, searchWorks } from "@/lib/debate/client";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateWorkOption } from "@/lib/debate/types";
import { DebateHomeHeader, DebateScreen, errorText, useToast } from "./debate-chrome";
import { CloseIcon } from "./debate-icons";
import { ConfirmDialog, DebateGuide } from "./debate-parts";
import styles from "./debate.module.css";

/*
 * 주제 올리기 (2026-11-23 시안 + 사용자 지시):
 *  - 애니메이션 이름은 우리 작품 목록에서 검색해 고른다 (2개까지, 2026-11-25 시안).
 *  - 설명은 100자, 투표 선택지는 8자까지 (2026-11-25 시안). 선택지 칸에는 '후보'라고만 보여 준다.
 *  - 하나라도 쓰면 '임시 저장'이, 필수 칸(작품·제목·선택지 두 개)을 다 채우면 '주제 올리기'도 켜진다 (2026-11-25 시안).
 *    올리기·임시 저장 모두 확인 창을 거친다.
 *  - 임시 저장은 이 브라우저에 회원별로 남는다 (다른 기기에서는 보이지 않는다). 다시 들어오면 불러오고, 올리면 지운다.
 */

type Draft = { works: DebateWorkOption[]; title: string; description: string; sideA: string; sideB: string };
const DRAFT_PREFIX = "pivot.debate-draft.";
const WORK_OPTIONS_MAX = 5;

/** 저장해 둔 값이 지금 화면이 쓰는 모양과 다르면(예전 모양·손으로 바뀐 값) 불러오지 않는다. 화면이 오류로 멈추지 않게 다섯 칸을 다 본다. */
function isDraft(value: unknown): value is Draft {
  if (!value || typeof value !== "object") return false;
  const draft = value as Record<string, unknown>;
  return (["title", "description", "sideA", "sideB"] as const).every((field) => typeof draft[field] === "string")
    && Array.isArray(draft.works)
    && draft.works.every((work: unknown) => {
      const row = work as Partial<DebateWorkOption> | null;
      return !!row && typeof row === "object" && Number.isSafeInteger(row.entityId) && typeof row.name === "string"
        && Array.isArray(row.aliases) && row.aliases.every((alias) => typeof alias === "string");
    });
}

function draftStore(userId: number | null) {
  const key = userId === null ? null : DRAFT_PREFIX + userId;
  return {
    read(): Draft | null {
      if (!key) return null;
      try {
        const value: unknown = JSON.parse(window.localStorage.getItem(key) ?? "null");
        return isDraft(value) ? value : null;
      } catch { return null; }
    },
    write(draft: Draft): boolean {
      if (!key) return false;
      try { window.localStorage.setItem(key, JSON.stringify(draft)); return true; } catch { return false; }
    },
    clear() {
      if (!key) return;
      try { window.localStorage.removeItem(key); } catch { /* 저장소가 막혀 있으면 지울 것도 없다 */ }
    },
  };
}

export function DebateCreate() {
  const router = useRouter();
  const { state } = usePivotSession();
  const session = state.session;
  const userId = session?.authenticated === true && typeof session.user?.id === "number" ? session.user.id : null;
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [sideA, setSideA] = useState("");
  const [sideB, setSideB] = useState("");
  const [works, setWorks] = useState<DebateWorkOption[]>([]);
  const [workText, setWorkText] = useState("");
  const [workOptions, setWorkOptions] = useState<{ text: string; rows: DebateWorkOption[] }>({ text: "", rows: [] });
  const [confirm, setConfirm] = useState<"draft" | "submit" | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [toast, showToast] = useToast();
  const restored = useRef(false);
  const ids = useId();

  // 임시 저장한 글이 있으면 처음 한 번 불러온다 (로그인 정보를 읽은 뒤).
  useEffect(() => {
    if (restored.current || userId === null) return;
    restored.current = true;
    const draft = draftStore(userId).read();
    if (!draft) return;
    // 상한을 줄여서(선택지 20 → 12 → 8자, 설명 200 → 100자, 작품 3 → 2개) 그 전에 저장한 글은 더 길 수 있다. 서버처럼 글자 단위로 자른다.
    const clip = (text: string, max: number) => Array.from(text).slice(0, max).join("");
    setWorks(draft.works.slice(0, DEBATE_RULES.worksMax));
    setTitle(draft.title);
    setDescription(clip(draft.description, DEBATE_RULES.descriptionMax));
    setSideA(clip(draft.sideA, DEBATE_RULES.sideLabelMax));
    setSideB(clip(draft.sideB, DEBATE_RULES.sideLabelMax));
    showToast("임시 저장한 내용을 불러왔어요.", "done");
  }, [userId, showToast]);

  const typed = workText.trim();
  useEffect(() => {
    if (!typed) return;
    let alive = true;
    const timer = setTimeout(() => {
      searchWorks(typed).then((rows) => { if (alive) setWorkOptions({ text: typed, rows }); }, () => undefined);
    }, 200);
    return () => { alive = false; clearTimeout(timer); };
  }, [typed]);
  const options = typed && workOptions.text === typed
    ? workOptions.rows.filter((option) => !works.some((work) => work.entityId === option.entityId)).slice(0, WORK_OPTIONS_MAX)
    : [];
  const full = works.length >= DEBATE_RULES.worksMax;
  const addWork = (work: DebateWorkOption) => {
    setWorks((current) => current.some((item) => item.entityId === work.entityId) || current.length >= DEBATE_RULES.worksMax ? current : [...current, work]);
    setWorkText("");
  };
  const removeWork = (entityId: number) => setWorks((current) => current.filter((work) => work.entityId !== entityId));

  const sameSides = sideA.trim() !== "" && sideA.trim().toLowerCase() === sideB.trim().toLowerCase();
  const ready = works.length > 0 && title.trim() !== "" && sideA.trim() !== "" && sideB.trim() !== "" && !sameSides && !submitting;
  const empty = works.length === 0 && !title.trim() && !description.trim() && !sideA.trim() && !sideB.trim();

  const saveDraft = () => {
    setConfirm(null);
    const saved = draftStore(userId).write({ works, title, description, sideA, sideB });
    showToast(saved ? "임시 저장했어요. 나중에 이어서 작성할 수 있어요." : "이 브라우저에서는 임시 저장을 할 수 없어요.", saved ? "done" : "info");
  };
  const submit = async () => {
    setConfirm(null);
    if (!ready) return;
    setSubmitting(true);
    setError(null);
    try {
      await createDebate({ title, description, sideA, sideB, workIds: works.map((work) => work.entityId) });
      draftStore(userId).clear();
      router.push("/pivot/debate/activity?tab=mine&created=1");
    } catch (reason) {
      setError(errorText(reason));
      setSubmitting(false);
    }
  };
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (ready) setConfirm("submit");
  };
  const closeConfirm = useCallback(() => setConfirm(null), []);
  const closeGuide = useCallback(() => setGuideOpen(false), []);

  return <DebateScreen label="토론 주제 올리기">
    <DebateHomeHeader title="토론 주제 올리기" onGuide={() => setGuideOpen(true)} back={() => router.push("/pivot/debate/activity?tab=mine")} />
    <form className={styles.createForm} onSubmit={onSubmit} noValidate>
      <div className={styles.field}>
        <label className={styles.label} htmlFor={`${ids}-work`}>애니메이션 이름 <span className={styles.required} aria-hidden="true">*</span></label>
        <div className={styles.workSearch}>
          <input id={`${ids}-work`} className={styles.input} value={workText} onChange={(event) => setWorkText(event.target.value)}
            placeholder={full ? `작품은 최대 ${DEBATE_RULES.worksMax}개까지 고를 수 있어요` : "관련 애니메이션 이름을 입력해주세요"} disabled={full}
            autoComplete="off" role="combobox" aria-expanded={options.length > 0} aria-controls={`${ids}-works`} aria-required="true"
            onKeyDown={(event) => {
              // 한글 입력을 끝내는 Enter 는 고르기가 아니다. 후보가 있으면 첫 번째를 고른다.
              if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); if (options[0]) addWork(options[0]); }
            }} />
          {options.length > 0 ? <ul id={`${ids}-works`} className={styles.workOptions} role="listbox" aria-label="작품 후보">
            {options.map((option) => <li key={option.entityId} role="option" aria-selected={false}>
              <button type="button" onClick={() => addWork(option)}>{option.name}</button>
            </li>)}
          </ul> : null}
        </div>
        {works.length > 0 ? <ul className={styles.pickedWorks} aria-label="고른 작품">
          {works.map((work) => <li key={work.entityId}>{work.name}
            <button type="button" aria-label={`${work.name} 빼기`} onClick={() => removeWork(work.entityId)}><CloseIcon size={16} /></button>
          </li>)}
        </ul> : null}
        {typed && workOptions.text === typed && options.length === 0 && !full
          ? <p className={styles.fieldHelp}>목록에 맞는 작품이 없어요. 다른 이름으로 찾아보세요.</p>
          : <p className={styles.fieldHelp}>{DEBATE_RULES.worksMax}개까지 고를 수 있어요 ({works.length}/{DEBATE_RULES.worksMax})</p>}
      </div>

      <div className={styles.field}>
        <label className={styles.label} htmlFor={`${ids}-title`}>주제 제목 <span className={styles.required} aria-hidden="true">*</span></label>
        <p className={styles.fieldHelp} id={`${ids}-title-help`}>주제의 배경, 비교 포인트, 토론하고 싶은 관점을 간단히 적어주세요</p>
        <input id={`${ids}-title`} className={styles.input} value={title} maxLength={DEBATE_RULES.titleMax} aria-required="true"
          aria-describedby={`${ids}-title-help ${ids}-title-example`}
          onChange={(event) => setTitle(event.target.value)} placeholder={`토론 주제를 입력해주세요 (최대 ${DEBATE_RULES.titleMax}자)`} />
        <p className={styles.fieldHelp} id={`${ids}-title-example`}>ex) 나루토와 사스케, 최종 승자는?</p>
      </div>

      <div className={styles.field}>
        <label className={styles.label} htmlFor={`${ids}-description`}>부가 설명 <small>(선택)</small></label>
        <p className={styles.fieldHelp}>주제에 대한 간단한 설명을 입력해주세요 (최대 {DEBATE_RULES.descriptionMax}자)</p>
        <div className={styles.textareaBox}>
          <textarea id={`${ids}-description`} className={styles.textarea} value={description} maxLength={DEBATE_RULES.descriptionMax}
            onChange={(event) => setDescription(event.target.value)} placeholder={`주제에 대한 간단한 설명을 입력해주세요 (최대 ${DEBATE_RULES.descriptionMax}자)`} />
          <span className={styles.counter} aria-hidden="true">{description.length} / {DEBATE_RULES.descriptionMax}</span>
        </div>
      </div>

      <fieldset className={styles.field}>
        <legend className={styles.label}>투표 선택지 <span className={styles.required} aria-hidden="true">*</span></legend>
        {([["a", sideA, setSideA], ["b", sideB, setSideB]] as const).map(([side, value, setValue], index) =>
          <div key={side} className={styles.choiceRow}>
            <label className={styles.choiceTag} htmlFor={`${ids}-side-${side}`}>선택지 {index + 1}</label>
            <div className={styles.choiceInput}>
              <input id={`${ids}-side-${side}`} value={value} maxLength={DEBATE_RULES.sideLabelMax} onChange={(event) => setValue(event.target.value)}
                placeholder="후보" aria-required="true" />
              {value ? <button type="button" aria-label={`선택지 ${index + 1} 지우기`} onClick={() => setValue("")}><CloseIcon size={18} /></button> : null}
            </div>
          </div>)}
        {sameSides ? <p className={styles.fieldError}>두 선택지는 서로 달라야 해요.</p> : null}
      </fieldset>

      {error ? <p className={styles.fieldError} role="alert">{error}</p> : null}
      <div className={styles.createButtons}>
        <button type="button" className={styles.draftButton} disabled={empty} onClick={() => setConfirm("draft")}>임시 저장</button>
        <button type="submit" className={styles.submitButton} disabled={!ready}>{submitting ? "올리는 중…" : "주제 올리기"}</button>
      </div>
    </form>
    {confirm === "draft" ? <ConfirmDialog title="임시 저장할까요?" body={"지금까지 작성한 내용이 저장돼요.\n나중에 이어서 작성할 수 있어요."}
      confirmLabel="저장하기" onConfirm={saveDraft} onCancel={closeConfirm} /> : null}
    {confirm === "submit" ? <ConfirmDialog title="주제를 올릴까요?"
      body={`주제를 올리면 ${DEBATE_RULES.votingDays}일 동안 투표를 받아요.\n${DEBATE_RULES.voteThreshold}표를 달성하면 채팅방이 열려요.\n등록 후에는 수정할 수 없어요.`}
      confirmLabel="올리기" onConfirm={() => void submit()} onCancel={closeConfirm} /> : null}
    {guideOpen ? <DebateGuide onClose={closeGuide} /> : null}
    {toast}
  </DebateScreen>;
}
