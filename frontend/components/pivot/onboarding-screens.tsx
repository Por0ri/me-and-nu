"use client";
import { Fragment, useEffect, useRef, useState, type Dispatch, type ImgHTMLAttributes } from "react";
import assetManifest from "@/public/pivot/bc-sources.json";
import { q1Choices, q2Choices, canAdvance, isSelectionComplete, selectionSummary, type OnboardingAction, type OnboardingState } from "@/lib/pivot/onboarding";
import { PivotFrame, PrimaryButton } from "./pivot-ui";
import { useOnboardingNavigation } from "./use-onboarding-navigation";
import styles from "./onboarding.module.css";

function Asset({ screen, variable, ...props }: { screen: string; variable: string } & ImgHTMLAttributes<HTMLImageElement>) {
  const asset = assetManifest.assets.find((item) => item.screen === screen && item.variable === variable);
  if (!asset) throw new Error("Missing local Figma asset: " + screen + "/" + variable);
  return <img src={"/pivot/" + asset.file} alt="" aria-hidden="true" {...props} />;
}
function Back({ onBack, busy }: { onBack: () => void; busy: boolean }) {
  return <button type="button" className={styles.back} aria-label="이전 단계" onClick={onBack} disabled={busy}>
    <Asset screen="q1" variable="imgChevronBackward" width={24} height={24} />
  </button>;
}
function Progress({ step, onBack, busy }: { step: 1 | 2; onBack: () => void; busy: boolean }) {
  return <nav className={styles.navigation} aria-label="온보딩 화면 탐색">
    <Back onBack={onBack} busy={busy} />
    <div className={styles.progress} role="progressbar" aria-label="기본 질문 진행 상태" aria-valuemin={0} aria-valuemax={2} aria-valuenow={step}>
      <i /><i className={step === 2 ? styles.filled : ""} />
    </div><span aria-hidden="true">{step}/2</span>
  </nav>;
}
type NavigationProps = { onBack: () => void; onNext: () => void; busy: boolean };
export function InterestScreen({ selected, onToggle, onBack, onNext, busy, chrome = false }: NavigationProps & { selected: boolean; onToggle: () => void; chrome?: boolean }) {
  const assetScreen = selected ? "interest-on" : "interest";
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <header className={styles.interestHeader}>
      <Back onBack={onBack} busy={busy} />
      <Asset screen={assetScreen} variable="img4Vectorized" width={104} height={36} alt="me;nu" aria-hidden={undefined} />
    </header>
    <section className={styles.interestBody} aria-labelledby="interest-heading">
      <h1 id="interest-heading" tabIndex={-1}>관심 분야를<br /><em>선택</em>해주세요</h1>
      <p className={styles.interestDescription}>지금은 애니메이션부터 시작할 수 있어요</p>
      <div className={styles.interestCards}>
        <label className={[styles.interestCard, selected ? styles.selected : ""].join(" ")}>
          <input type="checkbox" aria-label="애니메이션" checked={selected} onChange={onToggle} />
          <span className={styles.interestArtwork}><Asset screen={assetScreen} variable={selected ? "img" : "img1"} width={108} height={108} /></span>
          <strong>애니메이션</strong><span className={styles.badge}>{selected ? "선택 완료" : "선택 가능"}</span>
        </label>
        <div className={styles.upcoming} aria-label="추가 예정 분야">
          <Asset screen={assetScreen} variable={selected ? "img1" : "img2"} width={52} height={52} />
          <strong>추가 예정</strong>
        </div>
      </div>
      <div className={styles.hint}><Asset screen={assetScreen} variable={selected ? "img2" : "img"} width={32} height={32} /><p>분야를 선택한 뒤 취향을 고를 수 있어요</p></div>
    </section>
    <div className={styles.actions}><PrimaryButton disabled={!selected || busy} onClick={onNext}>애니메이션 선택하고 시작하기</PrimaryButton></div>
  </PivotFrame>;
}
export function QuestionScreen({ question, value, onChoose, onBack, onNext, busy, chrome = false }: NavigationProps & { question: 1 | 2; value: string | null; onChoose: (value: string) => void; chrome?: boolean }) {
  const screen = "q" + question + (value ? "-on" : "");
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <Progress step={question} onBack={onBack} busy={busy} />
    <section className={styles.questionBody} aria-labelledby="question-heading">
      <h1 id="question-heading" tabIndex={-1}><span>Q{question}.</span>
        {question === 1 ? "덕질할 때 주로 무엇을 하나요?" : <>가장 놓치고 싶지 않은 정보는<br />무엇인가요?</>}
      </h1>
      <p className={styles.questionDescription}>{question === 1 ? "가장 많이 하는 것을 하나만 선택해주세요" : "하나만 선택해주세요"}</p>
      <fieldset className={question === 1 ? styles.q1Grid : styles.q2List}>
        <legend className={styles.srOnly}>Q{question} 답변 하나 선택</legend>
        {question === 1 ? q1Choices.map((choice) => <label key={choice.id} className={[styles.choice, styles.q1Card, value === choice.id ? styles.selected : ""].join(" ")}>
          <input type="radio" name="q1" aria-label={choice.lines.join(" ")} value={choice.id} checked={value === choice.id} onChange={() => onChoose(choice.id)} />
          <Asset screen={screen} variable={choice.asset} width={76} height={66} />
          <span>{choice.lines.map((line, index) => <Fragment key={line}>{index > 0 && <br />}{line}</Fragment>)}</span>
        </label>) : q2Choices.map((choice) => <label key={choice.id} className={[styles.choice, styles.q2Card, value === choice.id ? styles.selected : ""].join(" ")}>
          <input type="radio" name="q2" aria-label={choice.label} value={choice.id} checked={value === choice.id} onChange={() => onChoose(choice.id)} />
          <Asset screen={screen} variable={choice.asset} width={44} height={44} /><span>{choice.label}</span>
        </label>)}
      </fieldset>
    </section>
    <div className={styles.actions}><PrimaryButton disabled={!value || busy} onClick={onNext}>선택 완료</PrimaryButton></div>
  </PivotFrame>;
}
export function ResultScreen({ state, onBack, busy, mode, onRestart, onDemoHome, chrome = false }: { state: OnboardingState; onBack: () => void; busy: boolean; mode: "demo" | "service" | "preview"; onRestart?: () => void; onDemoHome?: () => void; chrome?: boolean }) {
  const [homeNotice, setHomeNotice] = useState(false);
  const summary = selectionSummary(state);
  return <PivotFrame decorated={false} chrome={chrome} className={styles.frame}>
    <div className={styles.resultDecorations} aria-hidden="true">
      <Asset screen="result" variable="img" className={styles.sideDecoration} width={247} height={254} />
      <Asset screen="result" variable="img1" className={styles.topDecoration} width={380} height={380} />
    </div>
    <nav className={styles.resultNavigation} aria-label="결과 화면 탐색"><Back onBack={onBack} busy={busy} /></nav>
    <section className={styles.resultBody} aria-labelledby="result-heading">
      <p className={styles.resultMode}>{mode === "service" ? "선택한 취향" : "선택 답변 미리보기 · 피드 연결 대기"}</p>
      <h1 id="result-heading" tabIndex={-1}>취향에 맞는<br />피드가 준비됐어요!</h1>
      <div className={styles.resultMascot}><Asset screen="result" variable="imgImage24" /></div>
      <div className={styles.summary} aria-label="선택 답변 요약">
        <div className={styles.summaryIntro}><h2>초기 추천 기준</h2><p>선택해주신 2가지 답변을 바탕으로 콘텐츠를 추천할게요</p></div>
        <div className={styles.summaryRows}>
          <div className={styles.summaryRow}><Asset screen="result" variable="img2" width={29} height={29} /><span className={styles.summaryLabel}>주로 찾는 정보</span><strong className={styles.answerTag}>{summary.q1}</strong></div>
          <div className={styles.summaryRow}><Asset screen="result" variable="img3" width={29} height={29} /><span className={styles.summaryLabel}>놓치고 싶지 않은 소식</span><strong className={styles.answerTag}>{summary.q2}</strong></div>
        </div>
      </div>
      <p className={styles.connectionNotice}>{mode === "service" ? "선택한 취향은 아직 저장되지 않았어요." : <>{mode === "demo" ? "시연용 결과예요. " : ""}실제 추천 피드·가입·서버 저장은 아직 연결되지 않았어요.</>}</p>
    </section>
    <div className={styles.actions}>
      <PrimaryButton disabled={!isSelectionComplete(state) || busy} onClick={() => { if (mode === "demo" && onDemoHome) onDemoHome(); else setHomeNotice(true); }} aria-describedby="home-feedback">콘텐츠 보러가기</PrimaryButton>
      <p id="home-feedback" className={styles.homeFeedback} role="status" aria-live="polite">{homeNotice ? mode === "service" ? "콘텐츠 화면을 준비하고 있어요. 선택한 취향은 아직 저장되지 않았어요." : "새 Home 화면 연결을 기다리고 있어요. 가입이나 서버 저장은 진행되지 않았어요." : ""}</p>
      {onRestart && <button type="button" className={styles.restart} onClick={onRestart}>처음부터 시연</button>}
    </div>
  </PivotFrame>;
}
export function OnboardingFlow({ state, dispatch, onExit, mode, onRestart, onDemoHome, chrome = false }: { state: OnboardingState; dispatch: Dispatch<OnboardingAction>; onExit: () => void; mode: "demo" | "service" | "preview"; onRestart?: () => void; onDemoHome?: () => void; chrome?: boolean }) {
  const region = useRef<HTMLDivElement>(null);
  const navigation = useOnboardingNavigation(state, dispatch, onExit);
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
    region.current?.querySelector<HTMLHeadingElement>("h1")?.focus({ preventScroll: true });
  }, [state.step]);
  const shared = { onBack: navigation.back, onNext: navigation.advance, busy: navigation.busy, chrome };
  return <div ref={region} className={styles.flow}>
    {state.step === "interest" ? <InterestScreen {...shared} selected={state.interest === "animation"} onToggle={() => dispatch({ type: "toggle-interest" })} />
      : state.step === "q1" ? <QuestionScreen {...shared} question={1} value={state.q1} onChoose={(value) => dispatch({ type: "choose-q1", value })} />
      : state.step === "q2" ? <QuestionScreen {...shared} question={2} value={state.q2} onChoose={(value) => dispatch({ type: "choose-q2", value })} />
      : <ResultScreen state={state} onBack={navigation.back} busy={navigation.busy} mode={mode} onRestart={onRestart} onDemoHome={onDemoHome} chrome={chrome} />}
    <span className={styles.srOnly} role="status" aria-live="polite">{navigation.busy ? "다음 화면으로 이동 중" : canAdvance(state) ? "선택 완료. 다음 단계로 이동할 수 있어요." : ""}</span>
  </div>;
}
