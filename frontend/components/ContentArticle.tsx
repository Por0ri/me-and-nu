"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, getContent, type ContentDetail } from "@/lib/api";
import { needsReview, productionLabel, publishedDate } from "@/lib/content-presentation";

type Props = { contentId: number; topicId: number; subtopicId?: number };

function httpUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export default function ContentArticle({ contentId, topicId, subtopicId }: Props) {
  const [detail, setDetail] = useState<ContentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setDetail(null);
    setError(null);
    getContent(contentId, topicId).then((result) => {
      if (active) setDetail(result);
    }).catch((reason: unknown) => {
      if (!active) return;
      if (reason instanceof ApiError && (reason.status === 401 || reason.code === "ONBOARDING_REQUIRED")) {
        setError("콘텐츠를 보려면 로그인과 온보딩을 완료해 주세요.");
      } else {
        setError(reason instanceof Error ? reason.message : "글을 불러오지 못했습니다.");
      }
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [contentId, topicId, retry]);

  const backHref = subtopicId === undefined
    ? `/topics/${topicId}`
    : `/topics/${topicId}/subtopics/${subtopicId}`;
  const sourceUrl = httpUrl(detail?.sourceUrl ?? null);
  const preview = detail?.notices.some((notice) => notice.code === "DEV_PREVIEW") ?? false;

  return (
    <main className="mx-auto min-h-screen w-full max-w-3xl px-5 py-10 sm:px-8 lg:py-16">
      <nav className="mb-8 flex flex-wrap gap-4 text-sm text-slate-400" aria-label="이동">
        <Link href="/" className="hover:text-teal-200">홈</Link>
        <Link href={backHref} className="hover:text-teal-200">← 토픽 피드</Link>
      </nav>

      {loading && <p className="rounded-2xl border border-white/10 p-6 text-slate-400">글을 불러오는 중입니다…</p>}
      {error && <div role="alert" className="rounded-2xl border border-rose-300/20 bg-rose-300/5 p-6 text-rose-100">
        <p>{error}</p>
        <button type="button" onClick={() => setRetry((value) => value + 1)} className="mt-4 rounded-lg border border-white/20 px-3 py-2 text-sm">다시 시도</button>
      </div>}

      {detail && <article className="rounded-3xl border border-white/10 bg-white/[0.045] p-6 sm:p-9">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full bg-teal-300/10 px-3 py-1.5 font-semibold text-teal-200">{productionLabel(detail.productionType)}</span>
          {needsReview(detail.notices, detail.excerpt) && <span className="rounded-full bg-amber-300/10 px-3 py-1.5 font-semibold text-amber-200">검토 필요</span>}
          {preview && <span className="rounded-full bg-orange-300/10 px-3 py-1.5 font-semibold text-orange-200">개발용 미리보기</span>}
        </div>
        <h1 className="mt-5 text-3xl font-bold leading-tight tracking-tight sm:text-4xl">{detail.title}</h1>
        <p className="mt-3 text-sm text-slate-500">{detail.publisher}{publishedDate(detail.publishedAt) && ` · ${publishedDate(detail.publishedAt)}`}</p>

        {detail.displayMode !== "full_body" && detail.excerpt && <p className="mt-8 rounded-2xl border-l-2 border-teal-300/50 bg-slate-950/40 px-5 py-4 text-sm leading-7 text-slate-300">{detail.excerpt}</p>}
        {detail.displayMode === "full_body" && detail.body && (
          <div className="mt-8 whitespace-pre-wrap break-words text-base leading-8 text-slate-100">{detail.body}</div>
        )}
        {detail.displayMode !== "full_body" && <p className="mt-8 text-sm leading-7 text-slate-400">외부 출처의 글은 발췌문으로 제공됩니다.</p>}
        {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className="mt-8 inline-flex rounded-xl border border-white/15 px-4 py-2.5 text-sm font-semibold text-teal-200 hover:border-teal-300/60 hover:bg-teal-300/10">
          {detail.displayMode === "full_body" ? "참고 출처 보기 ↗" : "원문 보기 ↗"}
        </a>}
      </article>}
    </main>
  );
}
