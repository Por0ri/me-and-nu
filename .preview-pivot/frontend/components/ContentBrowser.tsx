"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, getFeed, getMyTopics, getSubtopics, type FeedCard, type MyTopic, type Subtopic } from "@/lib/api";
import { needsReview, productionLabel, publishedDate } from "@/lib/content-presentation";

type Props = {
  fixedTopicId?: number;
  subtopicId?: number;
};

function errorText(error: unknown): string {
  if (error instanceof ApiError && (error.status === 401 || error.code === "ONBOARDING_REQUIRED")) {
    return "콘텐츠를 보려면 로그인과 온보딩을 완료해 주세요. 아래 개발용 API 점검에서 진행할 수 있습니다.";
  }
  return error instanceof Error ? error.message : "콘텐츠를 불러오지 못했습니다.";
}

export default function ContentBrowser({ fixedTopicId, subtopicId }: Props) {
  const [topics, setTopics] = useState<MyTopic[]>([]);
  const [homeTopicId, setHomeTopicId] = useState<number | null>(null);
  const [topicsLoading, setTopicsLoading] = useState(true);
  const [topicsError, setTopicsError] = useState<string | null>(null);
  const [subtopics, setSubtopics] = useState<Subtopic[]>([]);
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [feedLoading, setFeedLoading] = useState(false);
  const [moreLoading, setMoreLoading] = useState(false);
  const [feedError, setFeedError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  const selectedTopicId = fixedTopicId ?? (
    topics.some((topic) => topic.id === homeTopicId) ? homeTopicId : topics[0]?.id ?? null
  );
  const requestScope = `${selectedTopicId ?? "none"}:${subtopicId ?? "all"}:${retry}`;
  const scopeRef = useRef(requestScope);
  scopeRef.current = requestScope;

  useEffect(() => {
    let active = true;
    setTopicsLoading(true);
    setTopicsError(null);
    getMyTopics().then((result) => {
      if (active) setTopics(result.topics.filter((topic) => topic.status === "active"));
    }).catch((error: unknown) => {
      if (active) setTopicsError(errorText(error));
    }).finally(() => {
      if (active) setTopicsLoading(false);
    });
    return () => { active = false; };
  }, [retry]);

  useEffect(() => {
    let active = true;
    setSubtopics([]);
    if (selectedTopicId !== null) {
      getSubtopics(selectedTopicId, { limit: 100 }).then((result) => {
        if (active) setSubtopics(result.items);
      }).catch(() => {
        // The feed request reports the actionable error for this Topic.
      });
    }
    return () => { active = false; };
  }, [selectedTopicId, retry]);

  useEffect(() => {
    let active = true;
    setCards([]);
    setNextCursor(null);
    setFeedError(null);
    setMoreLoading(false);
    if (selectedTopicId === null) {
      setFeedLoading(false);
      return () => { active = false; };
    }
    setFeedLoading(true);
    getFeed(selectedTopicId, { subtopicId }).then((result) => {
      if (!active) return;
      setCards(result.sections.flatMap((section) => section.contents));
      setNextCursor(result.nextCursor);
    }).catch((error: unknown) => {
      if (active) setFeedError(errorText(error));
    }).finally(() => {
      if (active) setFeedLoading(false);
    });
    return () => { active = false; };
  }, [selectedTopicId, subtopicId, retry]);

  async function loadMore() {
    if (selectedTopicId === null || !nextCursor || moreLoading) return;
    const scope = requestScope;
    setMoreLoading(true);
    try {
      const result = await getFeed(selectedTopicId, { subtopicId, cursor: nextCursor });
      if (scopeRef.current !== scope) return;
      setCards((current) => [...current, ...result.sections.flatMap((section) => section.contents)]);
      setNextCursor(result.nextCursor);
    } catch (error) {
      if (scopeRef.current === scope) setFeedError(errorText(error));
    } finally {
      if (scopeRef.current === scope) setMoreLoading(false);
    }
  }

  const selectedTopic = topics.find((topic) => topic.id === selectedTopicId);
  const selectedSubtopic = subtopics.find((item) => item.subtopicId === subtopicId);
  const title = subtopicId !== undefined
    ? selectedSubtopic?.name ?? "세부 토픽"
    : fixedTopicId !== undefined ? selectedTopic?.name ?? "토픽 콘텐츠" : "공개 콘텐츠";

  return (
    <section className="space-y-6" aria-label="공개 콘텐츠 피드">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs font-bold tracking-[0.2em] text-teal-300">ME;NU FEED</p>
          <h2 className="mt-2 text-3xl font-bold tracking-tight">{title}</h2>
          <p className="mt-2 text-sm leading-6 text-slate-400">선택한 관심사의 공개 글을 최신순으로 볼 수 있습니다.</p>
        </div>
        <button type="button" onClick={() => setRetry((value) => value + 1)} className="rounded-xl border border-white/15 px-4 py-2 text-sm text-slate-200 hover:border-teal-300/60 hover:bg-teal-300/10">
          새로고침
        </button>
      </div>

      <nav aria-label="토픽" className="flex flex-wrap gap-2">
        <Link href="/" className={`rounded-full border px-4 py-2 text-sm ${fixedTopicId === undefined ? "border-teal-300/60 bg-teal-300/15 text-teal-100" : "border-white/15 text-slate-300 hover:bg-white/5"}`}>홈</Link>
        {topics.map((topic) => (
          <Link key={topic.id} href={`/topics/${topic.id}`} className={`rounded-full border px-4 py-2 text-sm ${fixedTopicId === topic.id && subtopicId === undefined ? "border-teal-300/60 bg-teal-300/15 text-teal-100" : "border-white/15 text-slate-300 hover:bg-white/5"}`}>
            {topic.name}
          </Link>
        ))}
      </nav>

      {fixedTopicId === undefined && topics.length > 1 && (
        <label className="block max-w-xs text-sm text-slate-300">홈 피드 Topic
          <select className="mt-2 w-full rounded-xl border border-white/15 bg-slate-950 px-3 py-2 text-sm" value={selectedTopicId ?? ""} onChange={(event) => setHomeTopicId(Number(event.target.value))}>
            {topics.map((topic) => <option key={topic.id} value={topic.id}>{topic.name}</option>)}
          </select>
        </label>
      )}

      {selectedTopicId !== null && (
        <nav aria-label="세부 토픽" className="flex flex-wrap gap-2">
          <Link href={`/topics/${selectedTopicId}`} className={`rounded-lg px-3 py-2 text-sm ${fixedTopicId === selectedTopicId && subtopicId === undefined ? "bg-blue-300/15 text-blue-100" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}>전체</Link>
          {subtopics.map((item) => (
            <Link key={item.subtopicId} href={`/topics/${selectedTopicId}/subtopics/${item.subtopicId}`} className={`rounded-lg px-3 py-2 text-sm ${subtopicId === item.subtopicId ? "bg-blue-300/15 text-blue-100" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}>
              {item.name}
            </Link>
          ))}
        </nav>
      )}

      {topicsLoading && <p className="rounded-2xl border border-white/10 p-6 text-slate-400">관심사를 불러오는 중입니다…</p>}
      {topicsError && <p role="alert" className="rounded-2xl border border-amber-300/20 bg-amber-300/5 p-6 text-sm text-amber-100">{topicsError} <Link href="/#api-test" className="underline">API 점검으로 이동</Link></p>}
      {!topicsLoading && !topicsError && topics.length === 0 && <p className="rounded-2xl border border-white/10 p-6 text-slate-400">활성 관심사가 없습니다. 아래 API 점검에서 온보딩하거나 Topic을 추가해 주세요.</p>}
      {feedLoading && <p className="rounded-2xl border border-white/10 p-6 text-slate-400">글을 불러오는 중입니다…</p>}
      {feedError && <p role="alert" className="rounded-2xl border border-rose-300/20 bg-rose-300/5 p-6 text-sm text-rose-100">{feedError}</p>}
      {!feedLoading && !feedError && selectedTopicId !== null && cards.length === 0 && <p className="rounded-2xl border border-white/10 p-6 text-slate-400">이 토픽에 공개된 글이 없습니다.</p>}

      {cards.length > 0 && selectedTopicId !== null && (
        <div className="grid gap-4 md:grid-cols-2">
          {cards.map((card) => (
            <article key={card.id} className="rounded-2xl border border-white/10 bg-white/[0.045] p-5 hover:border-teal-300/30">
              <Link href={`/contents/${card.id}?topicId=${selectedTopicId}${subtopicId === undefined ? "" : `&subtopicId=${subtopicId}`}`} className="block h-full">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full bg-teal-300/10 px-2.5 py-1 font-semibold text-teal-200">{productionLabel(card.productionType)}</span>
                  {needsReview(card.notices, card.summary) && <span className="rounded-full bg-amber-300/10 px-2.5 py-1 font-semibold text-amber-200">검토 필요</span>}
                  {card.notices.some((notice) => notice.code === "DEV_PREVIEW") && <span className="rounded-full bg-orange-300/10 px-2.5 py-1 font-semibold text-orange-200">개발용 미리보기</span>}
                  {publishedDate(card.publishedAt) && <span className="text-slate-500">{publishedDate(card.publishedAt)}</span>}
                </div>
                <h3 className="mt-4 text-lg font-bold leading-7 text-slate-100">{card.title}</h3>
                {card.summary && <p className="mt-2 text-sm leading-6 text-slate-400">{card.summary}</p>}
                <p className="mt-4 text-xs text-slate-500">{card.sourceName} · 상세 보기 →</p>
              </Link>
            </article>
          ))}
        </div>
      )}

      {nextCursor && <button type="button" disabled={moreLoading} onClick={() => void loadMore()} className="rounded-xl border border-white/15 px-5 py-2.5 text-sm font-semibold hover:border-teal-300/60 disabled:opacity-50">{moreLoading ? "불러오는 중…" : "글 더 보기"}</button>}
    </section>
  );
}
