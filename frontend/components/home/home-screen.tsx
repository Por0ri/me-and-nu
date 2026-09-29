"use client";

import { consumerRoutes } from "@/lib/consumer-routes";

import Link from "next/link";
import Image from "next/image";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";

import { ContentCard } from "@/components/home/content-card";
import { HomeAIPanel } from "@/components/home/home-ai-panel";
import { useConsumerFlow } from "@/components/providers/consumer-flow-provider";
import { getHomeContents } from "@/lib/consumer-api/home";
import { isConsumerApiMode } from "@/lib/consumer-api/mode";
import { isLocalDemoLoginAvailable } from "@/lib/consumer-api/local-demo";
import type { HomeData } from "@/types/home";

type HomeViewMode = "list" | "card";

const HOME_VIEW_MODE_STORAGE_KEY = "menu:home-view-mode";

const HOME_ERROR_MESSAGE =
  "홈 콘텐츠를 불러오지 못했습니다. 다시 시도해 주세요.";

// Header presentation only; Topic data and navigation still use the original ID/name.
const HEADER_TOPIC_LABELS = new Map([
  ["topic-music", "music"],
  ["topic-movie", "movie"],
  ["topic-anime", "animation"],
]);

async function requestHomeData(topicId: string): Promise<HomeData> {
  const result = await getHomeContents({ topicId });

  if (result.selectedTopicId !== topicId) {
    throw new Error("Home topic does not match the requested topic.");
  }

  return result;
}

