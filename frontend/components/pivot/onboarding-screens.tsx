"use client";
import { Fragment, useEffect, useRef, useState, type Dispatch, type ImgHTMLAttributes } from "react";
import assetManifest from "@/public/pivot/bc-sources.json";
import { q1Choices, canAdvance, isStateComplete, selectionSummary, type OnboardingAction, type OnboardingState } from "@/lib/pivot/onboarding";
import { serviceCatalog, demoCatalog } from "@/lib/pivot/onboarding-catalog";
import { QuestionProgress, WorksQuestion, TagsQuestion } from "./onboarding-catalog-screens";
import { PivotFrame, PrimaryButton } from "./pivot-ui";
import { useOnboardingNavigation } from "./use-onboarding-navigation";
import styles from "./onboarding.module.css";

function Asset({ screen, variable, ...props }: { screen: string; variable: string } & ImgHTMLAttributes<HTMLImageElement>) {
  const asset = assetManifest.assets.find((item) => item.screen === screen && item.variable === variable);
  if (!asset) throw new Error("Missing local Figma asset: " + screen + "/" + variable);
  return <img src={"/pivot/" + asset.file} alt="" aria-hidden="true" {...props} />;
}
type NavigationProps = { onBack: () => void; onNext: () => void; busy: boolean };
type SubmissionProps = { submitting: boolean; error: string; onSubmit: () => void; onEditProfile: () => void; recovery?: { label: string; onClick: () => void } };
export function InterestScreen({ selected, onToggle, onNext, busy, chrome = false, onEditProfile }: Omit<NavigationProps, "onBack"> & { selected: boolean; onToggle: () => void; chrome?: boolean; onEditProfile?: () => void }) {
  const assetScreen = selected ? "interest-on" : "interest";
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <header className={styles.interestHeader}>
      <Asset screen={assetScreen} variable="img4Vectorized" width={104} height={20.7859} alt="me;nu" aria-hidden={undefined} />
    </header>
    <section className={styles.interestBody} aria-labelledby="interest-heading">
      <h1 id="interest-heading" tabIndex={-1}>관심 분야를<br /><em>선택</em>해주세요</h1>
      <p className={styles.interestDescription}>지금은 애니메이션부터 시작할 수 있어요</p>
      <div className={styles.interestCards}>
        <label className={[styles.interestCard, selected ? styles.selected : ""].join(" ")}>
          <input type="checkbox" aria-label="애니메이션" checked={selected} onChange={onToggle} disabled={busy} />
          <span className={styles.interestArtwork}><Asset screen={assetScreen} variable="img" width={115} height={104.11} /></span>
          <strong>애니메이션</strong><span className={styles.badge}>{selected ? "선택 완료" : "선택 가능"}</span>
        </label>
        <div className={styles.upcoming} aria-label="추가 예정 분야">
          <Asset screen={assetScreen} variable={selected ? "img1" : "img2"} width={52} height={52} />
          <strong>추가 예정</strong>
        </div>
      </div>
      <div className={styles.hint}><Asset screen={assetScreen} variable={selected ? "img2" : "img1"} width={32} height={32} /><p>분야를 선택한 뒤 취향을 고를 수 있어요</p></div>
    </section>
    <div className={styles.actions}>{onEditProfile && <button type="button" className={styles.reselect} onClick={onEditProfile} disabled={busy}>기본 정보 수정하기</button>}<PrimaryButton disabled={!selected || busy} onClick={onNext}>애니메이션 선택하고 시작하기</PrimaryButton></div>
  </PivotFrame>;
}
export function QuestionScreen({ value, onChoose, onBack, onNext, busy, chrome = false }: NavigationProps & { value: string | null; onChoose: (value: string) => void; chrome?: boolean }) {
  const screen = "q1" + (value ? "-on" : "");
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <QuestionProgress step={3} onBack={onBack} busy={busy} />
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q3.</span>어떻게 덕질하세요?</h1>
      <p className={styles.questionDescription}>가장 많이 하는 것을 하나만 선택해주세요.</p>
      <fieldset className={styles.q1Grid}>
        <legend className={styles.srOnly}>Q3 소비 유형 하나 선택</legend>
        {q1Choices.map((choice) => <label key={choice.id} className={[styles.choice, styles.q1Card, value === choice.id ? styles.selected : ""].join(" ")}>
          <input type="radio" name="consumptionType" aria-label={choice.lines.join(" ")} value={choice.id} checked={value === choice.id} onChange={() => onChoose(choice.id)} disabled={busy} />
          <Asset screen={screen} variable={choice.asset} width={76} height={66} />
          <span>{choice.lines.map((line, index) => <Fragment key={line}>{index > 0 && <br />}{line}</Fragment>)}</span>
        </label>)}
      </fieldset>
    </section>
    <div className={styles.actions}><PrimaryButton disabled={!value || busy} onClick={onNext}>선택 완료</PrimaryButton></div>
  </PivotFrame>;
}
export function ResultScreen({ state, onReselect, busy, mode, onRestart, onDemoHome, chrome = false, submission }: { state: OnboardingState; onReselect: () => void; busy: boolean; mode: "demo" | "service" | "preview"; onRestart?: () => void; onDemoHome?: () => void; chrome?: boolean; submission?: SubmissionProps }) {
  const [homeNotice, setHomeNotice] = useState(false);
  const summary = selectionSummary(state);
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <div className={styles.resultDecorations} aria-hidden="true">
      <Asset screen="result" variable="img" className={styles.sideDecoration} width={247} height={254} />
      <Asset screen="result" variable="img1" className={styles.topDecoration} width={380} height={380} />
    </div>
    <section className={styles.resultBody} aria-labelledby="result-heading">
      <p className={styles.resultMode}>{mode === "service" ? "선택한 취향" : "선택 답변 미리보기 · 피드 연결 대기"}</p>
      <h1 id="result-heading" tabIndex={-1}>{mode === "service" ? <>마지막으로<br />선택을 확인해주세요</> : <>취향에 맞는<br />피드가 준비됐어요!</>}</h1>
      <div className={styles.resultMascot}><Asset screen="result" variable="imgImage28" /></div>
      <div className={styles.summary} aria-label="선택 답변 요약">
        <div className={styles.summaryIntro}><h2>선택한 취향</h2><p>좋아하는 작품, 장르·소재, 소비 유형을 확인해주세요.</p></div>
        <div className={styles.summaryRows}>
          <div className={styles.summaryRow}><span className={styles.summaryLabel}>좋아하는 작품</span><strong className={styles.answerTag}>{summary.works}</strong></div>
          <div className={styles.summaryRow}><span className={styles.summaryLabel}>장르·소재</span><strong className={styles.answerTag}>{summary.tags}</strong></div>
          <div className={styles.summaryRow}><Asset screen="result" variable="img2" width={29} height={29} /><span className={styles.summaryLabel}>소비 유형</span><strong className={styles.answerTag}>{summary.consumption}</strong></div>
        </div>
      </div>
      <p className={styles.connectionNotice}>{mode === "service" ? "가입을 완료하면 선택한 취향을 저장하고 콘텐츠를 보여드릴게요." : <>{mode === "demo" ? "시연용 결과예요. " : ""}실제 추천 피드·가입·서버 저장은 아직 연결되지 않았어요.</>}</p>
    </section>
    <div className={styles.actions}>
      <button type="button" className={styles.reselect} onClick={onReselect} disabled={busy}>다시 선택하기</button>
      {mode === "service" && submission && <button type="button" className={styles.reselect} onClick={submission.onEditProfile} disabled={busy}>기본 정보 수정하기</button>}
      <PrimaryButton disabled={!isStateComplete(state) || busy || (mode === "service" && (!submission || !!submission.recovery))} onClick={() => {
        if (mode === "service") submission?.onSubmit();
        else if (mode === "demo" && onDemoHome) onDemoHome(); else setHomeNotice(true);
      }} aria-describedby="home-feedback">{mode === "service" ? submission?.submitting ? "가입을 완료하고 있어요…" : "가입 완료하고 콘텐츠 보기" : "콘텐츠 보러가기"}</PrimaryButton>
      <p id="home-feedback" className={styles.homeFeedback} role={submission?.error ? "alert" : "status"} aria-live="polite">{mode === "service" ? submission?.error || (submission?.submitting ? "가입 정보를 저장하고 있어요." : "") : homeNotice ? "새 Home 화면 연결을 기다리고 있어요. 가입이나 서버 저장은 진행되지 않았어요." : ""}</p>
      {mode === "service" && submission?.recovery && <button type="button" className={styles.restart} onClick={submission.recovery.onClick} disabled={busy}>{submission.recovery.label}</button>}
      {onRestart && <button type="button" className={styles.restart} onClick={onRestart}>처음부터 시연</button>}
    </div>
  </PivotFrame>;
}
export function OnboardingFlow({ state, dispatch, onExit, mode, onRestart, onDemoHome, chrome = false, submission }: { state: OnboardingState; dispatch: Dispatch<OnboardingAction>; onExit: () => void; mode: "demo" | "service" | "preview"; onRestart?: () => void; onDemoHome?: () => void; chrome?: boolean; submission?: SubmissionProps }) {
  const region = useRef<HTMLDivElement>(null);
  const navigation = useOnboardingNavigation(state, dispatch, onExit);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    region.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
  }, [state.step]);
  const busy = navigation.busy || (mode === "service" && submission?.submitting === true);
  const shared = { onBack: navigation.back, onNext: navigation.advance, busy, chrome };
  const catalogProps = { ...shared, state, dispatch, catalog: mode === "service" ? serviceCatalog : demoCatalog, demo: mode !== "service", message: submission?.error };
  return <div ref={region} className={styles.flow}>
    {state.step === "interest" ? <InterestScreen {...shared} selected={state.interest === "animation"} onToggle={() => dispatch({ type: "toggle-interest" })} onEditProfile={mode === "service" ? submission?.onEditProfile : undefined} />
      : state.step === "q1" ? <WorksQuestion {...catalogProps} />
      : state.step === "q2" ? <TagsQuestion {...catalogProps} />
      : state.step === "q3" ? <QuestionScreen {...shared} value={state.consumptionType} onChoose={(value) => dispatch({ type: "choose-consumption", value })} />
      : <ResultScreen state={state} onReselect={navigation.reselect} busy={busy} mode={mode} onRestart={onRestart} onDemoHome={onDemoHome} chrome={chrome} submission={submission} />}
    <span className={styles.srOnly} role="status" aria-live="polite">{navigation.busy ? "다음 화면으로 이동 중" : canAdvance(state) ? "선택 완료. 다음 단계로 이동할 수 있어요." : ""}</span>
  </div>;
}
