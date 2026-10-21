"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { topicLabelFor, useTopicOptions } from "@/components/my/use-topic-options";
import { BrandHeader } from "@/components/navigation/brand-header";
import { ConsumerBottomNav } from "@/components/navigation/consumer-bottom-nav";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { useConsumerSession } from "@/components/providers/consumer-session-provider";
import { getProfile, logout, type Profile } from "@/lib/api";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { consumerRoutes } from "@/lib/consumer-routes";

const COMING_SOON = "준비 중인 기능이에요.";

function Chevron() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M9 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="va-my-row"
    >
      <span>{label}</span>
      <span className="va-toggle" aria-hidden="true">
        <span className="va-toggle-thumb" />
      </span>
    </button>
  );
}

export function MyPageScreen({ topicId }: { topicId: string | null }) {
  const { flowState, isRestoring } = useConsumerFlow();
  const { refreshSession } = useConsumerSession();
  const { topics } = useTopicOptions();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // 알림 설정 API가 아직 없어 화면 안에서만 켜고 끈다.
  const [notificationsOn, setNotificationsOn] = useState(true);
  const [nightNotificationsOn, setNightNotificationsOn] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);

  const currentTopicId = topicId ?? flowState?.initialHomeTopicId ?? null;

  useEffect(() => {
    if (!isConsumerApiMode) return;
    let isCancelled = false;
    getProfile()
      .then((result) => {
        if (!isCancelled) setProfile(result);
      })
      .catch(() => undefined);
    return () => {
      isCancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 2000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function handleLogout() {
    if (!isConsumerApiMode) {
      setNotice(COMING_SOON);
      return;
    }
    setIsLoggingOut(true);
    try {
      await logout();
      // 세션이 사라지면 로그인 화면으로 자동 이동한다.
      await refreshSession();
    } catch {
      setNotice("로그아웃하지 못했습니다. 다시 시도해 주세요.");
      setIsLoggingOut(false);
    }
  }

  if (isConsumerApiMode && isRestoring) return null;

  const withTopic = (pathname: string) =>
    currentTopicId ? { pathname, query: { topicId: currentTopicId } } : pathname;
  const comingSoon = () => setNotice(COMING_SOON);

  return (
    <main className="ui-version-a ui-home">
      <div className="va-shell va-home va-tab-page">
        <BrandHeader topicLabel={topicLabelFor(topics, currentTopicId)} title="MY PAGE" />

        <section aria-labelledby="my-profile-title" className="va-my-section">
          <h2 id="my-profile-title" className="va-my-section-title">내 프로필</h2>
          <button type="button" onClick={comingSoon} className="va-my-row">
            <span>닉네임</span>
            <span className="va-my-row-value"><span className="va-my-row-value-text">{profile?.nickname}</span><Chevron /></span>
          </button>
          <button type="button" onClick={comingSoon} className="va-my-row">
            <span>이메일</span>
            <span className="va-my-row-value"><span className="va-my-row-value-text">{profile?.email}</span><Chevron /></span>
          </button>
          <button type="button" onClick={comingSoon} className="va-my-row">
            <span>내 활동</span>
            <Chevron />
          </button>
          <Link href={withTopic(consumerRoutes.mySubscriptions)} className="va-my-row">
            <span>구독 관리</span>
            <Chevron />
          </Link>
          <Link href={withTopic(consumerRoutes.myTopics)} className="va-my-row">
            <span>관심분야 관리</span>
            <Chevron />
          </Link>
          <Toggle label="알림 설정" checked={notificationsOn} onChange={setNotificationsOn} />
          <Toggle
            label="21시 이후 알림허용"
            checked={nightNotificationsOn}
            onChange={setNightNotificationsOn}
          />
        </section>

        <section aria-labelledby="my-guide-title" className="va-my-section">
          <h2 id="my-guide-title" className="va-my-section-title">이용 안내</h2>
          {["문의 및 신고", "이용약관", "개인정보처리방침"].map((label) => (
            <button key={label} type="button" onClick={comingSoon} className="va-my-row">
              <span>{label}</span>
              <Chevron />
            </button>
          ))}
        </section>

        <section className="va-my-section">
          <button
            type="button"
            disabled={isLoggingOut}
            onClick={() => void handleLogout()}
            className="va-my-row va-my-row-muted"
          >
            {isLoggingOut ? "로그아웃 중…" : "로그아웃"}
          </button>
          <button type="button" onClick={comingSoon} className="va-my-row va-my-row-muted">
            회원탈퇴
          </button>
        </section>

        <p role="status" className={notice ? "va-toast" : "sr-only"}>{notice}</p>
        <ConsumerBottomNav current="my" topicId={currentTopicId} />
      </div>
    </main>
  );
}
