"use client";
// My Page taste edit (2026-11 design): what the member reacted to lately, under each question, and the
// questions in the edit's own words. Shown only when the hints loaded with enough reacted posts.
import { useId, useState, type ReactNode } from "react";
import { MAX_PREFERENCE_TAGS_PER_KIND } from "@/lib/pivot/onboarding";
import type { TasteHints } from "@/lib/pivot/taste-edit";
import type { QuestionCopy } from "./onboarding-catalog-screens";
import styles from "./taste-hints.module.css";

type Step = "q1" | "q2" | "q3";

// Short names of the Q3 answers in the hint chips, as in the design.
const TYPE_LABELS: Record<string, string> = { world: "설정", relationships: "캐릭터", ost: "OST", goods: "굿즈", events: "팝업", news: "새 소식" };
// [folded title, open title] — 2026-11 feedback mock: a hint starts folded under the question.
const TITLES: Record<Step, readonly [string, string]> = {
  q1: ["최근 본 콘텐츠에서 선호 작품 알아보기!", "최근 본 콘텐츠에서 선호 작품을 발견했어요!"],
  q2: ["최근 본 콘텐츠에서 발견한 취향 알아보기!", "최근 본 콘텐츠에서 이런 취향을 발견했어요!"],
  q3: ["최근 내가 많이 본 콘텐츠 유형 알아보기", "최근 내가 많이 본 콘텐츠 유형이에요!"],
};

/** A hint under a question that starts folded: one line with '펼쳐 보기', and the box only when opened
 * ('접기'), so a question with its choices fits one screen (2026-11 feedback). Also used by signup Q2. */
export function HintToggle({ label, titles, children }: { label: string; titles: readonly [string, string]; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <section className={styles.hint} aria-label={label}>
    <p className={styles.title}>{open ? titles[1] : titles[0]}
      <button type="button" className={styles.more} aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}>{open ? "접기" : "펼쳐 보기"}</button>
    </p>
    <div id={id} hidden={!open}>{children}</div>
  </section>;
}

export function tasteHintShown(hints: TasteHints | null | undefined, step: Step): boolean {
  if (!hints?.enough) return false;
  return step === "q1" ? hints.works.length > 0 : step === "q2" ? hints.genres.length + hints.themes.length > 0 : hints.types.some(type => type.percent > 0);
}

export function TasteHintBox({ hints, step }: { hints: TasteHints | null | undefined; step: Step }) {
  if (!hints || !tasteHintShown(hints, step)) return null;
  if (step === "q1") return <HintToggle label="최근 본 콘텐츠로 찾은 작품 취향" titles={TITLES.q1}>
    <ul className={styles.box}>{hints.works.map(work => <li key={work.entityId} className={styles.chip}>{work.name} {work.percent}%</li>)}</ul>
  </HintToggle>;
  if (step === "q2") return <HintToggle label="최근 본 콘텐츠로 찾은 장르·소재 취향" titles={TITLES.q2}>
    <dl className={styles.box}>
      {([["장르", hints.genres], ["소재", hints.themes]] as const).filter(([, tags]) => tags.length > 0).map(([label, tags]) => <div key={label} className={styles.row}>
        <dt>{label}</dt>
        <dd><ul>{tags.map(tag => <li key={tag.tagId} className={styles.chip}>{tag.name}</li>)}</ul></dd>
      </div>)}
    </dl>
  </HintToggle>;
  // Open shows every type the member saw (the mock lists six in two lines); 0% says nothing, so it is left out.
  return <HintToggle label="최근 내가 많이 본 콘텐츠 유형" titles={TITLES.q3}>
    <ul className={styles.box}>{hints.types.filter(type => type.percent > 0).map(type => <li key={type.answer} className={styles.chip}>{TYPE_LABELS[type.answer]} {type.percent}%</li>)}</ul>
  </HintToggle>;
}

/** Q1~Q3 of the taste edit: the signup questions (2026-11 feedback mock) with the folded hint under them. */
export function tasteQuestionCopy(hints: TasteHints | null | undefined): Record<Step, QuestionCopy> {
  return {
    q1: { heading: "요즘 가장 관심 있는 작품은 무엇인가요?", description: "좋아하는 작품을 1개 이상 5개 이하 선택해 주세요",
      hint: <TasteHintBox hints={hints} step="q1" /> },
    q2: { heading: "어떤 장르와 소재를 좋아하나요?",
      description: <>장르·소재 각각 최소 1개, 최대 {MAX_PREFERENCE_TAGS_PER_KIND}개 선택해 주세요
        {tasteHintShown(hints, "q2") && <><br />아래 분석결과를 참고하여 선택해도 좋고,<br />추가로 선택해도 좋아요</>}</>,
      hint: <TasteHintBox hints={hints} step="q2" /> },
    // Same heading and description as signup Q3 (the screen's defaults), only the hint added.
    q3: { hint: <TasteHintBox hints={hints} step="q3" /> },
  };
}
