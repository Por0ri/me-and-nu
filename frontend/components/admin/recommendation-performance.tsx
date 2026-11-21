"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import { analyticsHref, type AnalyticsAudience, type AnalyticsQuery } from "@/lib/admin-analytics";
import type { AdminReadState } from "@/lib/admin-content";
import { loadRecommendationPerformance, type RecommendationPerformance as PerformanceData } from "@/lib/admin-statistics-api";
import { AdminStatus } from "./admin-status";
import { ReportPeriodFilter } from "./report-period-filter";
import { StatisticsPanel } from "./statistics-panels";
import { useAdminRead } from "./use-admin-read";
import shared from "./admin.module.css";
import statistics from "./statistics.module.css";
import styles from "./recommendation-performance.module.css";

type Props = { query: AnalyticsQuery; navigate: (query: AnalyticsQuery) => void };
type Cohort = "existing" | "new";
type Score = PerformanceData["models"][number]["scores"][number];
type ChartMetric = "ndcg" | "hitRate";
const audiences: Record<AnalyticsAudience, string> = { all: "기존·신규 함께", existing: "기존 사용자", new: "신규 사용자" };
const cohortLabel = { existing: "기존", new: "신규" };
const reasonLabels: Record<string, string> = {
  not_configured: "아직 평가 결과가 연결되지 않았습니다.",
  artifact_missing: "평가 결과가 준비되지 않았습니다.",
  artifact_invalid: "평가 결과를 확인할 수 없습니다.",
  artifact_unreadable: "평가 결과를 불러올 수 없습니다.",
  outside_period: "선택한 기간에 완료된 평가 결과가 없습니다.",
  audience_not_evaluated: "선택한 사용자 범위의 평가 결과가 없습니다.",
};
const cohortsFor = (audience: AnalyticsAudience): Cohort[] => audience === "all" ? ["existing", "new"] : [audience];
const integer = (value: number) => value.toLocaleString("ko-KR");
const ndcg = (value: number) => value.toFixed(4);
const hitRate = (value: number) => `${(value * 100).toFixed(1)}%`;
const delta = (value: number) => `${value > 0 ? "+" : ""}${value.toFixed(4)}`;
const percentagePointDelta = (value: number) => `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%p`;
const date = (value: string) => new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
}).format(new Date(value));

export function RecommendationPerformance({ query, navigate }: Props) {
  const read = useCallback((signal: AbortSignal) => loadRecommendationPerformance({ period: query.period, audience: query.audience }, signal), [query.period, query.audience]);
  const { state, retry } = useAdminRead(`performance:${query.period}:${query.audience}`, read);
  return <RecommendationPerformanceView query={query} navigate={navigate} state={state} retry={retry} />;
}