export function HomeScreen({ topicId }: { topicId: string | null }) {
  const searchParams = useSearchParams();
  const { flowState, isRestoring, restoreError, refreshTopics } = useConsumerFlow();
  const initialHomeTopicId =
    flowState?.availableTopicIds.includes(flowState.initialHomeTopicId) === true
      ? flowState.initialHomeTopicId
      : null;
  // Back can restore an older server prop; prefer the current URL, even when absent.
  const queryTopicIds = searchParams?.getAll("topicId");
  const queryTopicId = queryTopicIds
    ? queryTopicIds.length > 1
      ? ""
      : queryTopicIds[0] ?? null
    : topicId;
  const requestedTopicId = queryTopicId ?? initialHomeTopicId;
  const initialSelectedTopicId =
    requestedTopicId && flowState?.availableTopicIds.includes(requestedTopicId)
      ? requestedTopicId
      : null;
  const [selectedTopicId, setSelectedTopicId] = useState<string | null>(
    initialSelectedTopicId,
  );
  const [viewMode, setViewMode] = useState<HomeViewMode>("list");
  const [isTopicMenuOpen, setIsTopicMenuOpen] = useState(false);
  const [canRestartDemo, setCanRestartDemo] = useState(false);
  const topicMenuId = useId();
  const topicMenuRef = useRef<HTMLDivElement>(null);
  const topicMenuButtonRef = useRef<HTMLButtonElement>(null);
  const [homeData, setHomeData] = useState<HomeData | null>(null);
  const [isLoading, setIsLoading] = useState(initialSelectedTopicId !== null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCanRestartDemo(isConsumerApiMode && isLocalDemoLoginAvailable());
  }, []);

  useEffect(() => {
    if (isRestoring || !initialSelectedTopicId) return;
    if (selectedTopicId === initialSelectedTopicId) return;
    setIsLoading(true);
    setError(null);
    setSelectedTopicId(initialSelectedTopicId);
  }, [flowState, initialSelectedTopicId, isRestoring, selectedTopicId]);

  useEffect(() => {
    if (!isTopicMenuOpen) {
      return;
    }

    function closeOnOutsideClick(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !topicMenuRef.current?.contains(event.target)
      ) {
        setIsTopicMenuOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsTopicMenuOpen(false);
        topicMenuButtonRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isTopicMenuOpen]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      try {
        const storedViewMode = window.localStorage.getItem(HOME_VIEW_MODE_STORAGE_KEY);

        if (storedViewMode === "list" || storedViewMode === "card") {
          setViewMode(storedViewMode);
        }
      } catch {
        // Unavailable storage leaves the initial List view intact.
      }
    });

    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (!selectedTopicId) {
      return;
    }

    const topicId = selectedTopicId;
    let isCancelled = false;

    async function loadHome() {
      try {
        const result = await requestHomeData(topicId);

        if (!isCancelled) {
          setHomeData(result);
        }
      } catch {
        if (!isCancelled) {
          setError(HOME_ERROR_MESSAGE);
        }
      } finally {
        if (!isCancelled) {
          setIsLoading(false);
        }
      }
    }

    void loadHome();

    return () => {
      isCancelled = true;
    };
  }, [selectedTopicId]);

  function toggleViewMode() {
    const nextViewMode = viewMode === "list" ? "card" : "list";
    setViewMode(nextViewMode);

    try {
      window.localStorage.setItem(HOME_VIEW_MODE_STORAGE_KEY, nextViewMode);
    } catch {
      // Keep the local toggle usable when the preference cannot be persisted.
    }
  }

  function selectTopic(topicId: string) {
    const url = new URL(window.location.href);

    if (url.searchParams.get("topicId") !== topicId) {
      url.searchParams.set("topicId", topicId);
      // Sync the URL without route navigation remounting the query-keyed HomeScreen.
      window.history.replaceState(null, "", url);
    }

    if (topicId === selectedTopicId) {
      return;
    }

    setIsLoading(true);
    setError(null);
    setSelectedTopicId(topicId);
  }

  async function retryHome() {
    if (!selectedTopicId) {
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const result = await requestHomeData(selectedTopicId);
      setHomeData(result);
    } catch {
      setError(HOME_ERROR_MESSAGE);
    } finally {
      setIsLoading(false);
    }
  }

  if (isConsumerApiMode && isRestoring) return null;

  if (isConsumerApiMode && restoreError) {
    return (
      <main className="ui-version-a ui-home">
        <section className="va-shell va-recovery space-y-4 text-center">
          <h1 className="text-2xl font-semibold">홈</h1>
          <p role="alert" className="va-muted text-sm leading-6">{restoreError}</p>
          <button
            type="button"
            onClick={() => void refreshTopics().catch(() => undefined)}
            className="va-primary inline-flex w-full items-center justify-center"
          >
            다시 시도
          </button>
        </section>
      </main>
    );
  }

  if (!flowState || !initialHomeTopicId) {
    return (
      <main className="ui-version-a ui-home">
        <section className="va-shell va-recovery space-y-4 text-center">
          <h1 className="text-2xl font-semibold">홈</h1>
          <p role="status" className="va-muted text-sm leading-6">
            현재 연결된 분야 정보가 없습니다. 분야를 선택해 주세요.
          </p>
          <Link
            href={isConsumerApiMode ? consumerRoutes.addTopic : consumerRoutes.onboarding}
            className="va-primary inline-flex w-full items-center justify-center"
          >
            {isConsumerApiMode ? "분야 추가로 이동" : "온보딩으로 이동"}
          </Link>
        </section>
      </main>
    );
  }

  if (!initialSelectedTopicId || !selectedTopicId) {
    return (
      <main className="ui-version-a ui-home">
        <section className="va-shell va-recovery space-y-4 text-center">
          <h1 className="text-2xl font-semibold">홈</h1>
          <p role="status" className="va-muted text-sm leading-6">
            현재 연결된 분야가 아닙니다. 홈에서 다시 선택해 주세요.
          </p>
          <Link href={consumerRoutes.home} className="text-sm font-medium underline underline-offset-4">
            홈으로 돌아가기
          </Link>
        </section>
      </main>
    );
  }

  const allTopics = homeData?.topics ?? [];
  const visibleTopics = allTopics.filter((topic) =>
    flowState.availableTopicIds.includes(topic.id),
  );
  const currentTopic = visibleTopics.find(
    (topic) => topic.id === selectedTopicId,
  );
  const isCurrentTopicData = homeData?.selectedTopicId === selectedTopicId;
  const headerTopicLabel = currentTopic
    ? isConsumerApiMode
      ? currentTopic.code?.toLowerCase() ?? currentTopic.name
      : HEADER_TOPIC_LABELS.get(currentTopic.id) ?? "nu"
    : "nu";

  return (
    <main className="ui-version-a ui-home">
      <div className="va-shell va-home">
        <header className="va-home-header">
          <div className="min-w-0">
            <p className="va-home-brand">
              me;<span className="va-accent">{headerTopicLabel}</span>
            </p>
            <h1 className="sr-only">홈</h1>
            {currentTopic ? (
              <p className="sr-only">현재 분야: {currentTopic.name}</p>
            ) : null}
          </div>
          <div className="va-header-icons">
            <div ref={topicMenuRef} className="va-topic-switch">
              <button
                ref={topicMenuButtonRef}
                type="button"
                onClick={() => setIsTopicMenuOpen((isOpen) => !isOpen)}
                aria-label="분야 전환 메뉴"
                aria-expanded={isTopicMenuOpen}
                aria-controls={isTopicMenuOpen ? topicMenuId : undefined}
                className="va-topic-switch-trigger"
              >
                <Image src="/ui-version-a/swap-horiz.svg" alt="" width={20} height={16} />
              </button>
              {isTopicMenuOpen ? (
                <div id={topicMenuId} className="va-topic-popover">
                  {allTopics.length > 0 ? (
                    <nav aria-label="분야 선택" className="va-topic-menu-options">
                      {allTopics.map((topic) => {
                        const isActive = flowState.availableTopicIds.includes(topic.id);
                        const isSelected = topic.id === selectedTopicId;

                        if (!isActive) {
                          return (
                            <Link
                              key={topic.id}
                              href={{
                                pathname: consumerRoutes.addTopic,
                                query: {
                                  returnTopicId: selectedTopicId,
                                  targetTopicId: topic.id,
                                },
                              }}
                              onClick={() => setIsTopicMenuOpen(false)}
                              aria-label={`${topic.name} 분야 추가`}
                              className="va-topic-menu-option"
                            >
                              {topic.name}
                              <span className="va-muted text-xs">+ 추가</span>
                            </Link>
                          );
                        }

                        return (
                          <button
                            key={topic.id}
                            type="button"
                            aria-pressed={isSelected}
                            disabled={isLoading}
                            onClick={() => {
                              selectTopic(topic.id);
                              setIsTopicMenuOpen(false);
                              topicMenuButtonRef.current?.focus();
                            }}
                            className="va-topic-menu-option"
                          >
                            {topic.name}
                            {isSelected ? <span className="text-xs">현재 분야</span> : null}
                          </button>
                        );
                      })}
                    </nav>
                  ) : null}
                  <Link
                    href={{
                      pathname: consumerRoutes.addTopic,
                      query: { returnTopicId: selectedTopicId },
                    }}
                    onClick={() => setIsTopicMenuOpen(false)}
                    className="va-topic-menu-option"
                  >
                    분야 추가
                  </Link>
                  {canRestartDemo ? (
                    <Link
                      href={consumerRoutes.login}
                      onClick={() => setIsTopicMenuOpen(false)}
                      className="va-topic-menu-option"
                    >
                      처음부터 시연하기
                    </Link>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => {
                      toggleViewMode();
                      setIsTopicMenuOpen(false);
                      topicMenuButtonRef.current?.focus();
                    }}
                    className="va-topic-menu-view"
                  >
                    {viewMode === "list" ? "카드 보기로 전환" : "목록 보기로 전환"}
                  </button>
                </div>
              ) : null}
            </div>
            <span role="img" aria-label="알림 아이콘 (현재 기능 없음)">
              <Image src="/ui-home/notifications.svg" alt="" width={24} height={24} />
            </span>
          </div>
        </header>

        {error ? (
          <section className="va-message space-y-4 text-center">
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
            <button
              type="button"
              onClick={() => void retryHome()}
              className="va-secondary"
            >
              다시 시도
            </button>
          </section>
        ) : isLoading || !isCurrentTopicData ? (
          <p role="status" className="va-message va-muted text-center text-sm">
            홈 콘텐츠를 불러오는 중입니다.
          </p>
        ) : visibleTopics.length === 0 ? (
          <section className="va-message space-y-4 text-center">
            <p role="status" className="va-muted text-sm">
              현재 생성된 분야 정보를 찾을 수 없습니다.
            </p>
            <Link
              href={consumerRoutes.onboarding}
              className="va-secondary inline-flex"
            >
              온보딩으로 이동
            </Link>
          </section>
        ) : homeData.sections.length === 0 ? (
          <p role="status" className="va-message va-muted text-center text-sm">
            현재 분야에 표시할 콘텐츠가 없습니다.
          </p>
        ) : (
          <div className={viewMode === "card" ? "va-home-feed va-home-feed-card" : "va-home-feed"}>
            {homeData.sections.map((section) => (
              <section key={section.id}>
                <h2
                  className={section.contents.length > 0 ? "sr-only" : "va-empty-section-title"}
                >
                  {section.title}
                </h2>
                {section.contents.length > 0 ? (
                  <div className="va-home-section-contents">
                    {section.contents.map((content) => (
                      <ContentCard
                        key={content.id}
                        content={content}
                        topicId={selectedTopicId}
                        sectionTitle={section.title}
                        viewMode={viewMode}
                      />
                    ))}
                  </div>
                ) : (
                  <p role="status" className="va-message va-muted text-sm">
                    이 Section에 표시할 콘텐츠가 없습니다.
                  </p>
                )}
              </section>
            ))}
          </div>
        )}

        {!error && !isLoading && isCurrentTopicData && currentTopic ? (
          <HomeAIPanel
            key={selectedTopicId}
            topic={currentTopic}
            availableTopicIds={flowState.availableTopicIds}
          />
        ) : null}

        <nav aria-label="홈 메뉴" className="va-bottom-nav">
          <span aria-current="page" className="va-nav-current">
            <span className="va-nav-indicator">HOME</span>
          </span>
          <Link
            href={{ pathname: consumerRoutes.saved, query: { topicId: selectedTopicId } }}
            aria-label="저장 목록으로 이동"
          >
            SAVE
          </Link>
        </nav>
      </div>
    </main>
  );
}
