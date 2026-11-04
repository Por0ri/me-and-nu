"use client";
// My Page taste edit (2026-11 design): what the member reacted to lately, above each question, and the
// questions in the edit's own words. Shown only when the hints loaded with enough reacted posts.
import { useState } from "react";
import { MAX_PREFERENCE_TAGS_PER_KIND } from "@/lib/pivot/onboarding";
import type { TasteHints } from "@/lib/pivot/taste-edit";
import type { QuestionCopy } from "./onboarding-catalog-screens";
import styles from "./taste-hints.module.css";

type Step = "q1" | "q2" | "q3";

// Short names of the Q3 answers in the hint chips, as in the design.
const TYPE_LABELS: Record<string, string> = { world: "설정", relationships: "캐릭터", ost: "OST", goods: "굿즈", events: "팝업", news: "새 소식" };
const FIRST_TYPES = 3;
const FOUND = "최근 본 콘텐츠에서 이런 취향을 발견했어요!";

export function tasteHintShown(hints: TasteHints | null | undefined, step: Step): boolean {
  if (!hints?.enough) return false;
  return step === "q1" ? hints.works.length > 0 : step === "q2" ? hints.genres.length + hints.themes.length > 0 : hints.types.length > 0;
}

export function TasteHintBox({ hints, step }: { hints: TasteHints | null | undefined; step: Step }) {
  const [open, setOpen] = useState(false);
  if (!hints || !tasteHintShown(hints, step)) return null;
  if (step === "q1") return <section className={styles.hint} aria-label="최근 본 콘텐츠로 찾은 작품 취향">
    <p className={styles.title}>{FOUND}</p>
    <ul className={styles.box}>{hints.works.map(work => <li key={work.entityId} className={styles.chip}>{work.name} {work.percent}%</li>)}</ul>
  </section>;
  if (step === "q2") return <section className={styles.hint} aria-label="최근 본 콘텐츠로 찾은 장르·소재 취향">
    <p className={styles.title}>{FOUND}</p>
    <dl className={styles.box}>
      {([["장르", hints.genres], ["소재", hints.themes]] as const).filter(([, tags]) => tags.length > 0).map(([label, tags]) => <div key={label} className={styles.row}>
        <dt>{label}</dt>
        <dd><ul>{tags.map(tag => <li key={tag.tagId} className={styles.chip}>{tag.name}</li>)}</ul></dd>
      </div>)}
    </dl>
  </section>;
  const shown = open ? hints.types : hints.types.slice(0, FIRST_TYPES);
  return <section className={styles.hint} aria-label="최근 내가 많이 본 콘텐츠 유형">
    <p className={styles.title}>최근 내가 많이 본 콘텐츠 유형이에요!
      <button type="button" className={styles.more} aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "접기" : "더보기"}</button>
    </p>
    <ul className={styles.box}>{shown.map(type => <li key={type.answer} className={styles.chip}>{TYPE_LABELS[type.answer]} {type.percent}%</li>)}</ul>
  </section>;
}

/** Q1~Q3 of the taste edit: its own headings, the hint box, and a pointer to the hints only when they show. */
export function tasteQuestionCopy(hints: TasteHints | null | undefined): Record<Step, QuestionCopy> {
  return {
    q1: { heading: "요즘 가장 관심 있는 작품은 무엇인가요?", description: "좋아하는 작품을 1개 이상 5개 이하 선택해 주세요",
      hint: <TasteHintBox hints={hints} step="q1" /> },
    q2: { heading: "어떤 장르와 소재를 좋아하나요?",
      description: <>장르·소재 각각 최소 1개, 최대 {MAX_PREFERENCE_TAGS_PER_KIND}개 선택해 주세요
        {tasteHintShown(hints, "q2") && <><br />위 분석결과를 참고하여 선택해도 좋고,<br />추가로 선택해도 좋아요</>}</>,
      hint: <TasteHintBox hints={hints} step="q2" /> },
    q3: { heading: "어떤 콘텐츠를 더 보고싶나요?",
      description: tasteHintShown(hints, "q3") ? "이전 열람 성향과 다른 답변을 선택해도 괜찮아요" : "가장 보고 싶은 것을 하나만 선택해 주세요",
      hint: <TasteHintBox hints={hints} step="q3" /> },
  };
}