export function RecommendationPerformanceView({ query, navigate, state, retry }: Props & {
  state: AdminReadState<PerformanceData>; retry: () => void;
}) {
  const [metric, setMetric] = useState<ChartMetric>("ndcg");
  const response = state.status === "ready" ? state.data : null;
  const data = response?.availability === "available" ? response : null;
  const shownCohorts = cohortsFor(query.audience);
  const current = data?.models.find(model => model.modelId === data.currentModelId);
  const baseline = data?.models.find(model => model.modelId === data.baselineModelId);
  const simulated = data?.conditions?.cohortInterpretation === "input_scenarios";
  return <div className={styles.performance} data-analytics-view="performance" data-performance-state={state.status}>
    <Link href={analyticsHref({ ...query, view: "overview" })} className={statistics.viewLink}><img src="/admin/stat-back.svg" alt="" />데이터 분석</Link>
    <div className={`${shared.heading} ${styles.heading}`}>
      <div><h1>추천 성능</h1><p>작품 추천 · 애니메이션 · 오프라인 평가</p></div>
      <div className={styles.filters}>
        <ReportPeriodFilter value={query.period} onChange={period => navigate({ ...query, period })} />
        <label className={styles.audience}><span className={shared.srOnly}>추천 성능 사용자 범위</span>
          <select value={query.audience} onChange={event => navigate({ ...query, audience: event.target.value as AnalyticsAudience })}>
            {Object.entries(audiences).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select><img src="/admin/sort-down.svg" alt="" />
        </label>
      </div>
    </div>
    {state.status !== "ready" ? <AdminStatus subject="추천 성능" state={state} retry={retry} /> : <>
      <p className={styles.periodNote}>평가 완료 기간 · {date(state.data.periodStart)} ~ {date(state.data.periodEnd)} KST · 종료 시각 제외</p>
      {!data ? <section className={styles.empty} role="status" data-performance-unavailable>
        <h2>평가 수치 미집계</h2><p>{reasonLabels[state.data.reason ?? ""] ?? "평가 결과를 확인할 수 없습니다."}</p>
        <p>—는 0점이 아닌 확인할 수 없는 값입니다.</p>
        <button className={shared.secondaryButton} type="button" onClick={retry}>다시 확인</button>
      </section> : <>
        <div className={styles.context}>
          <p><strong>평가 기준 모델 {current?.label}</strong> · {data.evaluatedAt && date(data.evaluatedAt)} KST</p>
          <p>이 결과는 오프라인 평가이며 현재 서비스에 배포된 모델의 성능을 보증하지 않습니다.</p>
          <p>{simulated ? "기존·신규는 같은 표본에 전체 시청 기록 또는 온보딩 입력을 준 비교 장면입니다. 실제 가입 집단으로 해석하거나 두 표본을 합산하지 않습니다." : "기존·신규의 집단 기준은 아래 평가 조건을 확인하세요. 집단별 점수와 표본을 따로 표시합니다."}</p>
        </div>
        <div className={styles.metrics}>
          <ScoreSummary title="시리즈 NDCG@10" cohorts={shownCohorts} scores={current?.scores ?? []} format={score => ndcg(score.ndcgAt10)} highlight />
          <ScoreSummary title="시리즈 Hit Rate@10" cohorts={shownCohorts} scores={current?.scores ?? []} format={score => hitRate(score.hitRateAt10)} />
          <ScoreSummary title="평가 표본" cohorts={shownCohorts} scores={current?.scores ?? []} format={score => `${integer(score.sampleSize)}건`} />
        </div>
        <div className={styles.grid}>
          <StatisticsPanel title={`모델별 ${metric === "ndcg" ? "NDCG@10" : "Hit Rate@10"} 비교`} description="같은 평가 조건과 표본으로 비교합니다." className={styles.comparison} availability="available">
            <div className={styles.chartTools}>
              <div className={styles.metricSwitch} role="group" aria-label="비교할 추천 성능 지표">
                <button type="button" aria-pressed={metric === "ndcg"} onClick={() => setMetric("ndcg")}>NDCG@10</button>
                <button type="button" aria-pressed={metric === "hitRate"} onClick={() => setMetric("hitRate")}>Hit Rate@10</button>
              </div>
              <div className={styles.legend} aria-label="비교 계열">{shownCohorts.map(cohort => <span key={cohort}><i className={styles[cohort]} />{cohortLabel[cohort]}</span>)}</div>
            </div>
            <div className={styles.axis} aria-hidden="true"><span>0</span><span>{metric === "ndcg" ? "0.5" : "50%"}</span><span>{metric === "ndcg" ? "1.0" : "100%"}</span></div>
            <div className={styles.chart} data-performance-chart={metric} role="group" aria-label={`모델별 ${metric === "ndcg" ? "NDCG@10" : "Hit Rate@10"} 막대 그래프`}>
              {data.models.map(model => <div key={model.modelId} className={styles.model} data-model={model.modelId}>
                <div className={styles.modelName}><strong>{model.label}</strong>{model.modelId === data.currentModelId && <span>평가 기준</span>}</div>
                {shownCohorts.map(cohort => {
                  const score = model.scores.find(item => item.audience === cohort);
                  const value = score && (metric === "ndcg" ? score.ndcgAt10 : score.hitRateAt10);
                  const formatted = score ? (metric === "ndcg" ? ndcg(score.ndcgAt10) : hitRate(score.hitRateAt10)) : "—";
                  return <div key={cohort} className={styles.barRow}>
                    <span>{cohortLabel[cohort]}</span>
                    <div className={styles.track} data-model-id={model.modelId} data-audience={cohort} data-value={value} role="img" aria-label={`${model.label} ${cohortLabel[cohort]}: ${score ? formatted : "평가 없음"}`}>
                      {value !== undefined && <span className={`${styles.bar} ${styles[cohort]}`} style={{ width: `${value * 100}%` }} />}
                    </div>
                    <strong>{formatted}</strong>
                  </div>;
                })}
              </div>)}
            </div>
            <details open className={styles.disclosure}>
              <summary><DisclosureIcon />상세 수치 보기</summary>
              <div className={styles.tableRegion} role="region" aria-label="추천 성능 상세 수치 표, 가로 스크롤 가능" tabIndex={0}>
                <table><caption className={shared.srOnly}>모델별 기존·신규 평가 수치. 기준 모델 대비 차이는 NDCG의 절대 점수 차이와 Hit Rate의 퍼센트포인트 차이입니다.</caption>
                  <thead><tr><th scope="col">추천 방식</th><th scope="col">입력 범위</th><th scope="col">NDCG@10</th><th scope="col">Hit Rate@10</th><th scope="col">표본</th><th scope="col">{baseline?.label ?? "기준 모델"} 대비 NDCG</th><th scope="col">{baseline?.label ?? "기준 모델"} 대비 Hit Rate</th></tr></thead>
                  <tbody>{data.models.flatMap(model => shownCohorts.map(cohort => {
                    const score = model.scores.find(item => item.audience === cohort);
                    return <tr key={`${model.modelId}:${cohort}`} data-reference={model.modelId === data.currentModelId}>
                      <th scope="row">{model.label}</th><td>{cohortLabel[cohort]}</td><td>{score ? ndcg(score.ndcgAt10) : "—"}</td>
                      <td>{score ? hitRate(score.hitRateAt10) : "—"}</td><td>{score ? integer(score.sampleSize) : "—"}</td><td>{score ? delta(score.ndcgDeltaVsBaseline) : "—"}</td><td>{score ? percentagePointDelta(score.hitRateDeltaVsBaseline) : "—"}</td>
                    </tr>;
                  }))}</tbody>
                </table>
              </div>
            </details>
          </StatisticsPanel>
          <StatisticsPanel title="평가 조건" className={styles.conditions} availability="available">
            <dl>{data.conditions && [
              ["평가 데이터", data.conditions.evaluationData], ["입력 조건", data.conditions.inputConditions],
              ["집계 단위", "시리즈 · 정답 1개 · 상위 10개"], ["공통 규칙", data.conditions.commonRules],
              ["정답 기간", data.conditions.targetPeriod], ["정답 기준", data.conditions.groundTruth],
              ["후보·추천 수", data.conditions.candidatePolicy], ["기록 출처", data.conditions.recordSource],
              ["평가 ID", data.evaluationId],
            ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
            {data.cohorts.map(cohort => <p key={cohort.audience} className={styles.cohortDefinition}><strong>{cohortLabel[cohort.audience]}</strong> · {cohort.definition} · {integer(cohort.sampleSize)}건</p>)}
            <details open className={styles.disclosure}>
              <summary><DisclosureIcon />NDCG@10은 어떻게 읽나요?</summary>
              <p>정답 시리즈가 추천 상위 10개에서 앞에 나올수록 높은 점수를 줍니다. 정답 1개 기준으로 1위는 1.0, 2위는 약 0.63, 10위는 약 0.29, 10위 밖이면 0입니다.</p>
              <p>Hit Rate@10은 상위 10개 안에 정답이 포함된 표본의 비율입니다. 두 지표는 각 입력 범위의 전체 표본으로 계산합니다.</p>
            </details>
          </StatisticsPanel>
        </div>
      </>}
    </>}
  </div>;
}

function ScoreSummary({ title, cohorts, scores, format, highlight = false }: {
  title: string; cohorts: Cohort[]; scores: Score[]; format: (score: Score) => string; highlight?: boolean;
}) {
  return <section className={`${styles.scoreCard} ${highlight ? styles.highlight : ""}`} aria-label={title} data-statistics="available">
    <h2>{title}</h2><dl>{cohorts.map(cohort => {
      const score = scores.find(item => item.audience === cohort);
      return <div key={cohort}><dt>{cohortLabel[cohort]}</dt><dd>{score ? format(score) : <span aria-label={`${cohortLabel[cohort]} 평가 없음`}>—</span>}</dd></div>;
    })}</dl>
  </section>;
}

function DisclosureIcon() {
  return <><img className={styles.collapsedIcon} src="/admin/stat-collapsed.svg" alt="" /><img className={styles.expandedIcon} src="/admin/stat-expanded.svg" alt="" /></>;
}
