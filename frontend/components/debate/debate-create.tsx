"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { createDebate, searchWorks, similarDebates } from "@/lib/debate/client";
import { DEBATE_RULES } from "@/lib/debate/rules";
import type { DebateSummary, DebateWorkOption } from "@/lib/debate/types";
import { DebateHeader, DebateScreen, errorText } from "./debate-chrome";
import { BoltIcon, CalendarIcon, ChatIcon, ChevronRightIcon, SearchIcon, SparkleIcon } from "./debate-icons";
import { InfoNote, StatusBadge } from "./debate-parts";
import styles from "./debate.module.css";

const FORM_ID = "debate-create-form";

export function DebateCreate() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [sideA, setSideA] = useState("");
  const [sideB, setSideB] = useState("");
  const [works, setWorks] = useState<DebateWorkOption[]>([]);
  const [workText, setWorkText] = useState("");
  const [workOptions, setWorkOptions] = useState<DebateWorkOption[]>([]);
  const [similar, setSimilar] = useState<DebateSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => { searchWorks(workText).then((rows) => { if (alive) setWorkOptions(rows); }, () => undefined); }, 200);
    return () => { alive = false; clearTimeout(timer); };
  }, [workText]);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => { similarDebates(title).then((rows) => { if (alive) setSimilar(rows); }, () => undefined); }, 350);
    return () => { alive = false; clearTimeout(timer); };
  }, [title]);

  const sameSides = sideA.trim() !== "" && sideA.trim().toLowerCase() === sideB.trim().toLowerCase();
  const ready = title.trim() !== "" && sideA.trim() !== "" && sideB.trim() !== "" && !sameSides && !submitting;
  const toggleWork = (work: DebateWorkOption) => {
    setWorks((current) => current.some((item) => item.entityId === work.entityId)
      ? current.filter((item) => item.entityId !== work.entityId)
      : current.length >= DEBATE_RULES.worksMax ? current : [...current, work]);
  };
  // 고른 작품은 검색 결과에 없어도 계속 보이게 앞에 둔다.
  const shownWorks = [...works, ...workOptions.filter((option) => !works.some((work) => work.entityId === option.entityId))];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    setError(null);
    try {
      await createDebate({ title, description, sideA, sideB, workIds: works.map((work) => work.entityId) });
      router.push("/pivot/debate/activity?tab=mine&created=1");
    } catch (reason) {
      setError(errorText(reason));
      setSubmitting(false);
    }
  };

  return <DebateScreen composer label="토론 주제 올리기">
    <DebateHeader title="토론 주제 올리기" back="/pivot/debate?tab=vote" />
    <form id={FORM_ID} className={styles.stack} onSubmit={(event) => void submit(event)} noValidate>
      <InfoNote><strong>1인당 투표 중인 주제 1개만 등록 가능</strong>내 주제가 열리거나 소각되면 다시 등록할 수 있어요.</InfoNote>
      <div className={styles.infoRow}>
        <span className={styles.infoChip}><CalendarIcon size={16} />등록 후 {DEBATE_RULES.votingDays}일간 투표</span>
        <span className={styles.infoChip}><BoltIcon size={16} />10표 미달 시 자동 소각</span>
      </div>

      <div className={styles.field}>
        <label className={styles.label} htmlFor="debate-title">주제 제목 <span className={styles.required}>*</span></label>
        <input id="debate-title" className={styles.input} value={title} maxLength={DEBATE_RULES.titleMax}
          onChange={(event) => setTitle(event.target.value)} placeholder={`토론 주제를 입력해주세요 (최대 ${DEBATE_RULES.titleMax}자)`} />
        <span className={styles.counter}>{title.length} / {DEBATE_RULES.titleMax}</span>
      </div>

      <div className={styles.field}>
        <label className={styles.label} htmlFor="debate-description">간단한 설명 <small>(선택)</small></label>
        <textarea id="debate-description" className={styles.textarea} value={description} maxLength={DEBATE_RULES.descriptionMax}
          onChange={(event) => setDescription(event.target.value)} placeholder={`주제에 대한 간단한 설명을 입력해주세요 (최대 ${DEBATE_RULES.descriptionMax}자)`} />
        <span className={styles.counter}>{description.length} / {DEBATE_RULES.descriptionMax}</span>
      </div>

      {/* 시안에 없는 칸: 토론방에서 고를 두 편의 이름 (설계 9장) */}
      <fieldset className={styles.field} style={{ border: 0, margin: 0, padding: 0 }}>
        <legend className={styles.label} style={{ marginBottom: 8 }}>양쪽 선택지 <span className={styles.required}>*</span></legend>
        <div className={styles.sideInputs}>
          <input className={styles.input} value={sideA} maxLength={DEBATE_RULES.sideLabelMax} onChange={(event) => setSideA(event.target.value)}
            placeholder="예: 나루토" aria-label="A 선택지" />
          <span className={styles.versus} aria-hidden="true">vs</span>
          <input className={styles.input} value={sideB} maxLength={DEBATE_RULES.sideLabelMax} onChange={(event) => setSideB(event.target.value)}
            placeholder="예: 사스케" aria-label="B 선택지" />
        </div>
        {sameSides ? <p className={styles.fieldError}>양쪽 이름이 달라야 해요.</p>
          : <p className={styles.fieldHelp}>토론방에서 사람들이 고를 두 편이에요. &lsquo;된다 / 못 된다&rsquo;처럼 적어도 돼요.</p>}
      </fieldset>

      <div className={styles.field}>
        <p className={styles.label}>관련 작품 <small>(선택, 최대 {DEBATE_RULES.worksMax}개 · {works.length}/{DEBATE_RULES.worksMax})</small></p>
        <label className={styles.searchBox}>
          <SearchIcon size={18} /><span className={styles.srOnly}>작품 찾기</span>
          <input value={workText} onChange={(event) => setWorkText(event.target.value)} placeholder="작품 이름으로 찾기" />
        </label>
        <div className={styles.chips}>
          {shownWorks.map((work) => {
            const picked = works.some((item) => item.entityId === work.entityId);
            return <button key={work.entityId} type="button" className={styles.workPick} aria-pressed={picked}
              disabled={!picked && works.length >= DEBATE_RULES.worksMax} onClick={() => toggleWork(work)}>#{work.name}</button>;
          })}
        </div>
        {shownWorks.length === 0 ? <p className={styles.fieldHelp}>{workText.trim() ? "맞는 작품이 없어요." : "작품 이름을 입력하면 목록에서 찾아요."}</p> : null}
        <p className={styles.fieldHelp}>작품을 고르면 검색에서 작품으로 찾을 수 있어요. 목록에 없는 작품이면 고르지 않고 올려도 돼요.</p>
      </div>

      {similar.length > 0 ? <section className={styles.similar} aria-label="비슷한 주제">
        <p className={styles.similarHead}><SparkleIcon size={16} />비슷한 주제가 이미 있어요</p>
        {similar.map((debate) => <Link key={debate.debateId} className={styles.similarItem} href={`/pivot/debate/${debate.debateId}`}>
          <ChatIcon size={18} />
          <span style={{ flex: 1, minWidth: 0 }}>{debate.title}</span>
          <StatusBadge status={debate.status} />
          <ChevronRightIcon size={16} />
        </Link>)}
      </section> : null}

      {error ? <p className={styles.fieldError} role="alert">{error}</p> : null}
    </form>
    <div className={styles.submitBar}>
      <button type="submit" form={FORM_ID} className={styles.primaryButton} disabled={!ready}>{submitting ? "올리는 중…" : "주제 올리기"}</button>
    </div>
  </DebateScreen>;
}
