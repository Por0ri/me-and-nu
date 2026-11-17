"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "@/lib/api";
import { isActiveSession, isPendingConsumer } from "@/lib/pivot/services";
import { buildSelectionDraft } from "@/lib/pivot/onboarding";
import { checkNickname, NICKNAME_TAKEN_MESSAGE, serviceOnboardingService } from "@/lib/pivot/onboarding-service";
import { warmUpFeed } from "@/lib/pivot/feed-warmup";
import { FeedLoadingScreen } from "./feed-loading-screen";
import { usePivotSession } from "./pivot-session";
import { OnboardingFlow } from "./onboarding-screens";
import { OnboardingProfileScreen } from "./onboarding-profile";
import { PivotFrame, PivotBrand } from "./pivot-ui";
import styles from "./pivot.module.css";

export function OnboardingRoute() {
  const router = useRouter();
  const { state, refresh, draft, onboarding, dispatchOnboarding, profile, rememberProfile, completeOnboarding } = usePivotSession();
  const [profileReady, setProfileReady] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const [recovery, setRecovery] = useState<"consent" | "session" | null>(null);
  const lock = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function submit() {
    if (lock.current || !draft || !isPendingConsumer(state.session)) return;
    lock.current = true; setSubmitting(true); setError(""); setRecovery(null);
    const since = Date.now();
    try {
      const result = await serviceOnboardingService.submitSelection(buildSelectionDraft(onboarding), draft, profile);
      // The first feed waits for the first recommendation round: the loading screen covers it. The shared session
      // turns active only afterwards, since pivot-session sends an active session on this route straight to the feed.
      if (mounted.current) setPreparing(true);
      await warmUpFeed(onboarding.topicId, since);
      // Browser Back can leave this route while the request commits. Keep the shared
      // session current, but never navigate an abandoned route after completion.
      completeOnboarding(result.session);
      if (mounted.current) router.replace("/pivot/home");
    } catch (reason) {
      if (mounted.current) setPreparing(false);
      const code = reason instanceof ApiError ? reason.code : "";
      if (reason instanceof ApiError && reason.status === 401) {
        refresh(); if (mounted.current) router.replace("/pivot/login"); return;
      }
      if (!mounted.current) return;
      if (code === "NICKNAME_TAKEN") {
        // Someone finished with this nickname after the check: back to the nickname screen with the same red line.
        setError(NICKNAME_TAKEN_MESSAGE); setProfileReady(false);
      } else if (code === "POLICY_VERSION_CONFLICT" || code === "REQUIRED_CONSENT_MISSING") {
        setError("약관을 다시 확인해야 해요. 최신 약관을 확인한 뒤 가입을 완료해주세요."); setRecovery("consent");
      } else if (code === "STATE_CONFLICT" || code === "ONBOARDING_ALREADY_COMPLETED") {
        setError("로그인 상태를 다시 확인해야 해요."); setRecovery("session");
      } else if (code === "CSRF_INVALID") {
        setError("로그인 확인 정보가 갱신됐어요. 가입 완료 버튼을 다시 눌러주세요.");
      } else if (code === "TOPIC_NOT_FOUND") {
        setError("선택한 관심 분야를 확인하지 못했어요. 잠시 후 다시 시도해주세요.");
      } else if (code === "VALIDATION_ERROR") {
        if (reason instanceof ApiError && reason.fields?.some(field => field.field === "nickname")) {
          setError("닉네임을 다시 확인해주세요."); setProfileReady(false);
        } else {
          setError("선택한 작품과 장르·소재를 다시 확인해주세요.");
          dispatchOnboarding({ type: "back", from: "result" });
          dispatchOnboarding({ type: "back", from: "q3" });
        }
      } else {
        setError("가입 완료를 확인하지 못했어요. 입력한 내용은 유지돼요. 잠시 후 다시 시도해주세요.");
      }
    } finally {
      lock.current = false;
      if (mounted.current) setSubmitting(false);
    }
  }
  if (preparing) return <FeedLoadingScreen />;
  if (!isPendingConsumer(state.session) || !draft) return <PivotFrame>
    <PivotBrand screen="consent" className={styles.consentBrand} />
    <section className={styles.gate}><h1>온보딩 확인</h1>
      <p role="status">{state.status === "loading" ? "로그인 상태를 확인하고 있어요." : isActiveSession(state.session) ? "가입이 완료됐어요. 홈으로 이동하고 있어요." : !isPendingConsumer(state.session)
        ? "실제 로그인과 약관 확인이 필요해요. 가입이나 서버 저장은 진행되지 않았어요."
        : "약관 내용을 확인하고 필수 약관에 동의해 주세요."}</p>
      <Link href="/pivot/consent">약관 동의 확인하기</Link><Link href="/pivot/login">로그인 화면으로</Link>
    </section>
  </PivotFrame>;
  if (!profileReady) return <OnboardingProfileScreen profile={profile} message={error} checkNickname={checkNickname}
    onChange={(next) => { rememberProfile(next); setError(""); }}
    onContinue={(next) => { rememberProfile(next); setError(""); setProfileReady(true); }} />;
  return <OnboardingFlow state={onboarding} dispatch={dispatchOnboarding} mode="service" onExit={() => setProfileReady(false)}
    submission={{ submitting, error, onSubmit: submit,
      recovery: recovery === "consent" ? { label: "약관 다시 확인하기", onClick: () => router.push("/pivot/consent") }
        : recovery === "session" ? { label: "로그인 상태 다시 확인하기", onClick: refresh } : undefined }} />;
}
